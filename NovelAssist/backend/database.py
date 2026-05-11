"""数据库连接配置 — SQLite + SQLAlchemy 2.0"""
from pathlib import Path
from sqlalchemy import create_engine
from collections.abc import Generator

from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session

DATA_DIR = Path(__file__).resolve().parent.parent / ".data"
DATA_DIR.mkdir(exist_ok=True)

DATABASE_URL = f"sqlite:///{DATA_DIR / 'novel.db'}"
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
