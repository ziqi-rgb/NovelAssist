"""数据库连接配置 — SQLite + SQLAlchemy 2.0"""
import os
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session
from collections.abc import Generator

if getattr(sys, "frozen", False):
    base_dir = os.path.dirname(sys.executable)
else:
    base_dir = os.path.abspath(".")

DATA_DIR = os.path.join(base_dir, ".data")
os.makedirs(DATA_DIR, exist_ok=True)

DATABASE_URL = f"sqlite:///{os.path.join(DATA_DIR, 'novel.db')}"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖注入：每次请求获取一个数据库会话"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def run_migration() -> None:
    """创建默认小说项目，并迁移旧数据"""
    from sqlalchemy import inspect

    db = SessionLocal()
    try:
        # Run schema migrations FIRST before any ORM query
        insp = inspect(db.get_bind())
        char_cols = [c["name"] for c in insp.get_columns("characters")]
        if "is_always_context" not in char_cols:
            db.execute(text("ALTER TABLE characters ADD COLUMN is_always_context BOOLEAN DEFAULT 0"))
            db.commit()
        if "is_active" not in char_cols:
            db.execute(text("ALTER TABLE characters ADD COLUMN is_active BOOLEAN DEFAULT 1"))
            db.commit()

        ch_cols = [c["name"] for c in insp.get_columns("chapters")]
        if "archived_content" not in ch_cols:
            db.execute(text("ALTER TABLE chapters ADD COLUMN archived_content TEXT DEFAULT ''"))
            db.commit()

        te_cols = [c["name"] for c in insp.get_columns("timeline_events")]
        if "timeline_index" not in te_cols:
            db.execute(text("ALTER TABLE timeline_events ADD COLUMN timeline_index INTEGER DEFAULT 0"))
            db.commit()

        loc_cols = [c["name"] for c in insp.get_columns("locations")]
        if "scale_level" not in loc_cols:
            db.execute(text("ALTER TABLE locations ADD COLUMN scale_level TEXT DEFAULT '星球'"))
            db.commit()
        if "attributes" not in loc_cols:
            db.execute(text("ALTER TABLE locations ADD COLUMN attributes JSON DEFAULT '{}'"))
            db.commit()

        novel_cols = [c["name"] for c in insp.get_columns("novels")]
        if "location_templates" not in novel_cols:
            db.execute(text("ALTER TABLE novels ADD COLUMN location_templates JSON DEFAULT '{}'"))
            db.commit()
        if "calendar_config" not in novel_cols:
            db.execute(text("ALTER TABLE novels ADD COLUMN calendar_config JSON DEFAULT '{}'"))
            db.commit()
        if "current_tick" not in novel_cols:
            db.execute(text("ALTER TABLE novels ADD COLUMN current_tick INTEGER NOT NULL DEFAULT 0"))
            db.commit()
        te_cols2 = [c["name"] for c in insp.get_columns("timeline_events")]
        if "absolute_tick" not in te_cols2:
            db.execute(text("ALTER TABLE timeline_events ADD COLUMN absolute_tick INTEGER NOT NULL DEFAULT 0"))
            db.commit()
    finally:
        db.close()

