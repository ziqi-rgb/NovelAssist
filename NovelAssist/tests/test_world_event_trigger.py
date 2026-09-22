"""#T007 离屏世界事件 · 触发器最小单测。

覆盖：大跨度必触发 / 同区间幂等 / max_pending 上限 / enabled 开关 / 不回溯 / 可复现 / 提示词过滤。
conftest.py 已把工作目录切到临时目录，数据库落在隔离的临时 .data/novel.db。
"""
import pytest

from backend import main as M
from backend.database import Base, engine, SessionLocal

TICKS_PER_MONTH = 30 * 24  # 默认历法：30 天/月 × 24 时/天 = 720


def setup_module(module):
    Base.metadata.create_all(bind=engine)


@pytest.fixture(autouse=True)
def _clean():
    """每个用例前清空世界事件并复位小说时间/触发配置。"""
    with SessionLocal() as db:
        db.query(M.WorldEvent).delete()
        nv = db.get(M.Novel, 1)
        if nv is None:
            nv = M.Novel(id=1, title="测试小说")
            db.add(nv)
        nv.current_tick = 0
        nv.calendar_config = {"months_per_year": 12, "days_per_month": 30, "hours_per_day": 24}
        nv.world_event_config = {"enabled": True, "prob_per_month": 0.35, "max_pending": 3, "last_check_tick": 0}
        db.commit()
    yield


def test_large_span_triggers_pending_draft():
    """推进一年 → p≈1 → 必生成一条待审核草稿。"""
    with SessionLocal() as db:
        created = M.check_world_event_triggers(db, 1, 0, 12 * TICKS_PER_MONTH)
        assert len(created) == 1
        ev = created[0]
        assert ev.status == "pending"
        assert ev.origin_tick == 12 * TICKS_PER_MONTH
        assert ev.is_rumor is False


def test_same_span_is_idempotent():
    """同一 (prev,new) 区间重复调用不重复生成。"""
    with SessionLocal() as db:
        a = M.check_world_event_triggers(db, 1, 0, 12 * TICKS_PER_MONTH)
        b = M.check_world_event_triggers(db, 1, 0, 12 * TICKS_PER_MONTH)
        assert len(a) == 1
        assert len(b) == 0


def test_respects_max_pending():
    """待审核数量达到 max_pending 后不再生成。"""
    with SessionLocal() as db:
        nv = db.get(M.Novel, 1)
        nv.world_event_config = {"enabled": True, "prob_per_month": 1.0, "max_pending": 2}
        db.commit()
        assert len(M.check_world_event_triggers(db, 1, 0, TICKS_PER_MONTH)) == 1
        assert len(M.check_world_event_triggers(db, 1, TICKS_PER_MONTH, 2 * TICKS_PER_MONTH)) == 1
        assert len(M.check_world_event_triggers(db, 1, 2 * TICKS_PER_MONTH, 3 * TICKS_PER_MONTH)) == 0


def test_disabled_switch():
    """enabled=False 时永不触发。"""
    with SessionLocal() as db:
        nv = db.get(M.Novel, 1)
        nv.world_event_config = {"enabled": False}
        db.commit()
        assert M.check_world_event_triggers(db, 1, 0, 12 * TICKS_PER_MONTH) == []


def test_no_trigger_on_non_advance():
    """时间未前进（相等/倒退）不触发。"""
    with SessionLocal() as db:
        assert M.check_world_event_triggers(db, 1, 100, 100) == []
        assert M.check_world_event_triggers(db, 1, 100, 50) == []


def test_reproducible_same_span():
    """同一区间的随机判定可复现（种子由 novel_id/prev/new 决定）。"""
    with SessionLocal() as db:
        r1 = M.check_world_event_triggers(db, 1, 0, 60)  # 小跨度 → 小概率
        db.query(M.WorldEvent).delete()
        db.commit()
        r2 = M.check_world_event_triggers(db, 1, 0, 60)
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
