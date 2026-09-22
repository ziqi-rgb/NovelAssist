"""#T005 事件树（ActiveEvent）最小单测 — 覆盖 CRUD / 汇总注入 / 毕业流转。

不依赖 starlette TestClient（与当前 httpx 版本不兼容），直接调用端点函数。
conftest.py 已把工作目录切到临时目录，数据库落在隔离的临时 .data/novel.db。
"""
import asyncio

from backend import main as M
from backend.database import Base, engine, SessionLocal


def setup_module(module):
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        if db.get(M.Novel, 1) is None:
            db.add(M.Novel(id=1, title="测试小说"))
            db.commit()


def test_active_event_crud_and_summary():
    with SessionLocal() as db:
        main_node = M.create_active_event(M.ActiveEventCreate(
            kind="main", title="闯荡青云宗", content="主角入门", tick=0, end_tick=240,
        ), novel_id=1, db=db)
        assert main_node.kind == M.ActiveEventKind.MAIN
        assert main_node.parent_id is None

        branch = M.create_active_event(M.ActiveEventCreate(
            kind="branch", parent_id=main_node.id, title="拜师受挫", content="被拒之门外", tick=60,
        ), novel_id=1, db=db)
        assert branch.parent_id == main_node.id

        burst = M.create_active_event(M.ActiveEventCreate(
            kind="burst", parent_id=main_node.id, title="秘境开启", content="下月秘境开启", tick=5000,
        ), novel_id=1, db=db)
        assert burst.kind == M.ActiveEventKind.BURST

        items = M.list_active_events(novel_id=1, db=db)
        assert sorted(n.kind.value for n in items) == ["branch", "burst", "main"]

        updated = M.update_active_event(main_node.id, M.ActiveEventCreate(
            kind="main", title="闯荡青云宗", content="主角入门", tick=0, end_tick=300,
        ), db=db)
        assert updated.end_tick == 300

        summary = M.build_active_events_summary(db, 1)
        assert "【正在发生的剧情】" in summary
        assert "闯荡青云宗" in summary
        assert "拜师受挫" in summary
        assert "秘境开启" in summary

        M.delete_active_event(branch.id, db=db)
        assert all(n.id != branch.id for n in M.list_active_events(novel_id=1, db=db))


def test_lore_placeholder_includes_active_events():
    with SessionLocal() as db:
        M.create_active_event(M.ActiveEventCreate(
            kind="main", title="LORE注入主干", content="注入内容", tick=0,
        ), novel_id=1, db=db)
        text = M.resolve_placeholders("{lore}", 1, db)
        assert "正在发生的剧情" in text
        assert "LORE注入主干" in text


def test_record_branch_without_key_falls_back_to_text():
    with SessionLocal() as db:
        node = asyncio.run(M.record_active_branch(
            M.RecordBranchRequest(text="主角在夜色中独自修行，突破瓶颈。", start_tick=5, end_tick=10),
            novel_id=1, db=db,
        ))
        assert node.kind == M.ActiveEventKind.BRANCH
        assert node.tick == 5
        # 无 api_key → 直接以原文作为记录内容
        assert "修行" in node.content


def test_graduate_moves_main_to_timeline_and_removes_subtree():
    with SessionLocal() as db:
        main_node = M.create_active_event(M.ActiveEventCreate(
            kind="main", title="大战魔尊", content="决战", tick=10, end_tick=50,
        ), novel_id=1, db=db)
        M.create_active_event(M.ActiveEventCreate(
            kind="branch", parent_id=main_node.id, title="援军抵达", content="援军赶到", tick=40,
        ), novel_id=1, db=db)

        # 无 api_key → LLM 步骤退化，直接用汇总语料写 main_story
        result = asyncio.run(M.graduate_active_event(main_node.id, M.GraduateRequest(), db=db))
        assert result["ok"] is True
        ev_id = result["timeline_event_id"]

        tls = M.list_timeline(novel_id=1, event_type="main_story", db=db)
        grad = next(t for t in tls if t.id == ev_id)
        assert "大战魔尊" in grad.content
        assert grad.event_type == M.TimelineEventType.MAIN_STORY

        # 事件树中主干及其分支全部移除
        assert all(n.id != main_node.id for n in M.list_active_events(novel_id=1, db=db))

        # 只有主干可毕业
        branch = M.create_active_event(M.ActiveEventCreate(
            kind="branch", title="孤立分支", tick=1,
        ), novel_id=1, db=db)
        try:
            asyncio.run(M.graduate_active_event(branch.id, M.GraduateRequest(), db=db))
            raise AssertionError("分支不应可毕业")
        except M.HTTPException as exc:
            assert exc.status_code == 400
