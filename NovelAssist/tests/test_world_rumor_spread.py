"""#T007 世界事件 · 传闻扩散 + 角色中介可见性 单测。

覆盖：设置端点读写 / 分形扩散（同层曼哈顿 + 进入子层）/ 到达过地点→知晓 /
场景判定注入文本。conftest.py 已把工作目录切到临时目录，数据库落在隔离的临时 .data/novel.db。
"""
import pytest

from backend import main as M
from backend.database import Base, engine, SessionLocal

HPD = 24


def setup_module(module):
    Base.metadata.create_all(bind=engine)


@pytest.fixture(autouse=True)
def _clean():
    with SessionLocal() as db:
        db.query(M.WorldEvent).delete()
        db.query(M.TimelineEvent).delete()
        db.query(M.ActiveEventNode).delete()
        db.query(M.CharacterTrajectory).delete()
        db.query(M.CharacterRoutine).delete()
        db.query(M.Character).delete()
        db.query(M.Location).delete()
        nv = db.get(M.Novel, 1)
        if nv is None:
            nv = M.Novel(id=1, title="测试小说")
            db.add(nv)
        nv.current_tick = 0
        nv.calendar_config = {"months_per_year": 12, "days_per_month": 30, "hours_per_day": HPD}
        nv.world_event_config = {"enabled": True, "prob_per_month": 1.0, "pending_days": 0.0, "spread_base_speed": 2.0}
        db.commit()
    yield


def _mk_loc(db, name, parent=None, gx=0, gy=0):
    loc = M.Location(name=name, parent_id=parent.id if parent else None, grid_x=gx, grid_y=gy, map_x=gx, map_y=gy, novel_id=1)
    db.add(loc)
    db.commit()
    db.refresh(loc)
    return loc


def test_config_roundtrip():
    with SessionLocal() as db:
        cfg = M.get_world_event_config(novel_id=1, db=db)
        assert cfg["enabled"] is True and cfg["spread_base_speed"] == 2.0
        out = M.update_world_event_config(
            M.WorldEventSettings(novel_id=1, enabled=False, prob_per_month=0.5, spread_base_speed=3.0), db=db
        )
        assert out["enabled"] is False and out["prob_per_month"] == 0.5 and out["spread_base_speed"] == 3.0
        assert M.get_world_event_config(novel_id=1, db=db)["enabled"] is False


def test_spread_reach_horizontal_and_downward():
    with SessionLocal() as db:
        root = _mk_loc(db, "大陆")
        a = _mk_loc(db, "城A", root, 0, 0)
        b = _mk_loc(db, "城B", root, 3, 0)
        c = _mk_loc(db, "城C", root, 10, 0)
        _mk_loc(db, "街区a1", a, 0, 0)
        a2 = _mk_loc(db, "街区a2", a, 2, 0)
        reach = M._compute_spread_reach(db, a, speed=2.0, base_tick=0, hpd=HPD, current_tick=1000)
        by = {r["location_id"]: r for r in reach}
        assert by[a.id]["tick"] == 0
        assert by[b.id]["tick"] == int(3 / 2.0 * HPD)   # 同层曼哈顿 cost=3
        assert by[c.id]["tick"] == int(10 / 2.0 * HPD)  # cost=10
        assert by[a2.id]["depth"] == 1                  # 进入子层
        assert by[a2.id]["tick"] == int(1 / 2.0 * HPD)  # 入口 cost=1
        # current_tick 上限：到达 tick 超过则不计
        limited = M._compute_spread_reach(db, a, speed=2.0, base_tick=0, hpd=HPD, current_tick=int(3 / 2.0 * HPD))
        assert c.id not in {r["location_id"] for r in limited}


def test_advance_marks_knowers_via_visit():
    with SessionLocal() as db:
        root = _mk_loc(db, "大陆")
        a = _mk_loc(db, "城A", root, 0, 0)
        b = _mk_loc(db, "城B", root, 3, 0)
        ch = M.Character(name="甲", novel_id=1)
        db.add(ch)
        db.commit()
        db.refresh(ch)
        db.add(M.CharacterTrajectory(character_id=ch.id, location_id=a.id, start_tick=0))
        ev = M.WorldEvent(
            novel_id=1, title="A城火灾", status="accepted", is_rumor=True, origin_tick=0, location_id=a.id,
            spread_json={"origin_location_id": a.id, "origin_tick": 0, "speed": 100.0, "spread_prob": 1.0,
                         "reached": [], "knowers": []},
        )
        db.add(ev)
        db.commit()
        n = M.advance_rumor_spread(db, 1, 0, 1000)
        assert n == 1
        db.refresh(ev)
        assert ch.id in ev.spread_json["knowers"]                       # 到达过 A → 知晓
        assert any(r["location_id"] == b.id for r in ev.spread_json["reached"])  # 扩散到 B


def test_scene_rumors_injection():
    with SessionLocal() as db:
        root = _mk_loc(db, "大陆")
        a = _mk_loc(db, "城A", root, 0, 0)
        ch = M.Character(name="甲", novel_id=1, is_active=True)
        db.add(ch)
        db.commit()
        db.refresh(ch)
        ev = M.WorldEvent(
            novel_id=1, title="A城火灾", description="大火", status="accepted", is_rumor=True,
            origin_tick=0, location_id=a.id,
            spread_json={"origin_location_id": a.id, "origin_tick": 0, "speed": 2.0, "spread_prob": 1.0,
                         "reached": [], "knowers": [ch.id]},
        )
        db.add(ev)
        db.commit()
        txt = M.get_scene_rumors(db, 1)
        assert "A城火灾" in txt and "甲" in txt

        # 无知晓者时不注入
        ev.spread_json = {**ev.spread_json, "knowers": []}
        db.commit()
        assert M.get_scene_rumors(db, 1) == ""
