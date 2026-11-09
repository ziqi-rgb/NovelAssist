"""#T008 设定集导入导出 最小单测。

机制：
- 导出 = 全量设定 bundle（json/md 双格式；MD 内嵌 ```settings-json 机读标记块）
- 导入 = 按名增量 upsert：同名更新、缺失创建、导入后不再存在的删除
- 导入不推进 current_tick（时钟由作者手动控制）
conftest.py 已把工作目录切到临时目录，数据库隔离。
"""
import json

import pytest

from backend import main as M
from backend.database import Base, engine, SessionLocal


def setup_module(module):
    Base.metadata.create_all(bind=engine)


@pytest.fixture(autouse=True)
def _clean():
    """每个用例前清空所有设定表并复位小说。"""
    with SessionLocal() as db:
        db.query(M.TimelineEventRelation).delete()
        db.query(M.TimelineEvent).delete()
        db.query(M.Outline).delete()
        db.query(M.Character).delete()
        db.query(M.Faction).delete()
        db.query(M.Location).delete()
        nv = db.get(M.Novel, 1)
        if nv is None:
            nv = M.Novel(id=1, title="测试小说")
            db.add(nv)
        nv.current_tick = 100  # 非零，验证导入不推进时钟
        db.commit()
    yield


def _seed(db):
    """构造一棵基线：地点(大陆→城池) + 势力 + 人物(挂势力) + 大纲(父→子) + 时间线。"""
    continent = M.Location(novel_id=1, name="太荒大陆", scale_level="大陆", description="基线大陆")
    db.add(continent)
    db.flush()
    city = M.Location(novel_id=1, name="青云城", scale_level="城池", parent_id=continent.id, description="基线城池")
    db.add(city)
    db.flush()
    fac = M.Faction(novel_id=1, name="青云宗", description="基线势力", base_location_id=city.id)
    db.add(fac)
    db.flush()
    char = M.Character(novel_id=1, name="林枫", description="基线人物", status="存活",
                       faction_id=fac.id, faction_role="弟子",
                       attributes={"基础信息": {"姓名": "林枫", "年龄": "18"}})
    db.add(char)
    db.flush()
    root = M.Outline(novel_id=1, title="主线规划", category="剧情大纲", description="基线主线")
    db.add(root)
    db.flush()
    leaf = M.Outline(novel_id=1, title="第一卷", category="剧情大纲", parent_id=root.id, description="基线卷")
    db.add(leaf)
    db.flush()
    te = M.TimelineEvent(novel_id=1, event_type=M.TimelineEventType.MAIN_STORY, time_label="第1日",
                         title="开局", content="基线事件", absolute_tick=1,
                         character_ids=[char.id], related_location_id=city.id)
    db.add(te)
    db.commit()


def test_export_json_bundle():
    with SessionLocal() as db:
        _seed(db)
        resp = M.export_settings_bundle(1, fmt="json", db=db)
        bundle = json.loads(resp.body.decode("utf-8"))
        assert bundle["format"] == "novel-assist-settings"
        names = {lc["name"] for lc in bundle["locations"]}
        assert {"太荒大陆", "青云城"} <= names
        city = next(lc for lc in bundle["locations"] if lc["name"] == "青云城")
        assert city["parent_name"] == "太荒大陆"
        char = next(c for c in bundle["characters"] if c["name"] == "林枫")
        assert char["faction_name"] == "青云宗"
        leaf = next(o for o in bundle["outlines"] if o["title"] == "第一卷")
        assert leaf["parent_name"] == "主线规划"
        tle = next(t for t in bundle["timeline"] if t["time_label"] == "第1日")
        assert tle["related_location_name"] == "青云城"
        assert tle["character_ids"] == [char["id"]]


def test_export_md_pure_readable():
    """MD 导出 = 纯人类可读文档，不再内嵌 JSON 标记块，且宽松解析可往返"""
    with SessionLocal() as db:
        _seed(db)
        resp = M.export_settings_bundle(1, fmt="md", db=db)
        md = resp.body.decode("utf-8")
        assert "```settings-json" not in md
        assert "### 青云城 (城池)" in md
        parsed = M._parse_settings_markdown(md)
        assert {lc["name"] for lc in parsed["locations"]} >= {"太荒大陆", "青云城"}
        # 新字段往返：人物→势力、势力→基地
        c = next(x for x in parsed["characters"] if x["name"] == "林枫")
        assert c["faction_name"] == "青云宗" and c["faction_role"] == "弟子"
        f = next(x for x in parsed["factions"] if x["name"] == "青云宗")
        assert f["base_location_name"] == "青云城"


def test_parse_md_old_fenced_file_compatible():
    """旧版导出文件（带 ```settings-json 标记块）仍可解析：标记块被跳过，正文照常重建"""
    with SessionLocal() as db:
        _seed(db)
        md = M.export_settings_bundle(1, fmt="md", db=db).body.decode("utf-8")
        bundle = json.loads(M.export_settings_bundle(1, fmt="json", db=db).body.decode("utf-8"))
        old_md = "# 旧设定集\n\n```settings-json\n" + json.dumps(bundle, ensure_ascii=False) + "\n```\n\n" + md
        b = M._parse_settings_markdown(old_md)
        assert {lc["name"] for lc in b["locations"]} >= {"太荒大陆", "青云城"}


def test_import_json_create_and_update():
    with SessionLocal() as db:
        _seed(db)
        bundle = json.loads(M.export_settings_bundle(1, fmt="json", db=db).body.decode("utf-8"))
        # 改已有实体 + 新增实体
        bundle["characters"][0]["description"] = "被更新的人物"
        bundle["characters"].append({"id": None, "name": "苏婉", "description": "新角色",
                                     "status": "存活", "attributes": {}})
        bundle["locations"].append({"id": None, "name": "落霞谷", "description": "新地点",
                                    "scale_level": "街区", "parent_name": "太荒大陆", "attributes": {}})
        d = M.import_settings_bundle(1, M.ImportSettingsReq(content=json.dumps(bundle), dry_run=False), db=db)
        assert d["ok"] is True
        p = d["preview"]
        assert p["created"] == 2 and p["updated"] >= 1
        char2 = next(c for c in db.query(M.Character) if c.name == "苏婉")
        assert char2.description == "新角色"
        lu = next(lc for lc in db.query(M.Location) if lc.name == "落霞谷")
        parent = next(lc for lc in db.query(M.Location) if lc.name == "太荒大陆")
        assert lu.parent_id == parent.id
        feng = next(c for c in db.query(M.Character) if c.name == "林枫")
        assert feng.description == "被更新的人物"
        # 导入不推进时钟
        assert db.get(M.Novel, 1).current_tick == 100


def test_import_json_delete_missing():
    with SessionLocal() as db:
        _seed(db)
        bundle = json.loads(M.export_settings_bundle(1, fmt="json", db=db).body.decode("utf-8"))
        # 删除一个地点 + 一个人物
        bundle["locations"] = [lc for lc in bundle["locations"] if lc["name"] != "青云城"]
        bundle["characters"] = [c for c in bundle["characters"] if c["name"] != "林枫"]
        d = M.import_settings_bundle(1, M.ImportSettingsReq(content=json.dumps(bundle), dry_run=False), db=db)
        assert d["ok"] is True
        assert d["preview"]["deleted"] == 2
        names = {lc.name for lc in db.query(M.Location).all()}
        assert "青云城" not in names
        cnames = {c.name for c in db.query(M.Character).all()}
        assert "林枫" not in cnames


def test_import_md_roundtrip():
    """MD 导出文件用 fmt=md 导入 → 相同内容不增不减"""
    with SessionLocal() as db:
        _seed(db)
        md = M.export_settings_bundle(1, fmt="md", db=db).body.decode("utf-8")
        d = M.import_settings_bundle(1, M.ImportSettingsReq(content=md, fmt="md", dry_run=False), db=db)
        assert d["ok"] is True
        # 相同内容导入 → 不增不减
        assert d["preview"]["created"] == 0 and d["preview"]["deleted"] == 0


def test_parse_md_loose_without_fence():
    """手删标记块后仍可从标题/字段行宽松重建。"""
    md = """# 我的设定集

## 🗺️ 地点

### 九州大陆 (大陆)
*广袤世界*
- 地形: 多山

## 👤 人物

### 陈九
别名: 九叔
*老练镖师*
状态: 存活
- 基础信息.姓名: 陈九
- 基础信息.年龄: 45

## 📋 大纲

### 主线 (剧情大纲)
总纲描述
"""
    b = M._parse_settings_markdown(md)
    assert b["novel"]["title"] == "我的设定集"
    loc = b["locations"][0]
    assert loc["name"] == "九州大陆" and loc["scale_level"] == "大陆"
    assert loc["description"] == "广袤世界"
    assert loc["attributes"].get("地形") == "多山"
    c = b["characters"][0]
    assert c["name"] == "陈九" and c["aliases"] == "九叔" and c["status"] == "存活"
    assert c["attributes"]["基础信息"]["年龄"] == "45"
    o = b["outlines"][0]
    assert o["title"] == "主线" and o["category"] == "剧情大纲"


def test_dry_run_no_commit():
    with SessionLocal() as db:
        d = M.import_settings_bundle(
            1, M.ImportSettingsReq(content='{"locations":[{"name":"幻影城","description":"","attributes":{}}]}', dry_run=True),
            db=db)
        assert d["ok"] is True and d["dry_run"] is True
        names = {lc.name for lc in db.query(M.Location).all()}
        assert "幻影城" not in names


def test_invalid_content_rejected():
    with SessionLocal() as db:
        with pytest.raises(M.HTTPException):
            M.import_settings_bundle(1, M.ImportSettingsReq(content="   ", dry_run=False), db=db)
        with pytest.raises(M.HTTPException):
            M.import_settings_bundle(1, M.ImportSettingsReq(content="{not json", fmt="json", dry_run=False), db=db)
        with pytest.raises(M.HTTPException):
            M.import_settings_bundle(1, M.ImportSettingsReq(content="随便一段文字\n没有标题结构", fmt="md", dry_run=False), db=db)

