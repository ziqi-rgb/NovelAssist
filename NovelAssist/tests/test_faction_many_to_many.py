"""#T009 势力多对多中间表最小单测 — 覆盖中间表 CRUD / 端点 / 导入导出 / 级联删除。

conftest.py 已把工作目录切到临时目录，数据库落在隔离的临时 .data/novel.db。
"""
import time

import pytest
from fastapi import HTTPException

from backend import main as M
from backend.database import Base, engine, SessionLocal

_RUN = time.strftime("%H%M%S")


@pytest.fixture(autouse=True)
def _clean_character_factions():
    with SessionLocal() as db:
        db.query(M.CharacterFaction).delete()
        db.commit()
    yield


def setup_module(module):
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        if db.get(M.Novel, 1) is None:
            db.add(M.Novel(id=1, title="测试小说"))
            db.commit()


def _make_faction(db, name):
    r = M.create_faction({"novel_id": 1, "name": name}, db=db)
    return db.get(M.Faction, r["id"])


def test_set_character_factions_replaces_links():
    with SessionLocal() as db:
        f1 = _make_faction(db, f"青云宗{_RUN}")
        f2 = _make_faction(db, f"暗影会{_RUN}")
        c = M.create_character(M.CharacterCreate(name=f"测试者{_RUN}"), novel_id=1, db=db)

        # 整体替换：先加入 f1，再加入 f2 → 两个关联都在
        M.set_character_factions(c.id, [M.CharacterFactionLinkIn(faction_id=f1.id, faction_role="宗主"),
                                        M.CharacterFactionLinkIn(faction_id=f2.id, faction_role="内应")], db=db)
        assert c.faction_id == f1.id  # 首个为主势力
        roles = {lf.faction_id: lf.faction_role for lf in c.faction_members}
        assert roles == {f1.id: "宗主", f2.id: "内应"}

        # 再整体替换：只剩 f2 → f1 关联被清除，主势力变 f2
        M.set_character_factions(c.id, [M.CharacterFactionLinkIn(faction_id=f2.id, faction_role="长老")], db=db)
        assert c.faction_id == f2.id
        assert {lf.faction_id for lf in c.faction_members} == {f2.id}

        # 替换为空 → 全部清除
        M.set_character_factions(c.id, [], db=db)
        assert c.faction_id is None
        assert not c.faction_members


def test_get_factions_returns_members():
    with SessionLocal() as db:
        f1 = _make_faction(db, f"测力{_RUN}")
        c = M.create_character(M.CharacterCreate(name=f"测员{_RUN}"), novel_id=1, db=db)
        M.set_character_factions(c.id, [M.CharacterFactionLinkIn(faction_id=f1.id, faction_role="执事")], db=db)

        rows = M.get_factions(novel_id=1, db=db)
        hit = [r for r in rows if r["id"] == f1.id][0]
        assert hit["faction_members"] == [{"character_id": c.id, "name": f"测员{_RUN}", "faction_role": "执事"}]


def test_settings_roundtrip_with_multiple_factions():
    with SessionLocal() as db:
        f1 = _make_faction(db, f"甲宗{_RUN}")
        f2 = _make_faction(db, f"乙会{_RUN}")
        c = M.create_character(M.CharacterCreate(name=f"多势力{_RUN}"), novel_id=1, db=db)
        M.set_character_factions(c.id, [M.CharacterFactionLinkIn(faction_id=f1.id, faction_role="宗主"),
                                        M.CharacterFactionLinkIn(faction_id=f2.id, faction_role="卧底")], db=db)

        # JSON 导出 → 重新导入同一 novel（幂等，无变化）
        bundle = M._collect_settings_bundle(db, db.get(M.Novel, 1))
        mine = [ch for ch in bundle["characters"] if ch["name"] == f"多势力{_RUN}"]
        assert {m["faction_name"] for m in mine[0]["faction_members"]} == {f1.name, f2.name}

        md = M._render_settings_markdown(bundle)
        assert f"势力: {f1.name} (宗主)" in md
        assert f"势力: {f2.name} (卧底)" in md
        parsed = M._parse_settings_markdown(md)
        pchar = [ch for ch in parsed["characters"] if ch["name"] == f"多势力{_RUN}"][0]
        assert pchar["faction_name"] == f1.name  # 首个为主势力
        assert len(pchar["faction_members"]) == 2

        # 导入幂等：collect → parse(md) → upsert，不应产生删除
        stats = M._upsert_settings_bundle(db, db.get(M.Novel, 1), parsed)
        db.rollback()
        assert stats["deleted"] == 0


def test_delete_character_and_faction_cascade():
    with SessionLocal() as db:
        f1 = _make_faction(db, f"级联宗{_RUN}")
        c = M.create_character(M.CharacterCreate(name=f"级联员{_RUN}"), novel_id=1, db=db)
        M.set_character_factions(c.id, [M.CharacterFactionLinkIn(faction_id=f1.id, faction_role="弟子")], db=db)

        # 删势力 → 中间表关联清除，角色主势力置空
        M.delete_faction(f1.id, db=db)
        assert db.get(M.Character, c.id).faction_id is None
        assert db.query(M.CharacterFaction).count() == 0

        c2 = M.create_character(M.CharacterCreate(name=f"级联员2{_RUN}"), novel_id=1, db=db)
        # 势力已删除 → 端点应拒绝创建悬空关联（防御校验）
        with pytest.raises(HTTPException):
            M.set_character_factions(c2.id, [M.CharacterFactionLinkIn(faction_id=f1.id)], db=db)
        db.rollback()
        assert db.query(M.CharacterFaction).count() == 0

        # 删角色 → 中间表关联清除
        c3 = M.create_character(M.CharacterCreate(name=f"级联员3{_RUN}"), novel_id=1, db=db)
        f2 = _make_faction(db, f"级联会{_RUN}")
        M.set_character_factions(c3.id, [M.CharacterFactionLinkIn(faction_id=f2.id, faction_role="成员")], db=db)
        M.delete_character(c3.id, db=db)
        assert db.query(M.CharacterFaction).count() == 0
