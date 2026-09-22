"""#T007 离屏世界事件 · 触发器 + 审核 最小单测。

机制：累加「参与概率判定的时间长度」`pending_days`（天）→ 无待审核事件时
按 p = 1 - (1 - prob_per_month) ** 月数 掷骰触发；采纳 → 计数清零并写入 world 时间线；
拒绝 → 计数**减半**后继续累积（避免拒绝一次后每月都弹窗）。
conftest.py 已把工作目录切到临时目录，数据库落在隔离的临时 .data/novel.db。
"""
import pytest

from backend import main as M
from backend.database import Base, engine, SessionLocal

HPD = 24            # 默认历法：24 时/天
DPM = 30            # 30 天/月


def setup_module(module):
    Base.metadata.create_all(bind=engine)


@pytest.fixture(autouse=True)
def _clean():
    """每个用例前清空世界事件/时间线并复位小说时间与配置。"""
    with SessionLocal() as db:
        db.query(M.WorldEvent).delete()
        db.query(M.TimelineEvent).delete()
        nv = db.get(M.Novel, 1)
        if nv is None:
            nv = M.Novel(id=1, title="测试小说")
            db.add(nv)
        nv.current_tick = 0
        nv.calendar_config = {"months_per_year": 12, "days_per_month": DPM, "hours_per_day": HPD}
        nv.world_event_config = {"enabled": True, "prob_per_month": 1.0, "pending_days": 0.0}
        db.commit()
    yield


def _pending_days(db):
    return float((db.get(M.Novel, 1).world_event_config or {}).get("pending_days", 0.0))


def test_prob1_triggers_pending_draft():
    """prob=1 → 任意推进都触发一条待审核草稿。"""
    with SessionLocal() as db:
        created = M.check_world_event_triggers(db, 1, 0, 5 * HPD)  # 推进 5 天
        assert len(created) == 1
        assert created[0].status == "pending"
        assert created[0].origin_tick == 5 * HPD
        assert _pending_days(db) == pytest.approx(5.0)


def test_prob0_accumulates_but_never_triggers():
    """prob=0 → 不触发，但累计时间长度持续增长。"""
    with SessionLocal() as db:
        nv = db.get(M.Novel, 1)
        nv.world_event_config = {"enabled": True, "prob_per_month": 0.0, "pending_days": 0.0}
        db.commit()
        assert M.check_world_event_triggers(db, 1, 0, 30 * HPD) == []
        assert _pending_days(db) == pytest.approx(30.0)


def test_pending_blocks_new_trigger_but_time_accumulates():
    """存在未处理 pending 时不再新建，但累计时间继续增长。"""
    with SessionLocal() as db:
        assert len(M.check_world_event_triggers(db, 1, 0, 1 * HPD)) == 1
        assert M.check_world_event_triggers(db, 1, 1 * HPD, 3 * HPD) == []
        assert _pending_days(db) == pytest.approx(3.0)


def test_accept_clears_counter_and_writes_world_timeline():
    with SessionLocal() as db:
        ev = M.check_world_event_triggers(db, 1, 0, 10 * HPD)[0]
        ev.title = "北境雪灾"  # 模拟已有生成内容
        db.commit()
        out = M.accept_world_event(ev.id, db=db)
        assert out.status == "accepted" and out.is_rumor is True
        assert _pending_days(db) == pytest.approx(0.0)
        tes = db.query(M.TimelineEvent).all()
        assert len(tes) == 1
        assert tes[0].event_type == M.TimelineEventType.WORLD
        assert tes[0].title == "北境雪灾"
        assert tes[0].absolute_tick == 10 * HPD


def test_reject_halves_counter():
    with SessionLocal() as db:
        ev = M.check_world_event_triggers(db, 1, 0, 10 * HPD)[0]
        assert _pending_days(db) == pytest.approx(10.0)
        out = M.reject_world_event(ev.id, db=db)
        assert out.status == "rejected"
        assert _pending_days(db) == pytest.approx(5.0)  # 减半后继续累计


def test_disabled_switch():
    with SessionLocal() as db:
        nv = db.get(M.Novel, 1)
        nv.world_event_config = {"enabled": False}
        db.commit()
        assert M.check_world_event_triggers(db, 1, 0, 100 * HPD) == []


def test_no_trigger_on_non_advance():
    with SessionLocal() as db:
        assert M.check_world_event_triggers(db, 1, 100, 100) == []
        assert M.check_world_event_triggers(db, 1, 100, 50) == []


def test_reproducible_same_span():
    """同一区间的概率判定可复现（种子由 novel_id/prev/new 决定）。"""
    with SessionLocal() as db:
        nv = db.get(M.Novel, 1)
        nv.world_event_config = {"enabled": True, "prob_per_month": 0.5, "pending_days": 0.0}
        db.commit()
        r1 = M.check_world_event_triggers(db, 1, 0, 2 * HPD)
        db.query(M.WorldEvent).delete()
        nv = db.get(M.Novel, 1)
        nv.world_event_config = {"enabled": True, "prob_per_month": 0.5, "pending_days": 0.0}
        db.commit()
        r2 = M.check_world_event_triggers(db, 1, 0, 2 * HPD)
        assert len(r1) == len(r2)


def test_world_lore_excludes_pending():
    """待审核草稿不进入提示词世界事件轴，仅已采纳的事件注入。"""
    with SessionLocal() as db:
        db.add(M.WorldEvent(novel_id=1, title="待审核事件", status="pending", origin_tick=100))
        db.add(M.WorldEvent(novel_id=1, title="已采纳事件", status="accepted", origin_tick=50))
        db.commit()
        lore = M.get_world_lore(db, 1)
        assert "已采纳事件" in lore
        assert "待审核事件" not in lore
