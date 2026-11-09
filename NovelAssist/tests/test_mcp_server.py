"""#T010 MCP 桥接层 单测。

机制：
- backend/mcp_server.py 模块级执行 _init_schema()（create_all + migration + seed 默认小说）
- 全部工具返回 JSON 字符串；HTTPException → {"error": true, "status": ...}
- conftest.py 已切临时目录，数据库隔离，不触碰真实 .data/novel.db
"""
import asyncio
import json

import pytest

from backend import main as M
from backend import mcp_server as S
from backend.database import SessionLocal


def _j(s: str) -> dict:
    return json.loads(s)


def _err(s: str) -> bool:
    d = _j(s)
    return d.get("error") is True


def _tool_names() -> set:
    return {t.name for t in asyncio.run(S.server.list_tools())}


@pytest.fixture(autouse=True)
def _no_llm(monkeypatch):
    """清空 LLM 凭据，确保测试不产生真实网络调用。"""
    monkeypatch.setattr(M, "LLM_API_KEY", "")
    monkeypatch.setattr(M, "LLM_BASE_URL", "")
    monkeypatch.setattr(M, "LLM_MODEL", "")
    yield


# ════════════════════════════════════════════════════════════════
# 注册完整性
# ════════════════════════════════════════════════════════════════

def test_tool_registry():
    assert len(_tool_names()) == 81


def test_exclusions_not_leaked():
    """§5 排除清单：LLM 生成类端点不得出现在工具中。"""
    leaked = [n for n in _tool_names() if "generate" in n.lower() or "llm" in n.lower()]
    assert leaked == []


# ════════════════════════════════════════════════════════════════
# 4.1 项目 / 时间
# ════════════════════════════════════════════════════════════════

def test_novel_crud():
    assert _j(S.list_novels())
    nv = _j(S.get_novel(novel_id=1))
    assert nv["id"] == 1
    created = _j(S.create_novel(body=M.NovelCreate(title="桥接测试项目")))
    nid = created["id"]
    assert nid and nid != 1
    assert not _err(S.delete_novel(novel_id=nid))
    assert _err(S.get_novel(novel_id=nid))


def test_novel_time():
    req = M.NovelTimeUpdate(current_tick=42, calendar_config={})
    assert not _err(S.set_novel_time(req=req))
    assert _j(S.get_novel_time())["current_tick"] == 42
    assert _err(S.set_novel_time(req=req, novel_id=99999))


# ════════════════════════════════════════════════════════════════
# 4.2 卷 / 章
# ════════════════════════════════════════════════════════════════

def test_volume_and_chapter():
    vid = _j(S.create_volume(body=M.VolumeCreate(title="桥接卷")))["id"]
    assert vid
    cid = _j(S.create_chapter(body=M.ChapterCreate(title="第一章", volume_id=vid)))["id"]
    assert cid
    single = _j(S.get_chapter(chapter_id=cid))
    assert single["id"] == cid and single["title"] == "第一章"
    assert _err(S.get_chapter(chapter_id=99999))
    # 注：create_chapter 不校验 volume_id 存在性（与 main 端点行为一致）
    assert not _err(S.create_chapter(body=M.ChapterCreate(title="x", volume_id=99999)))
    assert not _err(S.delete_chapter(chapter_id=cid))
    assert not _err(S.delete_volume(volume_id=vid))


# ════════════════════════════════════════════════════════════════
# 4.3 大纲
# ════════════════════════════════════════════════════════════════

def test_outline():
    oid = _j(S.create_outline(body=M.OutlineCreate(title="桥接主线", category="剧情大纲")))["id"]
    assert oid
    assert any(o["id"] == oid for o in _j(S.list_outlines()))
    assert not _err(S.delete_outline(outline_id=oid))


# ════════════════════════════════════════════════════════════════
# 4.4 人物 / 势力
# ════════════════════════════════════════════════════════════════

def test_character_crud_and_factions():
    cid = _j(S.create_character(body=M.CharacterCreate(name="桥接甲", status="存活")))["id"]
    assert cid
    fid = _j(S.create_faction(data={"name": "桥接宗", "novel_id": 1}))["id"]
    assert fid
    out = S.set_character_factions(char_id=cid, body=[{"faction_id": fid, "faction_role": "弟子"}])
    assert not _err(out)
    ch = _j(S.get_character(char_id=cid))
    assert ch["id"] == cid
    # 悬空 faction_id → 404
    assert _err(S.set_character_factions(char_id=cid, body=[{"faction_id": 99999, "faction_role": "x"}]))
    assert not _err(S.delete_character(char_id=cid))
    assert not _err(S.delete_faction(faction_id=fid))


def test_time_data_roundtrip():
    cid = _j(S.create_character(body=M.CharacterCreate(name="桥接乙", status="存活")))["id"]
    assert not _err(S.set_time_data(char_id=cid, req=M.CharacterTimeData(routines=[], trajectories=[])))
    td = _j(S.get_time_data(char_id=cid))
    assert td["routines"] == [] and td["trajectories"] == []
    assert not _err(S.delete_character(char_id=cid))


# ════════════════════════════════════════════════════════════════
# 4.5 地点
# ════════════════════════════════════════════════════════════════

def test_location():
    lid = _j(S.create_location(body=M.LocationCreate(name="桥接地", scale_level="城池")))["id"]
    assert lid
    assert any(loc["id"] == lid for loc in _j(S.list_locations()))
    assert not _err(S.delete_location(location_id=lid))
    assert _err(S.delete_location(location_id=99999))


# ════════════════════════════════════════════════════════════════
# 4.6 事件 / 世界事件
# ════════════════════════════════════════════════════════════════

def test_event_and_world_event():
    eid = _j(S.create_event(body=M.EventCreate(title="桥接事件", description="d")))["id"]
    assert eid
    assert not _err(S.delete_event(event_id=eid))
    assert _err(S.accept_world_event(event_id=99999))
    assert _err(S.reject_world_event(event_id=99999))


def test_world_event_accept_and_reject():
    """直接构造 WorldEvent（正常路径由 tick 推进时概率生成）→ accept / reject"""
    db = SessionLocal()
    try:
        w1 = M.WorldEvent(novel_id=1, title="桥接天变A", description="d", status="pending", origin_tick=1)
        w2 = M.WorldEvent(novel_id=1, title="桥接天变B", description="d", status="pending", origin_tick=2)
        db.add_all([w1, w2])
        db.commit()
        ids = [w1.id, w2.id]
    finally:
        db.close()

    assert not _err(S.accept_world_event(event_id=ids[0]))
    # 注：accept/reject 不校验当前 status，可重复调用（与 main 端点行为一致）
    assert not _err(S.reject_world_event(event_id=ids[1]))
    # accept 写入的时间线事件清理
    with SessionLocal() as db:
        for te in db.query(M.TimelineEvent).filter(M.TimelineEvent.event_type == M.TimelineEventType.WORLD).all():
            db.delete(te)
        db.commit()


# ════════════════════════════════════════════════════════════════
# 4.7 时间线 / 活跃事件
# ════════════════════════════════════════════════════════════════

def test_timeline():
    tid = _j(S.create_timeline(body=M.TimelineCreate(
        event_type="MAIN_STORY", title="桥接开局", time_label="第1日", content="c"
    )))["id"]
    assert tid
    assert not _err(S.delete_timeline(event_id=tid))


def test_active_event_lifecycle():
    """主干唯一 → record_branch（无 api_key 跳过 LLM）→ 毕业（无 api_key 走原文总结）"""
    # 清理残留活跃事件节点（消除与 test_active_events.py 的跨文件主干冲突）
    with SessionLocal() as db:
        for n in db.query(M.ActiveEventNode).filter(M.ActiveEventNode.novel_id == 1).all():
            db.delete(n)
        db.commit()

    main = _j(S.create_active_event(body=M.ActiveEventCreate(kind="main", title="桥接主干", content="d", tick=1)))
    assert not main.get("error"), main
    main_id = main["id"]
    dup = _j(S.create_active_event(body=M.ActiveEventCreate(kind="main", title="第二主干", content="d", tick=2)))
    assert dup.get("error") is True  # 主干全局唯一 → 400

    br = asyncio.run(S.record_branch(body=M.RecordBranchRequest(text="这里发生了一件支线的事", start_tick=3)))
    assert not _err(br), br
    assert _err(asyncio.run(S.record_branch(body=M.RecordBranchRequest(text="   ", start_tick=3))))

    grad = asyncio.run(S.graduate_active_event(node_id=main_id, body=M.GraduateRequest()))
    assert not _err(grad), grad
    # 毕业写入的时间线事件清理
    with SessionLocal() as db:
        for te in db.query(M.TimelineEvent).filter(M.TimelineEvent.event_type == M.TimelineEventType.MAIN_STORY).all():
            if te.title and "桥接主干" in (te.content or ""):
                db.delete(te)
            db.commit()


# ════════════════════════════════════════════════════════════════
# 4.8 关系 / 推理链
# ════════════════════════════════════════════════════════════════

def test_relation_and_reasoning_chain():
    c1 = _j(S.create_character(body=M.CharacterCreate(name="链甲", status="存活")))["id"]
    c2 = _j(S.create_character(body=M.CharacterCreate(name="链乙", status="存活")))["id"]
    rid = _j(S.create_character_relation(
        body=M.RelationCreate(source_id=c1, target_id=c2, label="师徒")
    ))["id"]
    assert rid
    assert not _err(S.delete_character_relation(rel_id=rid))

    # 系统内置链：仅可重置/清 todos，不可删除
    sys_chain = _j(S.create_reasoning_chain(body=M.ReasoningChainCreate(title="🗺️ 蓝图规划与逐层搭建流")))
    assert not sys_chain.get("error"), sys_chain
    assert not _err(S.clear_chain_todos(chain_id=sys_chain["id"]))
    assert not _err(S.restore_reasoning_chain(chain_id=sys_chain["id"]))
    assert _err(S.delete_reasoning_chain(chain_id=sys_chain["id"]))
    # 自定义链：可删除，不可重置
    my_chain = _j(S.create_reasoning_chain(body=M.ReasoningChainCreate(title="桥接自定义链")))
    assert not my_chain.get("error"), my_chain
    assert _err(S.restore_reasoning_chain(chain_id=my_chain["id"]))
    assert not _err(S.delete_reasoning_chain(chain_id=my_chain["id"]))
    assert not _err(S.delete_character(char_id=c1))
    assert not _err(S.delete_character(char_id=c2))


# ════════════════════════════════════════════════════════════════
# 4.9 批量交换
# ════════════════════════════════════════════════════════════════

def test_export_import_settings():
    bundle = _j(S.export_settings())
    assert bundle["format"] == "novel-assist-settings"
    md = _j(S.export_settings(fmt="md"))
    assert md["fmt"] == "md" and "设定集" in md["content"]
    assert _err(S.export_settings(novel_id=99999))

    # 含实体的最小 bundle → dry_run 不落库（_finalize 要求至少一个可导入实体）
    min_bundle = {"novel": {"title": "测试"},
                  "locations": [{"name": "桥接城", "description": "", "scale_level": "城池", "attributes": {}}]}
    req = M.ImportSettingsReq(content=json.dumps(min_bundle, ensure_ascii=False), dry_run=True)
    d = _j(S.import_settings(req=req))
    assert d["ok"] is True and d["dry_run"] is True
    # 错误路径：空内容 / 无实体 / 小说不存在
    assert _err(S.import_settings(req=M.ImportSettingsReq(content="   ")))
    assert _err(S.import_settings(req=M.ImportSettingsReq(content=json.dumps({"novel": {"title": "x"}}))))
    assert _err(S.import_settings(req=M.ImportSettingsReq(content=json.dumps(min_bundle)), novel_id=99999))


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
