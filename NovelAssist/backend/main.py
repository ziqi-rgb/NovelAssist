"""
AI 小说辅助引擎 — FastAPI 后端入口
提供静态文件服务、状态接口和章节 CRUD API
"""
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import Base, engine, get_db
from .models import Chapter


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 注意：SQLite 不支持 ALTER TABLE，如果旧数据库中 chapters 表缺少 outline_id 列，
    # 请手动删除 .data/novel.db 文件后重启，应用会自动重新建表。
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="AI 小说辅助引擎", version="0.2.0", lifespan=lifespan)


class ChapterCreate(BaseModel):
    title: str = ""
    content: str = ""
    order_index: int = 0


class ChapterOut(BaseModel):
    id: int
    title: str
    content: str
    order_index: int
    created_at: datetime

    class Config:
        from_attributes = True


@app.get("/api/status")
async def get_status() -> dict[str, str]:
    return {"status": "ok", "message": "Backend is running"}


@app.post("/api/chapters", response_model=ChapterOut)
def create_chapter(body: ChapterCreate, db: Session = Depends(get_db)):
    chapter = Chapter(
        title=body.title, content=body.content, order_index=body.order_index
    )
    db.add(chapter)
    db.commit()
    db.refresh(chapter)
    return chapter


@app.put("/api/chapters/{chapter_id}", response_model=ChapterOut)
def update_chapter(chapter_id: int, body: ChapterCreate, db: Session = Depends(get_db)):
    chapter = db.get(Chapter, chapter_id)
    if chapter is None:
        raise HTTPException(status_code=404, detail="章节不存在")
    chapter.title = body.title
    chapter.content = body.content
    db.commit()
    db.refresh(chapter)
    return chapter


@app.get("/api/chapters", response_model=list[ChapterOut])
def list_chapters(db: Session = Depends(get_db)):
    stmt = select(Chapter).order_by(Chapter.order_index)
    return db.scalars(stmt).all()


frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
