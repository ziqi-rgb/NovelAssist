"""
AI 小说辅助引擎 — FastAPI 后端入口
提供静态文件服务、状态接口和多项目 CRUD API
"""
import copy
import io
import json
import os
import re
import sys
import threading
import urllib.parse
import webbrowser
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, Query, Body
from fastapi.responses import StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from openai import AsyncOpenAI
from sqlalchemy import select, func, text
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

import uvicorn
import multiprocessing

load_dotenv()

try:
    from .database import Base, engine, get_db, run_migration  # noqa: E402
    from .models import (  # noqa: E402
        Chapter, Character, Outline, Location,
        WorldEvent, Novel, Volume, TimelineEvent, CharacterSnapshot, ReasoningChain, GlobalSettings,
        DEFAULT_PROMPT_TEMPLATES, TimelineEventType,
    )
except ImportError:
    from database import Base, engine, get_db, run_migration  # noqa: E402
    from models import (  # noqa: E402
        Chapter, Character, Outline, Location,
        WorldEvent, Novel, Volume, TimelineEvent, CharacterSnapshot, ReasoningChain, GlobalSettings,
        DEFAULT_PROMPT_TEMPLATES, TimelineEventType,
    )

LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-3.5-turbo")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    run_migration()
    # Data healing: normalize timeline_events event_type to uppercase
    try:
        from .database import SessionLocal
    except ImportError:
        from database import SessionLocal
    db = SessionLocal()
    try:
        db.execute(text("UPDATE timeline_events SET event_type = 'MAIN_STORY' WHERE event_type = 'main_story'"))
        db.execute(text("UPDATE timeline_events SET event_type = 'HISTORY' WHERE event_type = 'history'"))
        db.execute(text("UPDATE timeline_events SET event_type = 'WORLD' WHERE event_type = 'world'"))
        db.commit()
    finally:
        db.close()
    yield


app = FastAPI(title="AI 小说辅助引擎", version="0.5.1", lifespan=lifespan)
NOVEL_ID_DEFAULT = 1


# ═══════════ Schemas ═══════════

class NovelCreate(BaseModel):
    title: str = "新小说"

class NovelOut(BaseModel):
    id: int
    title: str
    character_template: Optional[list] = None
    created_at: datetime
    updated_at: datetime
    class Config:
        from_attributes = True

class VolumeCreate(BaseModel):
    title: str = ""
    order_index: int = 0

class VolumeOut(BaseModel):
    id: int
    novel_id: int
    title: str
    order_index: int
    class Config:
        from_attributes = True

class ChapterCreate(BaseModel):
    title: str = ""
    content: str = ""
    archived_content: str = ""
    order_index: int = 0
    volume_id: Optional[int] = None

class ChapterOut(BaseModel):
    id: int
    title: str
    content: str
    archived_content: str = ""
    order_index: int
    volume_id: Optional[int] = None
    novel_id: int
    created_at: datetime
    class Config:
        from_attributes = True

class OutlineCreate(BaseModel):
    title: str = ""
    description: str = ""
    category: str = "剧情"
    parent_id: Optional[int] = None
    order_index: int = 0
    is_always_context: bool = False

class OutlineOut(BaseModel):
    id: int
    title: str
    description: str
    category: str
    parent_id: Optional[int] = None
    order_index: int
    novel_id: int
    is_always_context: bool
    class Config:
        from_attributes = True

class CharacterCreate(BaseModel):
    name: str = ""
    aliases: str = ""
    description: str = ""
    status: str = "存活"
    is_active: bool = True
    is_always_context: bool = False
    attributes: dict = {}

class CharacterOut(BaseModel):
    id: int
    name: str
    aliases: str
    description: str
    status: str
    is_active: bool = True
    is_always_context: bool = False
    attributes: dict = {}
    novel_id: int
    class Config:
        from_attributes = True

class LocationCreate(BaseModel):
    name: str = ""
    description: str = ""
    parent_id: Optional[int] = None
    scale_level: str = "星球"
    scale: str = "REGION"
    grid_x: Optional[int] = None
    grid_y: Optional[int] = None
    attributes: dict = {}

class LocationOut(BaseModel):
    id: int
    name: str
    description: str
    parent_id: Optional[int] = None
    scale_level: str = "星球"
    scale: str
    grid_x: Optional[int] = None
    grid_y: Optional[int] = None
    attributes: dict = {}
    computed_attributes: dict = {}
    novel_id: int
    class Config:
        from_attributes = True

class EventCreate(BaseModel):
    title: str = ""
    description: str = ""
    event_type: str = "MAIN"
    timeline_index: int = 0
    location_id: Optional[int] = None

class EventOut(BaseModel):
    id: int
    title: str
    description: str
    event_type: str
    timeline_index: int
    location_id: Optional[int] = None
    novel_id: int
    class Config:
        from_attributes = True

class TimelineCreate(BaseModel):
    event_type: str = "main_story"
    time_label: str = ""
    content: str = ""
    related_location_id: Optional[int] = None

class TimelineOut(BaseModel):
    id: int
    novel_id: int
    event_type: str
    time_label: str
    content: str
    related_location_id: Optional[int] = None
    class Config:
        from_attributes = True


class SnapshotCreate(BaseModel):
    version_name: str = ""
    attributes: dict = {}


class SnapshotOut(BaseModel):
    id: int
    character_id: int
    version_name: str
    attributes: dict
    created_at: datetime
    class Config:
        from_attributes = True


class ReasoningChainCreate(BaseModel):
    title: str = ""
    nodes: list = []
    context_settings: dict = {}


class ReasoningChainOut(BaseModel):
    id: int
    novel_id: int
    title: str
    nodes: list
    context_settings: dict
    class Config:
        from_attributes = True


# ═══════════ Novels ═══════════

@app.get("/api/novels", response_model=list[NovelOut])
def list_novels(db: Session = Depends(get_db)):
    return db.scalars(select(Novel).order_by(Novel.id)).all()

@app.post("/api/novels", response_model=NovelOut)
def create_novel(body: NovelCreate, db: Session = Depends(get_db)):
    n = Novel(title=body.title)
    db.add(n)
    db.commit()
    db.refresh(n)
    return n


@app.put("/api/novels/{novel_id}/template")
def update_novel_template(novel_id: int, body: list = Body(...), db: Session = Depends(get_db)):
    n = db.get(Novel, novel_id)
    if n is None:
        raise HTTPException(404, "小说不存在")
    n.character_template = body  # type: ignore[assignment]
    db.commit()
    return {"ok": True}

@app.get("/api/status")
async def get_status() -> dict[str, str]:
    return {"status": "ok", "message": "Backend is running"}


# ═══════════ Volumes ═══════════

@app.get("/api/volumes", response_model=list[VolumeOut])
def list_volumes(novel_id: int = Query(default=NOVEL_ID_DEFAULT), db: Session = Depends(get_db)):
    return db.scalars(
        select(Volume).where(Volume.novel_id == novel_id).order_by(Volume.order_index)
    ).all()

@app.post("/api/volumes", response_model=VolumeOut)
def create_volume(body: VolumeCreate, novel_id: int = Query(default=NOVEL_ID_DEFAULT), db: Session = Depends(get_db)):
    v = Volume(title=body.title, order_index=body.order_index, novel_id=novel_id)
    db.add(v)
    db.commit()
    db.refresh(v)
    return v


class VolumeUpdate(BaseModel):
    title: str


@app.put("/api/volumes/{volume_id}")
def update_volume(volume_id: int, req: VolumeUpdate, db: Session = Depends(get_db)):
    vol = db.get(Volume, volume_id)
    if vol is None:
        raise HTTPException(404, "分卷不存在")
    vol.title = req.title
    db.commit()
    return {"msg": "分卷已更新"}


@app.delete("/api/volumes/{volume_id}")
def delete_volume(volume_id: int, db: Session = Depends(get_db)):
    v = db.get(Volume, volume_id)
    if v is None:
        raise HTTPException(404, "分卷不存在")
    chapters = db.scalars(select(Chapter).where(Chapter.volume_id == volume_id)).all()
    for ch in chapters:
        db.delete(ch)
    db.delete(v)
    db.commit()
    return {"ok": True}


# ═══════════ Chapters ═══════════

@app.get("/api/chapters", response_model=list[ChapterOut])
def list_chapters(novel_id: int = Query(default=NOVEL_ID_DEFAULT), db: Session = Depends(get_db)):
    return db.scalars(
        select(Chapter).where(Chapter.novel_id == novel_id).order_by(Chapter.order_index)
    ).all()

@app.post("/api/chapters", response_model=ChapterOut)
def create_chapter(body: ChapterCreate, novel_id: int = Query(default=NOVEL_ID_DEFAULT), db: Session = Depends(get_db)):
    ch = Chapter(
        title=body.title, content=body.content,
        order_index=body.order_index, volume_id=body.volume_id, novel_id=novel_id,
    )
    db.add(ch)
    db.commit()
    db.refresh(ch)
    return ch

@app.put("/api/chapters/{chapter_id}", response_model=ChapterOut)
def update_chapter(chapter_id: int, body: ChapterCreate, db: Session = Depends(get_db)):
    ch = db.get(Chapter, chapter_id)
    if ch is None:
        raise HTTPException(404, "章节不存在")
    ch.title = body.title
    ch.content = body.content
    ch.archived_content = body.archived_content
    if body.volume_id is not None:
        ch.volume_id = body.volume_id
    db.commit()
    db.refresh(ch)
    return ch


class ArchiveRequest(BaseModel):
    text_to_archive: str
    api_key: str = ""
    base_url: str = ""
    model: str = "gpt-3.5-turbo"


@app.post("/api/chapters/{chapter_id}/archive")
async def archive_chapter(chapter_id: int, req: ArchiveRequest, db: Session = Depends(get_db)):
    ch = db.get(Chapter, chapter_id)
    if ch is None:
        raise HTTPException(404, "章节不存在")
    text = req.text_to_archive.strip()
    if not text:
        raise HTTPException(400, "归档文本不能为空")

    api_key = req.api_key or LLM_API_KEY
    base_url = req.base_url or LLM_BASE_URL
    model = req.model or LLM_MODEL

    # 1. Move text
    remaining = ch.content.replace(text, "", 1).strip()
    if ch.archived_content:
        ch.archived_content += "\n\n" + text
    else:
        ch.archived_content = text
    ch.content = remaining

    gs = db.get(GlobalSettings, 1)
    templates = gs.prompt_templates if gs else DEFAULT_PROMPT_TEMPLATES

    client = AsyncOpenAI(api_key=api_key, base_url=base_url or None)

    # 2. Timeline summary
    timeline_added = False
    timeline_error: Optional[str] = None
    try:
        tl_resp = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": templates.get("archive_timeline", DEFAULT_PROMPT_TEMPLATES["archive_timeline"]).replace("{text}", text)}],
            temperature=0.5,
        )
        raw_tl = (tl_resp.choices[0].message.content or "").strip()
        time_label = ""
        tl_content = ""
        for line in raw_tl.split("\n"):
            line = line.strip()
            if line.startswith("时间:") or line.startswith("时间："):
                time_label = line.replace("时间:", "").replace("时间：", "").strip()
            elif line.startswith("事件:") or line.startswith("事件："):
                tl_content = line.replace("事件:", "").replace("事件：", "").strip()
        if not tl_content:
            tl_content = raw_tl[:100]
        if not time_label:
            time_label = f"归档 ch{chapter_id}"
        if tl_content:
            max_idx = db.scalar(
                select(func.max(TimelineEvent.timeline_index)).where(
                    TimelineEvent.novel_id == ch.novel_id,
                    TimelineEvent.event_type == TimelineEventType.MAIN_STORY.name,
                )
            )
            new_idx = (max_idx or 0) + 1
            te = TimelineEvent(
                novel_id=ch.novel_id,
                event_type=TimelineEventType.MAIN_STORY.name,  # "MAIN_STORY" string, consistent with frontend query
                timeline_index=new_idx,
                time_label=time_label,
                content=tl_content,
            )
            print(f"[ARCHIVE] TimelineEvent insert: idx={new_idx} type={te.event_type} label={time_label} content={tl_content[:40]}...")
            db.add(te)
            timeline_added = True
    except Exception as e:
        timeline_error = str(e)

    # 3. Character updates
    updated_names: list[str] = []
    character_error: Optional[str] = None
    try:
        novel = db.get(Novel, ch.novel_id)
        char_template = novel.character_template if novel and novel.character_template else []
        chars = db.scalars(select(Character).where(Character.novel_id == ch.novel_id)).all()
        if chars:
            char_list = []
            for c in chars:
                name = c.name
                if c.attributes and isinstance(c.attributes, dict):
                    base = c.attributes.get("基础信息", {})
                    if isinstance(base, dict) and base.get("姓名"):
                        name = str(base["姓名"])
                char_list.append({"id": c.id, "name": name, "status": c.status, "is_active": c.is_active, "attributes": c.attributes or {}})
            char_json = json.dumps(char_list, ensure_ascii=False)
            tmpl_json = json.dumps(char_template, ensure_ascii=False)
            prompt = templates.get("archive_character", DEFAULT_PROMPT_TEMPLATES["archive_character"]).replace("{template}", tmpl_json).replace("{characters}", char_json).replace("{text}", text)
            cu_resp = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
            )
            raw = (cu_resp.choices[0].message.content or "").strip()
            # Robust JSON extraction
            m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", raw, re.DOTALL)
            if m:
                raw = m.group(1).strip()
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                for upd in parsed:
                    cid = upd.get("character_id")
                    u = upd.get("updates", {})
                    if cid:
                        target = db.get(Character, cid)
                        if target:
                            old_attrs = copy.deepcopy(target.attributes or {})
                            db.add(CharacterSnapshot(character_id=cid, version_name="自动归档前快照", attributes=old_attrs))
                            # Merge attributes
                            if u and isinstance(u, dict):
                                merged = copy.deepcopy(target.attributes or {})
                                for group, fields in u.items():
                                    if isinstance(fields, dict):
                                        merged.setdefault(group, {})
                                        if isinstance(merged[group], dict):
                                            for k, v in fields.items():
                                                merged[group][k] = str(v)
                                target.attributes = merged
                                flag_modified(target, "attributes")
                            # Handle status_change and is_active
                            if upd.get("status_change"):
                                target.status = str(upd["status_change"])
                            if "is_active" in upd:
                                target.is_active = bool(upd["is_active"])
                            name = target.name
                            if target.attributes and isinstance(target.attributes, dict):
                                base = target.attributes.get("基础信息", {})
                                if isinstance(base, dict) and base.get("姓名"):
                                    name = str(base["姓名"])
                            updated_names.append(name)
    except Exception as e:
        character_error = str(e)

    db.commit()
    return {
        "message": "归档完成",
        "timeline_added": timeline_added,
        "timeline_error": timeline_error,
        "characters_updated": updated_names,
        "character_error": character_error,
    }


# ═══════════ Outlines ═══════════

@app.get("/api/outlines", response_model=list[OutlineOut])
def list_outlines(novel_id: int = Query(default=NOVEL_ID_DEFAULT), db: Session = Depends(get_db)):
    return db.scalars(
        select(Outline).where(Outline.novel_id == novel_id).order_by(Outline.order_index)
    ).all()

@app.post("/api/outlines", response_model=OutlineOut)
def create_outline(body: OutlineCreate, novel_id: int = Query(default=NOVEL_ID_DEFAULT), db: Session = Depends(get_db)):
    o = Outline(
        title=body.title, description=body.description, category=body.category,
        parent_id=body.parent_id, order_index=body.order_index, novel_id=novel_id,
        is_always_context=body.is_always_context,
    )
    db.add(o)
    db.commit()
    db.refresh(o)
    return o

@app.put("/api/outlines/{outline_id}", response_model=OutlineOut)
def update_outline(outline_id: int, body: OutlineCreate, db: Session = Depends(get_db)):
    o = db.get(Outline, outline_id)
    if o is None:
        raise HTTPException(404, "大纲不存在")
    o.title = body.title
    o.description = body.description
    o.category = body.category
    o.is_always_context = body.is_always_context
    if body.parent_id is not None:
        o.parent_id = body.parent_id
    db.commit()
    db.refresh(o)
    return o


# ═══════════ Characters ═══════════

@app.get("/api/characters", response_model=list[CharacterOut])
def list_characters(novel_id: int = Query(default=NOVEL_ID_DEFAULT), db: Session = Depends(get_db)):
    return db.scalars(
        select(Character).where(Character.novel_id == novel_id).order_by(Character.id)
    ).all()


@app.get("/api/characters/{char_id}", response_model=CharacterOut)
def get_character(char_id: int, db: Session = Depends(get_db)):
    c = db.get(Character, char_id)
    if c is None:
        raise HTTPException(404, "人物不存在")
    return c

@app.post("/api/characters", response_model=CharacterOut)
def create_character(body: CharacterCreate, novel_id: int = Query(default=NOVEL_ID_DEFAULT), db: Session = Depends(get_db)):
    c = Character(
        name=body.name, aliases=body.aliases,
        description=body.description, status=body.status, novel_id=novel_id,
        attributes=body.attributes, is_always_context=body.is_always_context,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


@app.put("/api/characters/{char_id}", response_model=CharacterOut)
def update_character(char_id: int, body: CharacterCreate, db: Session = Depends(get_db)):
    c = db.get(Character, char_id)
    if c is None:
        raise HTTPException(404, "人物不存在")
    c.name = body.name
    c.aliases = body.aliases
    c.description = body.description
    c.status = body.status
    c.is_always_context = body.is_always_context
    c.attributes = body.attributes
    db.commit()
    db.refresh(c)
    return c


# ═══════════ Locations ═══════════

@app.get("/api/locations", response_model=list[LocationOut])
def list_locations(
    novel_id: int = Query(default=NOVEL_ID_DEFAULT),
    parent_id: Optional[int] = Query(default=None),
    filter_null_parent: bool = Query(default=False),
    scale_level: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    stmt = select(Location).where(Location.novel_id == novel_id)
    if parent_id is not None:
        stmt = stmt.where(Location.parent_id == parent_id)
    elif filter_null_parent:
        stmt = stmt.where(Location.parent_id.is_(None))
    if scale_level is not None:
        stmt = stmt.where(Location.scale_level == scale_level)
    results = db.scalars(stmt.order_by(Location.id)).all()
    return [enrich_location_out(loc, db) for loc in results]

@app.post("/api/locations", response_model=LocationOut)
def create_location(body: LocationCreate, novel_id: int = Query(default=NOVEL_ID_DEFAULT), db: Session = Depends(get_db)):
    loc = Location(
        name=body.name, description=body.description,
        parent_id=body.parent_id, scale_level=body.scale_level,
        scale=body.scale,  # type: ignore[arg-type]
        grid_x=body.grid_x, grid_y=body.grid_y,
        attributes=body.attributes, novel_id=novel_id,
    )
    db.add(loc)
    db.commit()
    db.refresh(loc)
    return loc

@app.put("/api/locations/{location_id}", response_model=LocationOut)
def update_location(location_id: int, body: LocationCreate, db: Session = Depends(get_db)):
    loc = db.get(Location, location_id)
    if loc is None:
        raise HTTPException(404, "地点不存在")
    loc.name = body.name
    loc.description = body.description
    if body.parent_id is not None:
        loc.parent_id = body.parent_id
    if body.grid_x is not None:
        loc.grid_x = body.grid_x
    if body.grid_y is not None:
        loc.grid_y = body.grid_y
    if body.attributes is not None:
        loc.attributes = body.attributes
        flag_modified(loc, "attributes")
    db.commit()
    db.refresh(loc)
    return loc


def get_computed_attributes(location_id: int, db: Session, _depth: int = 0) -> dict:
    """Recursively merge parent attributes with child overrides."""
    if _depth > 10:
        return {}
    loc = db.get(Location, location_id)
    if loc is None:
        return {}
    own = dict(loc.attributes or {})
    if loc.parent_id is None:
        return own
    parent_computed = get_computed_attributes(loc.parent_id, db, _depth + 1)
    merged = dict(parent_computed)
    merged.update(own)
    return merged


def enrich_location_out(loc, db: Session) -> dict:
    try:
        comp = get_computed_attributes(loc.id, db)
    except Exception:
        comp = {}
    d = {
        "id": loc.id, "name": loc.name, "description": loc.description,
        "parent_id": loc.parent_id, "scale_level": loc.scale_level,
        "scale": loc.scale.value if hasattr(loc.scale, "value") else str(loc.scale),
        "grid_x": loc.grid_x, "grid_y": loc.grid_y,
        "attributes": loc.attributes or {},
        "computed_attributes": comp,
        "novel_id": loc.novel_id,
    }
    return d


@app.get("/api/novels/{novel_id}/location_templates")
def get_location_templates(novel_id: int, db: Session = Depends(get_db)):
    novel = db.get(Novel, novel_id)
    if novel is None:
        raise HTTPException(404, "小说不存在")
    return novel.location_templates or {}


@app.put("/api/novels/{novel_id}/location_templates")
def update_location_templates(novel_id: int, body: dict, db: Session = Depends(get_db)):
    novel = db.get(Novel, novel_id)
    if novel is None:
        raise HTTPException(404, "小说不存在")
    novel.location_templates = body
    db.commit()
    return {"ok": True}


@app.get("/api/novels/{novel_id}/export")
def export_novel(novel_id: int, db: Session = Depends(get_db)):
    novel = db.get(Novel, novel_id)
    if novel is None:
        raise HTTPException(404, "小说不存在")

    lines: list[str] = [f"# {novel.title}", "", f"生成时间: {datetime.utcnow().isoformat()}", ""]

    # Chapters
    chapters = db.scalars(select(Chapter).where(Chapter.novel_id == novel_id).order_by(Chapter.order_index)).all()
    if chapters:
        lines.append("---")
        lines.append("## 📖 正文")
        for ch in chapters:
            lines.append(f"### {ch.title or '(无标题)'}")
            if ch.content:
                lines.append(ch.content)
            if ch.archived_content:
                lines.append(f"\n> *已归档内容:*\n{ch.archived_content}")
            lines.append("")

    # Locations
    locs = db.scalars(select(Location).where(Location.novel_id == novel_id).order_by(Location.id)).all()
    if locs:
        lines.append("---")
        lines.append("## 🗺️ 世界观·地点")
        for lc in locs:
            try:
                comp = get_computed_attributes(lc.id, db)
            except Exception:
                comp = {}
            lines.append(f"### {lc.name} ({lc.scale_level or '未定义'})")
            if lc.description:
                lines.append(f"*{lc.description}*")
            if comp:
                for k, v in comp.items():
                    if v:
                        lines.append(f"- **{k}**: {v}")
            lines.append("")

    # Characters
    chars = db.scalars(select(Character).where(Character.novel_id == novel_id)).all()
    if chars:
        lines.append("---")
        lines.append("## 👤 世界观·人物")
        for c in chars:
            lines.append(f"### {c.name}")
            if c.aliases:
                lines.append(f"别名: {c.aliases}")
            if c.description:
                lines.append(f"*{c.description}*")
            lines.append(f"状态: {c.status}")
            if c.attributes and isinstance(c.attributes, dict):
                for group, fields in c.attributes.items():
                    if isinstance(fields, dict):
                        for k, v in fields.items():
                            if v:
                                lines.append(f"- {group}.{k}: {v}")
            lines.append("")

    # Outlines
    outlines = db.scalars(select(Outline).where(Outline.novel_id == novel_id).order_by(Outline.order_index)).all()
    if outlines:
        lines.append("---")
        lines.append("## 📋 大纲")
        for o in outlines:
            lines.append(f"### {o.title} ({o.category})")
            if o.description:
                lines.append(o.description)
            lines.append("")

    # Timeline (use raw SQL to avoid enum conversion errors)
    try:
        from sqlalchemy import text as sa_text
        rows = db.execute(sa_text(
            "SELECT event_type, time_label, content FROM timeline_events WHERE novel_id = :nid ORDER BY timeline_index"
        ), {"nid": novel_id}).fetchall()
        type_map = {"main_story": ("正文时间线", "MAIN_STORY"), "history": ("历史时间线", "HISTORY"), "world": ("世界时间线", "WORLD")}
        for et_key, (title, _) in type_map.items():
            filtered = [r for r in rows if r[0] in (et_key, et_key.upper())]
            lines.append("---")
            lines.append(f"## 🕐 {title}")
            if filtered:
                for row in filtered:
                    label = row[1] or ""
                    content = row[2] or ""
                    lines.append(f"- **{label}**: {content}")
            else:
                lines.append("*(暂无事件)*")
            lines.append("")
    except Exception:
        lines.append("---")
        lines.append("## 🕐 时间线 (导出失败)")
        lines.append("")

    md = "\n".join(lines)
    return Response(content=md, media_type="text/markdown; charset=utf-8")


@app.get("/api/novels/{novel_id}/export-zip")
def export_novel_zip(novel_id: int, db: Session = Depends(get_db)):
    novel = db.get(Novel, novel_id)
    if novel is None:
        raise HTTPException(404, "小说不存在")

    volumes = db.query(Volume).filter(Volume.novel_id == novel_id).order_by(Volume.id).all()
    chapters = db.query(Chapter).filter(Chapter.novel_id == novel_id).order_by(Chapter.id).all()

    mem_zip = io.BytesIO()
    with zipfile.ZipFile(mem_zip, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        safe_novel = "".join(c for c in novel.title if c not in r'\/:*?"<>|') or "未命名小说"

        vol_map: dict[int, str] = {}
        ch_counters: dict[Optional[int], int] = {None: 1}

        for idx, v in enumerate(volumes, start=1):
            safe_v = "".join(c for c in v.title if c not in r'\/:*?"<>|') or "未命名"
            vol_map[v.id] = f"第{idx}卷_{safe_v}"
            ch_counters[v.id] = 1

        for ch in chapters:
            vid = ch.volume_id
            vol_folder = vol_map.get(vid, "00_未分卷")
            current_ch_num = ch_counters.get(vid, 1)
            ch_counters[vid] = current_ch_num + 1

            safe_ch = "".join(c for c in (ch.title or "无标题") if c not in r'\/:*?"<>|') or "无标题"
            file_path = f"{safe_novel}/{vol_folder}/第{current_ch_num}章_{safe_ch}.txt"
            zf.writestr(file_path, ch.content or "")

    mem_zip.seek(0)
    encoded_name = urllib.parse.quote(f"{safe_novel}的底稿.zip")
    return StreamingResponse(
        mem_zip,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}"},
    )


# ═══════════ WorldEvents ═══════════

@app.get("/api/events", response_model=list[EventOut])
def list_events(novel_id: int = Query(default=NOVEL_ID_DEFAULT), db: Session = Depends(get_db)):
    return db.scalars(
        select(WorldEvent).where(WorldEvent.novel_id == novel_id).order_by(WorldEvent.timeline_index)
    ).all()

@app.post("/api/events", response_model=EventOut)
def create_event(body: EventCreate, novel_id: int = Query(default=NOVEL_ID_DEFAULT), db: Session = Depends(get_db)):
    e = WorldEvent(
        title=body.title, description=body.description,
        event_type=body.event_type,  # type: ignore[arg-type]
        timeline_index=body.timeline_index, location_id=body.location_id, novel_id=novel_id,
    )
    db.add(e)
    db.commit()
    db.refresh(e)
    return e


# ═══════════ TimelineEvents ═══════════

@app.get("/api/timeline", response_model=list[TimelineOut])
def list_timeline(
    novel_id: int = Query(default=NOVEL_ID_DEFAULT),
    event_type: str = Query(default=""),
    db: Session = Depends(get_db),
):
    stmt = select(TimelineEvent).where(TimelineEvent.novel_id == novel_id)
    if event_type:
        stmt = stmt.where(func.upper(TimelineEvent.event_type) == event_type.upper())  # type: ignore[arg-type]
    return db.scalars(stmt.order_by(TimelineEvent.timeline_index)).all()

@app.post("/api/timeline", response_model=TimelineOut)
def create_timeline(body: TimelineCreate, novel_id: int = Query(default=NOVEL_ID_DEFAULT), db: Session = Depends(get_db)):
    t = TimelineEvent(
        event_type=body.event_type,  # type: ignore[arg-type]
        time_label=body.time_label, content=body.content,
        related_location_id=body.related_location_id, novel_id=novel_id,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


# ═══════════ Snapshots ═══════════

@app.get("/api/characters/{char_id}/snapshots", response_model=list[SnapshotOut])
def list_snapshots(char_id: int, db: Session = Depends(get_db)):
    return db.scalars(
        select(CharacterSnapshot).where(CharacterSnapshot.character_id == char_id).order_by(CharacterSnapshot.created_at.desc())
    ).all()

@app.post("/api/characters/{char_id}/snapshots", response_model=SnapshotOut)
def create_snapshot(char_id: int, body: SnapshotCreate, db: Session = Depends(get_db)):
    s = CharacterSnapshot(character_id=char_id, version_name=body.version_name, attributes=body.attributes)
    db.add(s)
    db.commit()
    db.refresh(s)
    return s

@app.delete("/api/snapshots/{snap_id}")
def delete_snapshot(snap_id: int, db: Session = Depends(get_db)):
    s = db.get(CharacterSnapshot, snap_id)
    if s is None:
        raise HTTPException(404, "快照不存在")
    db.delete(s)
    db.commit()
    return {"ok": True}


# ═══════════ ReasoningChains ═══════════

@app.get("/api/reasoning_chains", response_model=list[ReasoningChainOut])
def list_reasoning_chains(novel_id: int = Query(default=NOVEL_ID_DEFAULT), db: Session = Depends(get_db)):
    return db.scalars(select(ReasoningChain).where(ReasoningChain.novel_id == novel_id).order_by(ReasoningChain.id)).all()

@app.get("/api/reasoning_chains/{chain_id}", response_model=ReasoningChainOut)
def get_reasoning_chain(chain_id: int, db: Session = Depends(get_db)):
    rc = db.get(ReasoningChain, chain_id)
    if rc is None:
        raise HTTPException(404, "推演方案不存在")
    return rc

@app.post("/api/reasoning_chains", response_model=ReasoningChainOut)
def create_reasoning_chain(body: ReasoningChainCreate, novel_id: int = Query(default=NOVEL_ID_DEFAULT), db: Session = Depends(get_db)):
    rc = ReasoningChain(title=body.title, nodes=body.nodes, context_settings=body.context_settings, novel_id=novel_id)
    db.add(rc)
    db.commit()
    db.refresh(rc)
    return rc

@app.put("/api/reasoning_chains/{chain_id}", response_model=ReasoningChainOut)
def update_reasoning_chain(chain_id: int, body: ReasoningChainCreate, db: Session = Depends(get_db)):
    rc = db.get(ReasoningChain, chain_id)
    if rc is None:
        raise HTTPException(404, "推演方案不存在")
    rc.title = body.title
    rc.nodes = body.nodes
    rc.context_settings = body.context_settings
    db.commit()
    db.refresh(rc)
    return rc

@app.delete("/api/reasoning_chains/{chain_id}")
def delete_reasoning_chain(chain_id: int, db: Session = Depends(get_db)):
    rc = db.get(ReasoningChain, chain_id)
    if rc is None:
        raise HTTPException(404, "推演方案不存在")
    db.delete(rc)
    db.commit()
    return {"ok": True}


# ═══════════ Delete endpoints ═══════════

@app.delete("/api/chapters/{chapter_id}")
def delete_chapter(chapter_id: int, db: Session = Depends(get_db)):
    ch = db.get(Chapter, chapter_id)
    if ch is None:
        raise HTTPException(404, "章节不存在")
    db.delete(ch)
    db.commit()
    return {"ok": True}

@app.delete("/api/outlines/{outline_id}")
def delete_outline(outline_id: int, db: Session = Depends(get_db)):
    o = db.get(Outline, outline_id)
    if o is None:
        raise HTTPException(404, "大纲不存在")
    db.delete(o)
    db.commit()
    return {"ok": True}

@app.delete("/api/locations/{location_id}")
def delete_location(location_id: int, db: Session = Depends(get_db)):
    loc = db.get(Location, location_id)
    if loc is None:
        raise HTTPException(404, "地点不存在")
    db.delete(loc)
    db.commit()
    return {"ok": True}

@app.delete("/api/events/{event_id}")
def delete_event(event_id: int, db: Session = Depends(get_db)):
    e = db.get(WorldEvent, event_id)
    if e is None:
        raise HTTPException(404, "事件不存在")
    db.delete(e)
    db.commit()
    return {"ok": True}

@app.delete("/api/timeline/{event_id}")
def delete_timeline(event_id: int, db: Session = Depends(get_db)):
    t = db.get(TimelineEvent, event_id)
    if t is None:
        raise HTTPException(404, "时间线事件不存在")
    db.delete(t)
    db.commit()
    return {"ok": True}

@app.delete("/api/characters/{char_id}")
def delete_character(char_id: int, db: Session = Depends(get_db)):
    c = db.get(Character, char_id)
    if c is None:
        raise HTTPException(404, "人物不存在")
    db.delete(c)
    db.commit()
    return {"ok": True}


# ═══════════ AI ═══════════

class AIChatRequest(BaseModel):
    messages: list[dict[str, str]]
    api_key: str
    base_url: str = ""
    model: str = "gpt-3.5-turbo"

@app.post("/api/ai/stream")
async def ai_stream(req: AIChatRequest):
    async def gen():
        try:
            client = AsyncOpenAI(api_key=req.api_key, base_url=req.base_url or None)
            stream = await client.chat.completions.create(
                model=req.model, messages=req.messages, stream=True,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta
                if delta.content:
                    yield delta.content
        except Exception as e:
            yield f"[ERROR] {e}"
    return StreamingResponse(gen(), media_type="text/plain")


def get_world_lore(db: Session, novel_id: int) -> str:
    parts: list[str] = []
    chars = db.scalars(
        select(Character).where(Character.novel_id == novel_id).order_by(Character.id)
    ).all()
    if chars:
        lines = ["## 人物列表"]
        for c in chars:
            pc = [f"- **{c.name}**"]
            if c.aliases:
                pc.append(f"({c.aliases})")
            pc.append(f"[{c.status}]")
            if c.description:
                pc.append(f": {c.description}")
            lines.append("".join(pc))
        parts.append("\n".join(lines))
    locs = db.scalars(
        select(Location).where(Location.novel_id == novel_id).order_by(Location.id)
    ).all()
    if locs:
        lines = ["## 地点列表"]
        for loc in locs:
            e = f"- **{loc.name}** [{loc.scale}]"
            if loc.grid_x is not None:
                e += f" @({loc.grid_x},{loc.grid_y})"
            if loc.description:
                e += f": {loc.description}"
            lines.append(e)
        parts.append("\n".join(lines))
    events = db.scalars(
        select(WorldEvent).where(WorldEvent.novel_id == novel_id).order_by(WorldEvent.timeline_index)
    ).all()
    if events:
        lines = ["## 世界事件轴"]
        for ev in events:
            lines.append(f"- [#{ev.timeline_index}] **{ev.title}** [{ev.event_type}]")
            if ev.description:
                lines.append(f"  {ev.description}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts) if parts else ""


class AIReasoningRequest(BaseModel):
    api_key: str
    base_url: str = ""
    model: str = "gpt-3.5-turbo"
    content: str = ""

@app.post("/api/ai/reasoning")
async def ai_reasoning(req: AIReasoningRequest, novel_id: int = Query(default=NOVEL_ID_DEFAULT), db: Session = Depends(get_db)):
    world_lore = get_world_lore(db, novel_id)

    async def gen():
        import json
        client = AsyncOpenAI(api_key=req.api_key, base_url=req.base_url or None)
        yield f"data: {json.dumps({'type':'status','message':'Step 1/2'})}\n\n"

        sp = (
            f"世界观:\n{world_lore}\n\n正文:\n{req.content}\n\n"
            '返回JSON: {"is_combat":bool,"selected_character_name":"str","reasoning":"str"}'
        )
        try:
            resp = await client.chat.completions.create(
                model=req.model,
                messages=[{"role": "user", "content": sp}],
                temperature=0.7,
            )
            raw = (resp.choices[0].message.content or "{}").strip()
            if raw.startswith("```"):
                raw = raw.strip("`").removeprefix("json").strip()
            d = json.loads(raw)
        except Exception:
            d = {"is_combat": False, "selected_character_name": "无", "reasoning": "解析失败"}
        yield f"data: {json.dumps({'type':'step_result','step':1,'data':d})}\n\n"

        yield f"data: {json.dumps({'type':'status','message':'Step 2/2'})}\n\n"

        sp2 = (
            f"推演:{d.get('reasoning','')}\n角色:{d.get('selected_character_name','')}\n"
            f"{world_lore}\n正文:{req.content}\n续写300字:"
        )
        try:
            stream = await client.chat.completions.create(
                model=req.model,
                messages=[{"role": "user", "content": sp2}],
                temperature=0.8,
                stream=True,
            )
            async for c in stream:
                delta = c.choices[0].delta
                if delta.content:
                    yield f"data: {json.dumps({'type':'text_chunk','content':delta.content})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type':'error','message':str(e)})}\n\n"

        yield f"data: {json.dumps({'type':'done'})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


# ═══════════ Reasoning Execution ═══════════

class ReasoningExecuteRequest(BaseModel):
    novel_id: int
    premise: str = ""
    prompt: str = ""
    target: str = ""
    previous_output: str = ""
    api_key: str = ""
    base_url: str = ""
    model: str = "gpt-3.5-turbo"


async def async_llm_stream(messages: list[dict], api_key: str, base_url: str, model: str) -> StreamingResponse:
    async def gen():
        try:
            client = AsyncOpenAI(api_key=api_key, base_url=base_url or None)
            stream = await client.chat.completions.create(
                model=model, messages=messages, temperature=0.8, stream=True,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta
                if delta.content:
                    yield f"data: {json.dumps({'type':'chunk','content':delta.content})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type':'error','message':str(e)})}\n\n"
        yield f"data: {json.dumps({'type':'done'})}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/reasoning/execute")
async def reasoning_execute(req: ReasoningExecuteRequest, db: Session = Depends(get_db)):
    system_text = resolve_placeholders(
        "你是小说推演引擎，严格按用户指令输出。\n{outlines_list}\n{characters_list}",
        req.novel_id, db,
    )
    system_msg = f"【解析系统已生效】：用户提供的文本中可能包含数据库检索结果。请依据这些精准设定的事实基础进行推演，保持逻辑严密。\n\n{system_text}"

    user_parts = []
    if req.previous_output:
        user_parts.append(f"[前置内容/上文]:\n{req.previous_output}")
    if req.premise:
        user_parts.append(f"[当前情景]:\n{resolve_placeholders(req.premise, req.novel_id, db)}")
    if req.prompt:
        user_parts.append(f"[推演指令]:\n{req.prompt}")
    if req.target:
        user_parts.append(f"[输出要求]:\n{req.target}")

    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]
    return await async_llm_stream(messages, req.api_key or LLM_API_KEY, req.base_url or LLM_BASE_URL, req.model or LLM_MODEL)


def resolve_placeholders(text: str, novel_id: int, db: Session) -> str:
    """Replace all {placeholder} patterns with database content."""

    def _fmt_char(c) -> str:
        name = c.name
        if c.attributes and isinstance(c.attributes, dict):
            base = c.attributes.get("基础信息", {})
            if isinstance(base, dict) and base.get("姓名"):
                name = str(base["姓名"])
        parts: list[str] = [f"- **{name}** [{c.status}] {'🏃活跃' if c.is_active else '🚪退场'}"]
        if c.aliases:
            parts.append(f"  别名: {c.aliases}")
        if c.description:
            parts.append(f"  描述: {c.description}")
        if c.attributes and isinstance(c.attributes, dict):
            for group, fields in c.attributes.items():
                if isinstance(fields, dict):
                    for k, v in fields.items():
                        if v:
                            parts.append(f"  {group}.{k}: {v}")
        return "\n".join(parts)

    def _fmt_loc(lc) -> str:
        sc = lc.scale.value if hasattr(lc.scale, "value") else str(lc.scale)
        line = f"- **{lc.name}** [{sc}]"
        if lc.description:
            line += f": {lc.description}"
        comp = get_computed_attributes(lc.id, db)
        if comp:
            for k, v in comp.items():
                if v:
                    line += f" | [{k}]: {v}"
        return line

    def _lore() -> str:
        outlines = db.scalars(select(Outline).where(
            Outline.novel_id == novel_id, Outline.is_always_context.is_(True)
        )).all()
        parts: list[str] = []
        if outlines:
            lines = ["## 常驻大纲"]
            for o in outlines:
                lines.append(f"- **{o.title}** [{o.category}]: {o.description}")
            parts.append("\n".join(lines))
        chars = db.scalars(select(Character).where(
            Character.novel_id == novel_id, Character.is_always_context.is_(True)
        )).all()
        if chars:
            lines = ["## 常驻人物"]
            for c in chars:
                lines.append(_fmt_char(c))
            parts.append("\n".join(lines))
        locs = db.scalars(select(Location).where(Location.novel_id == novel_id)).all()
        if locs:
            lines = ["## 地点列表"]
            for lc in locs:
                lines.append(_fmt_loc(lc))
            parts.append("\n".join(lines))
        for et, label in [("history", "历史时间线"), ("world", "世界时间线")]:
            events = db.scalars(select(TimelineEvent).where(
                TimelineEvent.novel_id == novel_id,
                TimelineEvent.event_type == et,
            ).order_by(TimelineEvent.time_label)).all()
            if events:
                lines = [f"## {label}"]
                for e in events:
                    lines.append(f"- **{e.time_label}**: {e.content}")
                parts.append("\n".join(lines))
        return "\n\n".join(parts) if parts else "(无上下文)"

    def _rep(match):
        ph = match.group(1)

        if ph == "outlines_list":
            outlines = db.scalars(select(Outline).where(Outline.novel_id == novel_id)).all()
            if not outlines:
                return "(无大纲)"
            lines = ["## 大纲目录"]
            for o in outlines:
                lines.append(f"- **{o.title}** [{o.category}]: {o.description}")
            return "\n".join(lines)

        if ph == "characters_list":
            chars = db.scalars(select(Character).where(Character.novel_id == novel_id)).all()
            if not chars:
                return "(无人物)"
            lines = ["## 人物名录"]
            for c in chars:
                name = c.name
                if c.attributes and isinstance(c.attributes, dict):
                    base = c.attributes.get("基础信息", {})
                    if isinstance(base, dict) and base.get("姓名"):
                        name = str(base["姓名"])
                active_tag = "🏃活跃" if c.is_active else "🚪退场"
                lines.append(f"- **{name}** [{c.status}] {active_tag}")
            return "\n".join(lines)

        if ph == "locations_list":
            locs = db.scalars(select(Location).where(Location.novel_id == novel_id)).all()
            if not locs:
                return "(无地点)"
            lines = ["## 地点列表"]
            for lc in locs:
                sc = lc.scale.value if hasattr(lc.scale, "value") else str(lc.scale)
                lines.append(f"- **{lc.name}** [{sc}]")
            return "\n".join(lines)

        if ph == "timeline_history":
            events = db.scalars(select(TimelineEvent).where(
                TimelineEvent.novel_id == novel_id,
                TimelineEvent.event_type == "history",
            ).order_by(TimelineEvent.time_label)).all()
            if not events:
                return "(无历史时间线)"
            lines = ["## 历史时间线"]
            for e in events:
                lines.append(f"- **{e.time_label}**: {e.content}")
            return "\n".join(lines)

        if ph == "timeline_world":
            events = db.scalars(select(TimelineEvent).where(
                TimelineEvent.novel_id == novel_id,
                TimelineEvent.event_type == "world",
            ).order_by(TimelineEvent.time_label)).all()
            if not events:
                return "(无世界时间线)"
            lines = ["## 世界时间线"]
            for e in events:
                lines.append(f"- **{e.time_label}**: {e.content}")
            return "\n".join(lines)

        if ph == "timeline_main":
            events = db.scalars(select(TimelineEvent).where(
                TimelineEvent.novel_id == novel_id,
                TimelineEvent.event_type == "main_story",
            ).order_by(TimelineEvent.time_label)).all()
            if not events:
                return "(无正文时间线)"
            lines = ["## 正文时间线"]
            for e in events:
                lines.append(f"- **{e.time_label}**: {e.content}")
            return "\n".join(lines)

        if ph == "lore":
            return _lore()

        m = re.match(r"character:(.+)$", ph)
        if m:
            name = m.group(1).strip()
            chars = db.scalars(select(Character).where(Character.novel_id == novel_id)).all()
            for c in chars:
                if c.name == name or name in (c.aliases or ""):
                    info = [f"## 人物: {c.name}"]
                    if c.aliases:
                        info.append(f"别名: {c.aliases}")
                    if c.description:
                        info.append(f"描述: {c.description}")
                    info.append(f"状态: {c.status}")
                    info.append(f"活跃: {'是' if c.is_active else '否（已退场）'}")
                    if c.attributes and isinstance(c.attributes, dict):
                        for group, fields in c.attributes.items():
                            if isinstance(fields, dict):
                                for k, v in fields.items():
                                    if v:
                                        info.append(f"{group}.{k}: {v}")
                    return "\n".join(info)
            return f"(未找到人物: {name})"

        m = re.match(r"outline:(.+)$", ph)
        if m:
            title = m.group(1).strip()
            outlines = db.scalars(select(Outline).where(Outline.novel_id == novel_id, Outline.title == title)).all()
            if outlines:
                o = outlines[0]
                return f"## 大纲: {o.title}\n分类: {o.category}\n{o.description}"
            return f"(未找到大纲: {title})"

        m = re.match(r"location:(.+)$", ph)
        if m:
            name = m.group(1).strip()
            locs = db.scalars(select(Location).where(Location.novel_id == novel_id, Location.name == name)).all()
            if locs:
                lc = locs[0]
                sc = lc.scale.value if hasattr(lc.scale, "value") else str(lc.scale)
                info = [f"## 地点: {lc.name}", f"尺度: {sc}"]
                if lc.description:
                    info.append(f"描述: {lc.description}")
                comp = get_computed_attributes(lc.id, db)
                if comp:
                    for k, v in comp.items():
                        if v:
                            info.append(f"[{k}]: {v}")
                return "\n".join(info)
            return f"(未找到地点: {name})"

        return match.group(0)

    return re.sub(r"\{(\w+(?::[^}]+)?)\}", _rep, text)


# ═══════════ Settings ═══════════

@app.get("/api/settings/prompts")
def get_prompts(db: Session = Depends(get_db)):
    gs = db.get(GlobalSettings, 1)
    if gs is None:
        gs = GlobalSettings(id=1, prompt_templates=DEFAULT_PROMPT_TEMPLATES)
        db.add(gs)
    merged = dict(gs.prompt_templates or {})
    changed = False
    for k, v in DEFAULT_PROMPT_TEMPLATES.items():
        if k not in merged:
            merged[k] = v
            changed = True
    # Force-update if sandbox_sim is missing {content} (old format migration)
    if "{content}" not in merged.get("sandbox_sim", ""):
        merged["sandbox_sim"] = DEFAULT_PROMPT_TEMPLATES["sandbox_sim"]
        changed = True
    if changed:
        gs.prompt_templates = merged
    db.commit()
    db.refresh(gs)
    return gs.prompt_templates

@app.put("/api/settings/prompts")
def update_prompts(body: dict, db: Session = Depends(get_db)):
    gs = db.get(GlobalSettings, 1)
    if gs is None:
        gs = GlobalSettings(id=1, prompt_templates=body)
        db.add(gs)
    else:
        gs.prompt_templates = body
    db.commit()
    return {"ok": True}


@app.get("/api/settings/prompts/defaults")
def get_default_prompts():
    return DEFAULT_PROMPT_TEMPLATES


# ═══════════ Writing Copilot ═══════════

class CopilotRequest(BaseModel):
    novel_id: int
    current_chapter_content: str = ""
    selected_text: str = ""
    instruction: str = ""
    api_key: str = ""
    base_url: str = ""
    model: str = "gpt-3.5-turbo"
    scene: str = "chat"
    chain_data: str = ""


@app.post("/api/writing/copilot")
async def writing_copilot(req: CopilotRequest, db: Session = Depends(get_db)):
    gs = db.get(GlobalSettings, 1)
    templates = gs.prompt_templates if gs else DEFAULT_PROMPT_TEMPLATES
    tmpl = templates.get(req.scene, templates.get("chat", "{instruction}"))

    prompt = tmpl.replace("{content}", req.current_chapter_content).replace("{selection}", req.selected_text).replace("{instruction}", req.instruction).replace("{chain_data}", req.chain_data)
    prompt = resolve_placeholders(prompt, req.novel_id, db)

    messages = [{"role": "user", "content": prompt}]
    return await async_llm_stream(messages, req.api_key or LLM_API_KEY, req.base_url or LLM_BASE_URL, req.model or LLM_MODEL)


# ═══════════ Static ═══════════

if getattr(sys, "frozen", False):
    frontend_path = os.path.join(sys._MEIPASS, "frontend")  # type: ignore[attr-defined]
else:
    frontend_path = str(Path(__file__).resolve().parent.parent / "frontend")
app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")


# ═══════════ Entry Point ═══════════

def open_browser():
    webbrowser.open("http://127.0.0.1:8000")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    threading.Timer(1.5, open_browser).start()
    uvicorn.run(app, host="127.0.0.1", port=8000)
