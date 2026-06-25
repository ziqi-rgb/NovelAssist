"""数据库连接配置 — SQLite + SQLAlchemy 2.0"""
import os
import sys

try:
    from .constants import LocationScaleLevel
except ImportError:
    from constants import LocationScaleLevel

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
        if "pending_timeline_text" not in ch_cols:
            db.execute(text("ALTER TABLE chapters ADD COLUMN pending_timeline_text TEXT DEFAULT ''"))
            db.commit()

        te_cols = [c["name"] for c in insp.get_columns("timeline_events")]
        if "timeline_index" not in te_cols:
            db.execute(text("ALTER TABLE timeline_events ADD COLUMN timeline_index INTEGER DEFAULT 0"))
            db.commit()

        loc_cols = [c["name"] for c in insp.get_columns("locations")]
        if "scale_level" not in loc_cols:
            db.execute(text(f"ALTER TABLE locations ADD COLUMN scale_level TEXT DEFAULT '{LocationScaleLevel.PLANET.value}'"))
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
        if "title" not in te_cols2:
            db.execute(text("ALTER TABLE timeline_events ADD COLUMN title TEXT DEFAULT ''"))
            db.commit()
        if "created_at" not in te_cols2:
            db.execute(text("ALTER TABLE timeline_events ADD COLUMN created_at TIMESTAMP"))
            db.commit()
        if "updated_at" not in te_cols2:
            db.execute(text("ALTER TABLE timeline_events ADD COLUMN updated_at TIMESTAMP"))
            db.commit()
        if "character_ids" not in te_cols2:
            db.execute(text("ALTER TABLE timeline_events ADD COLUMN character_ids JSON DEFAULT '[]'"))
            db.commit()
        if "category" not in te_cols2:
            db.execute(text("ALTER TABLE timeline_events ADD COLUMN category TEXT DEFAULT '其他'"))
            db.commit()

        # Ensure timeline_event_relations table exists
        insp2 = inspect(db.get_bind())
        if "timeline_event_relations" not in insp2.get_table_names():
            db.execute(text("""
                CREATE TABLE timeline_event_relations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    novel_id INTEGER NOT NULL DEFAULT 1 REFERENCES novels(id),
                    source_event_id INTEGER NOT NULL REFERENCES timeline_events(id) ON DELETE CASCADE,
                    target_event_id INTEGER NOT NULL REFERENCES timeline_events(id) ON DELETE CASCADE,
                    label VARCHAR(64) NOT NULL DEFAULT '导致',
                    description TEXT NOT NULL DEFAULT ''
                )
            """))
            db.commit()

        if "faction_id" not in char_cols:
            db.execute(text("ALTER TABLE characters ADD COLUMN faction_id INTEGER"))
            db.commit()
        if "faction_role" not in char_cols:
            db.execute(text("ALTER TABLE characters ADD COLUMN faction_role TEXT DEFAULT ''"))
            db.commit()

        loc_cols2 = [c["name"] for c in insp.get_columns("locations")]
        if "map_x" not in loc_cols2:
            db.execute(text("ALTER TABLE locations ADD COLUMN map_x INTEGER"))
            db.commit()
        if "map_y" not in loc_cols2:
            db.execute(text("ALTER TABLE locations ADD COLUMN map_y INTEGER"))
            db.commit()

        rc_cols = [c["name"] for c in insp.get_columns("reasoning_chains")]
        if "todos" not in rc_cols:
            db.execute(text("ALTER TABLE reasoning_chains ADD COLUMN todos JSON DEFAULT '[]'"))
            db.commit()
    finally:
        db.close()

