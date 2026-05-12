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
        CharacterRoutine, CharacterTrajectory,
        DEFAULT_PROMPT_TEMPLATES, TimelineEventType,
    )
    from .calendar import CalendarEngine  # noqa: E402
except ImportError:
    from database import Base, engine, get_db, run_migration  # noqa: E402
    from models import (  # noqa: E402
        Chapter, Character, Outline, Location,
        WorldEvent, Novel, Volume, TimelineEvent, CharacterSnapshot, ReasoningChain, GlobalSettings,
        CharacterRoutine, CharacterTrajectory,
        DEFAULT_PROMPT_TEMPLATES, TimelineEventType,
    )
    from calendar import CalendarEngine  # noqa: E402

LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-3.5-turbo")

AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "advance_world_time",
            "description": "当小说时间流逝或用户要求推移时间时调用，推进小说的世界时间。",
            "parameters": {
                "type": "object",
                "properties": {
                    "elapsed_days": {"type": "integer", "description": "流逝的天数"},
                    "elapsed_hours": {"type": "integer", "description": "流逝的小时数"},
                },
                "required": ["elapsed_days", "elapsed_hours"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "modify_character",
            "description": "修改指定人物的存活状态或瞬间移动到某个地点。必须使用精确的人物ID和地点ID。",
            "parameters": {
                "type": "object",
                "properties": {
                    "character_id": {"type": "integer", "description": "目标人物ID"},
                    "status": {"type": "string", "description": "当前状态描述(如:重伤、死亡、闭关)"},
                    "is_active": {"type": "boolean", "description": "死亡或永久退场填false"},
                    "location_id": {"type": "integer", "description": "要移动到的地点ID(选填)"},
                    "reason": {"type": "string", "description": "修改原因"},
                },
                "required": ["character_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_world_element",
            "description": "修改除人物外的世界观元素：大纲(outline)、地点(location)、时间线(timeline)。可以新增、更新或删除。",
            "parameters": {
                "type": "object",
                "properties": {
                    "element_type": {"type": "string", "enum": ["outline", "location", "timeline"]},
                    "action": {"type": "string", "enum": ["add", "update", "delete"]},
                    "element_id": {"type": "integer", "description": "更新或删除时必填"},
                    "data": {
                        "type": "object",
                        "description": "新增或更新的数据字段。大纲填 title/description; 地点填 name/description; 时间线填 time_label/content(事件内容)",
                        "additionalProperties": True,
                    },
                },
                "required": ["element_type", "action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_reasoning_chain",
            "description": "根据用户的需求，创建一个新的AI推理链方案，或覆盖已有的推理链。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["create_new", "overwrite_existing"]},
                    "chain_id": {"type": "integer", "description": "如果覆盖现有方案则必填"},
                    "title": {"type": "string", "description": "推理链名称"},
                    "steps": {
                        "type": "array",
                        "description": "推理链的顺序步骤。后端会自动将这些步骤线性连接并分配坐标。",
                        "items": {
                            "type": "object",
                            "properties": {
                                "premise": {"type": "string", "description": "本节点的前置逻辑或目标说明。必须填具体的自然语言，不要使用{占位符}格式。"},
                                "prompt": {"type": "string", "description": "发给AI的提示词命令。必须填具体的自然语言指令，不要使用{占位符}格式。"},
                                "target": {"type": "string", "description": "写入目标的占位符或标识"},
                            },
                            "required": ["premise", "prompt"],
                        },
                    },
                },
                "required": ["action", "title", "steps"],
            },
        },
    },
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    run_migration()
    # Data healing: normalize timeline_events event_type to uppercase
    try:
        from .database import SessionLocal
    except ImportError:
        from database import SessionLocal
    with SessionLocal() as db:
        db.execute(text("UPDATE timeline_events SET event_type = 'MAIN_STORY' WHERE event_type = 'main_story'"))
        db.execute(text("UPDATE timeline_events SET event_type = 'HISTORY' WHERE event_type = 'history'"))
        db.execute(text("UPDATE timeline_events SET event_type = 'WORLD' WHERE event_type = 'world'"))
        db.commit()
    # Prompt auto-heal in independent session
    with SessionLocal() as db2:
        gs = db2.get(GlobalSettings, 1)
        if gs and isinstance(gs.prompt_templates, dict):
            tpl = dict(gs.prompt_templates)
            healed = False
            if "archive_timeline" in tpl and "elapsed_days" not in tpl["archive_timeline"]:
                tpl["archive_timeline"] = DEFAULT_PROMPT_TEMPLATES["archive_timeline"]
                healed = True
            if "archive_character" in tpl and "move_to_location" not in tpl["archive_character"]:
                tpl["archive_character"] = DEFAULT_PROMPT_TEMPLATES["archive_character"]
                healed = True
            if healed:
                gs.prompt_templates = tpl
                flag_modified(gs, "prompt_templates")
                db2.commit()
    yield


app = FastAPI(title="AI 小说辅助引擎", version="0.5.1", lifespan=lifespan)
NOVEL_ID_DEFAULT = 1


# ═══════════ Space-Time Engine ═══════════

def get_character_current_status(char_id: int, current_tick: int, db: Session):
    from sqlalchemy import desc
    # 1. 查找正在发生的特殊轨迹
    traj = db.query(CharacterTrajectory).filter(
        CharacterTrajectory.character_id == char_id,
        CharacterTrajectory.start_tick <= current_tick,
    ).order_by(desc(CharacterTrajectory.start_tick)).first()
    if traj and (traj.end_tick is None or traj.end_tick > current_tick):
        loc = db.get(Location, traj.location_id)
        loc_name = loc.name if loc else "未知地"
        return {"type": "trajectory", "location_id": traj.location_id, "location_name": loc_name, "desc": traj.reason}

    # 2. 无轨迹则时间匹配作息规律
    routines = db.query(CharacterRoutine).filter(
        CharacterRoutine.character_id == char_id
    ).all()
    fallback_routine = None
    char = db.get(Character, char_id)
    novel = db.get(Novel, char.novel_id) if char else None
    config = novel.calendar_config if novel and novel.calendar_config else {}
    for r in routines:
        if not r.cycle_value or str(r.cycle_value).strip() == "常驻":
            fallback_routine = r
        elif CalendarEngine.is_routine_active(current_tick, config, r.cycle_type, r.cycle_value):
            loc = db.get(Location, r.location_id)
            loc_name = loc.name if loc else "未知地"
            return {"type": "routine", "location_id": r.location_id, "location_name": loc_name, "desc": r.activity}
    # 3. 无匹配作息则回退到常驻地
    if fallback_routine:
        loc = db.get(Location, fallback_routine.location_id)
        loc_name = loc.name if loc else "未知地"
        return {"type": "routine_fallback", "location_id": fallback_routine.location_id, "location_name": loc_name, "desc": f"常驻于{loc_name}"}
    return {"type": "unknown", "location_id": None, "location_name": "行踪不明", "desc": ""}


# ═══════════ Schemas ═══════════

class NovelCreate(BaseModel):
    title: str = "新小说"

class NovelOut(BaseModel):
    id: int
    title: str
    character_template: Optional[list] = None
    location_templates: Optional[dict] = None
    calendar_config: Optional[dict] = None
    current_tick: int = 0
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


class NovelTimeUpdate(BaseModel):
    current_tick: int
    calendar_config: dict


@app.get("/api/novels/{novel_id}/time")
def get_novel_time(novel_id: int, db: Session = Depends(get_db)):
    novel = db.get(Novel, novel_id)
    if novel is None:
        raise HTTPException(404, "小说不存在")
    return {"current_tick": novel.current_tick, "calendar_config": novel.calendar_config}


@app.put("/api/novels/{novel_id}/time")
def update_novel_time(novel_id: int, req: NovelTimeUpdate, db: Session = Depends(get_db)):
    novel = db.get(Novel, novel_id)
    if novel is None:
        raise HTTPException(404, "小说不存在")
    novel.current_tick = req.current_tick
    novel.calendar_config = req.calendar_config
    db.commit()
    return {"msg": "OK"}

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
        # Try new JSON object format first, fall back to old "时间:/事件:" text format
        try:
            mj = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw_tl, re.DOTALL)
            if mj:
                parsed_tl = json.loads(mj.group(1))
            else:
                # Maybe it's bare JSON without markdown fences
                parsed_tl = json.loads(raw_tl)
            if isinstance(parsed_tl, dict):
                # New format: {"elapsed_days": X, "elapsed_hours": X, "events": [...]}
                novel = db.get(Novel, ch.novel_id)
                elapsed_days = int(parsed_tl.get("elapsed_days", 0))
                elapsed_hours = int(parsed_tl.get("elapsed_hours", 0))
                config = novel.calendar_config if novel else {}
                hpd = config.get("hours_per_day", 24)
                total_elapsed_ticks = (elapsed_days * hpd) + elapsed_hours
                if total_elapsed_ticks > 0 and novel:
                    novel.current_tick += total_elapsed_ticks
                events_list = parsed_tl.get("events", [])
                for evt in events_list:
                    te = TimelineEvent(
                        novel_id=ch.novel_id,
                        event_type=TimelineEventType.MAIN_STORY.name,
                        timeline_index=((db.scalar(
                            select(func.max(TimelineEvent.timeline_index)).where(
                                TimelineEvent.novel_id == ch.novel_id,
                                TimelineEvent.event_type == TimelineEventType.MAIN_STORY.name,
                            )
                        ) or 0) + 1),
                        time_label=evt.get("time", ""),
                        content=evt.get("event", ""),
                    )
                    db.add(te)
                    timeline_added = True
        except (json.JSONDecodeError, AttributeError):
            # Fallback: old "时间:/事件:" text format
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
                    event_type=TimelineEventType.MAIN_STORY.name,
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
                            # Auto-create trajectory from move_to_location
                            if upd.get("move_to_location") and isinstance(upd["move_to_location"], str) and upd["move_to_location"].strip():
                                loc_name = upd["move_to_location"].strip()
                                loc = db.query(Location).filter(
                                    Location.novel_id == ch.novel_id,
                                    Location.name.like(f"%{loc_name}%"),
                                ).first()
                                if loc:
                                    db.add(CharacterTrajectory(
                                        character_id=target.id,
                                        location_id=loc.id,
                                        start_tick=novel2.current_tick if (novel2 := db.get(Novel, ch.novel_id)) else 0,
                                        reason=f"正文归档：{loc_name}",
                                    ))
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


@app.get("/api/locations/{loc_id}/presence")
def get_location_presence(loc_id: int, novel_id: int, db: Session = Depends(get_db)):
    novel = db.get(Novel, novel_id)
    current_tick = novel.current_tick if novel else 0
    chars = db.query(Character).filter(
        Character.novel_id == novel_id, Character.is_active.is_(True)
    ).all()
    present_chars: list[dict] = []
    for c in chars:
        status = get_character_current_status(c.id, current_tick, db)
        if status["location_id"] == loc_id:
            present_chars.append({"id": c.id, "name": c.name, "status_desc": status["desc"]})
    return present_chars


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


# ═══════════ Character Time Data ═══════════

class RoutineData(BaseModel):
    location_id: int
    cycle_type: str
    cycle_value: str
    activity: str

class TrajectoryData(BaseModel):
    location_id: int
    start_tick: int
    end_tick: Optional[int] = None
    reason: str

class CharacterTimeData(BaseModel):
    routines: list[RoutineData] = []
    trajectories: list[TrajectoryData] = []


@app.get("/api/characters/{char_id}/time_data")
def get_char_time(char_id: int, db: Session = Depends(get_db)):
    routines = db.query(CharacterRoutine).filter_by(character_id=char_id).all()
    trajectories = db.query(CharacterTrajectory).filter_by(character_id=char_id).order_by(CharacterTrajectory.start_tick).all()
    return {
        "routines": [{"location_id": r.location_id, "cycle_type": r.cycle_type, "cycle_value": r.cycle_value, "activity": r.activity} for r in routines],
        "trajectories": [{"location_id": t.location_id, "start_tick": t.start_tick, "end_tick": t.end_tick, "reason": t.reason} for t in trajectories],
    }


@app.put("/api/characters/{char_id}/time_data")
def update_char_time(char_id: int, req: CharacterTimeData, db: Session = Depends(get_db)):
    db.query(CharacterRoutine).filter_by(character_id=char_id).delete()
    db.query(CharacterTrajectory).filter_by(character_id=char_id).delete()
    for r in req.routines:
        db.add(CharacterRoutine(character_id=char_id, location_id=r.location_id, cycle_type=r.cycle_type, cycle_value=r.cycle_value, activity=r.activity))
    for t in req.trajectories:
        db.add(CharacterTrajectory(character_id=char_id, location_id=t.location_id, start_tick=t.start_tick, end_tick=t.end_tick, reason=t.reason))
    db.commit()
    return {"msg": "OK"}

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
    context_settings: dict = {}


async def async_llm_stream(messages: list[dict], api_key: str, base_url: str, model: str, tools: Optional[list[dict]] = None) -> StreamingResponse:
    async def gen():
        tool_calls_acc: dict[int, dict[str, str]] = {}
        try:
            client = AsyncOpenAI(api_key=api_key, base_url=base_url or None)
            extra = {}
            if tools:
                extra = {"tools": tools, "tool_choice": "auto"}
            stream = await client.chat.completions.create(
                model=model, messages=messages, temperature=0.8, stream=True, **extra,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta
                if delta.content:
                    yield f"data: {json.dumps({'type':'chunk','content':delta.content})}\n\n"
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_calls_acc:
                            tool_calls_acc[idx] = {"id": "", "name": "", "arguments": ""}
                        acc = tool_calls_acc[idx]
                        if tc.id:
                            acc["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                acc["name"] += tc.function.name
                            if tc.function.arguments:
                                acc["arguments"] += tc.function.arguments
        except Exception as e:
            yield f"data: {json.dumps({'type':'error','message':str(e)})}\n\n"
        for tc_data in tool_calls_acc.values():
            if tc_data["name"]:
                try:
                    args = json.loads(tc_data["arguments"]) if tc_data["arguments"] else {}
                except json.JSONDecodeError:
                    args = {}
                yield f"data: {json.dumps({'type':'tool_proposal','tool_name':tc_data['name'],'arguments':args,'tool_call_id':tc_data['id']})}\n\n"
        yield f"data: {json.dumps({'type':'done'})}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/reasoning/execute")
async def reasoning_execute(req: ReasoningExecuteRequest, db: Session = Depends(get_db)):
    novel = db.get(Novel, req.novel_id)
    system_text = resolve_placeholders(
        "你是小说推演引擎，严格按用户指令输出。\n{outlines_list}\n{characters_list}",
        req.novel_id, db,
    )
    system_msg = f"【解析系统已生效】：用户提供的文本中可能包含数据库检索结果。请依据这些精准设定的事实基础进行推演，保持逻辑严密。\n\n{system_text}"

    final_prompt = req.prompt
    ctx = req.context_settings or {}
    if ctx.get("use_outlines") or ctx.get("use_characters") or ctx.get("use_timeline"):
        current_tick = novel.current_tick if novel else 0
        cfg = novel.calendar_config if novel else {}
        blocks: list[str] = []
        lore_time = CalendarEngine.format_tick_to_lore_time(current_tick, cfg)
        blocks.append(f"【当前世界时间】: {lore_time}")

        if ctx.get("use_outlines"):
            outlines = db.query(Outline).filter(Outline.novel_id == req.novel_id).order_by(Outline.order_index).all()
            if outlines:
                o_lines = [f"- {o.title}: {o.description}" for o in outlines]
                blocks.append("【世界大纲】:\n" + "\n".join(o_lines))
            else:
                blocks.append("【世界大纲】: (暂无)")

        if ctx.get("use_characters"):
            chars = db.query(Character).filter(Character.novel_id == req.novel_id, Character.is_active).all()
            if chars:
                c_lines: list[str] = []
                for c in chars:
                    status = get_character_current_status(c.id, current_tick, db)
                    identity = ""
                    if c.attributes and isinstance(c.attributes, dict):
                        identity = c.attributes.get("身份", "")
                    c_lines.append(f"- {c.name}: [位于 {status['location_name']}] {identity}")
                blocks.append("【活跃人物与当前坐标】:\n" + "\n".join(c_lines))
            else:
                blocks.append("【活跃人物与当前坐标】: (暂无)")

        if ctx.get("use_timeline"):
            events = db.query(TimelineEvent).filter(TimelineEvent.novel_id == req.novel_id).order_by(TimelineEvent.absolute_tick).all()
            if events:
                e_lines = [f"- [{e.time_label}] {e.content}" for e in events]
                blocks.append("【已发生的时间线事件】:\n" + "\n".join(e_lines))
            else:
                blocks.append("【已发生的时间线事件】: (暂无)")

        context_header = "==== 当前世界状态参考 ====\n" + "\n\n".join(blocks) + "\n==========================\n\n"
        final_prompt = context_header + req.prompt

    user_parts = []
    if req.previous_output:
        user_parts.append(f"[前置内容/上文]:\n{req.previous_output}")
    if req.premise:
        user_parts.append(f"[当前情景]:\n{resolve_placeholders(req.premise, req.novel_id, db)}")
    if final_prompt:
        user_parts.append(f"[推演指令]:\n{final_prompt}")
    if req.target:
        user_parts.append(f"[输出要求]:\n{req.target}")

    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]
    return await async_llm_stream(messages, req.api_key or LLM_API_KEY, req.base_url or LLM_BASE_URL, req.model or LLM_MODEL)


def resolve_placeholders(text: str, novel_id: int, db: Session) -> str:
    """Replace all {placeholder} patterns with database content."""
    novel = db.get(Novel, novel_id)
    current_tick = novel.current_tick if novel else 0

    def _fmt_char(c) -> str:
        name = c.name
        if c.attributes and isinstance(c.attributes, dict):
            base = c.attributes.get("基础信息", {})
            if isinstance(base, dict) and base.get("姓名"):
                name = str(base["姓名"])
        parts: list[str] = [f"- **{name}** (ID:{c.id}) [{c.status}] {'🏃活跃' if c.is_active else '🚪退场'}"]
        status = get_character_current_status(c.id, current_tick, db)
        parts.append(f"  📍 当前位置: {status['location_name']} ({status['desc']})")
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
            # Active + always_context characters (always_context overrides inactive)
            active = [c for c in chars if c.is_active or c.is_always_context]
            if not active:
                return "(无活跃人物)"
            lines = ["## 活跃人物名录"]
            for c in active:
                name = c.name
                if c.attributes and isinstance(c.attributes, dict):
                    base = c.attributes.get("基础信息", {})
                    if isinstance(base, dict) and base.get("姓名"):
                        name = str(base["姓名"])
                active_tag = "🏃活跃" if c.is_active else "🚪退场(常驻)"
                status_info = get_character_current_status(c.id, current_tick, db)
                lines.append(f"- **{name}** [{c.status}] {active_tag} | 📍 {status_info['location_name']} ({status_info['desc']})")
            return "\n".join(lines)

        if ph == "characters_inactive":
            chars = db.scalars(select(Character).where(Character.novel_id == novel_id)).all()
            # Only truly retired AND not always_context
            inactive = [c for c in chars if not c.is_active and not c.is_always_context]
            if not inactive:
                return "(无退场人物)"
            lines = ["## 退场人物名录"]
            for c in inactive:
                name = c.name
                if c.attributes and isinstance(c.attributes, dict):
                    base = c.attributes.get("基础信息", {})
                    if isinstance(base, dict) and base.get("姓名"):
                        name = str(base["姓名"])
                lines.append(f"- **{name}** [{c.status}] 🚪退场")
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

        if ph == "current_time":
            lore_time = CalendarEngine.format_tick_to_lore_time(current_tick, novel.calendar_config if novel else {})
            return f"当前世界时间：{lore_time}"

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
                    status = get_character_current_status(c.id, current_tick, db)
                    info.append(f"📍 当前时空坐标: 位于【{status['location_name']}】({status['desc']})")
                    routines = db.query(CharacterRoutine).filter_by(character_id=c.id).all()
                    if routines:
                        info.append("📅 日常作息规律:")
                        for r in routines:
                            r_loc = db.get(Location, r.location_id)
                            r_loc_name = r_loc.name if r_loc else "未知"
                            info.append(f"  - [{r.cycle_type}{r.cycle_value}] 在 {r_loc_name} ({r.activity})")
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

    messages = [
        {
            "role": "system",
            "content": (
                "你是小说创作助手，集成了世界观编辑工具。\n"
                "当用户要求执行以下操作时，你必须调用对应的工具函数，而不是口头描述：\n"
                "- 推进时间 → 调用 advance_world_time\n"
                "- 修改人物状态/位置/生死 → 调用 modify_character\n"
                "- 增删改大纲/地点/时间线 → 调用 manage_world_element\n"
                "- 创建或覆盖推理方案 → 调用 generate_reasoning_chain\n"
                "创建推理链时，每个步骤的premise和prompt必须填写具体的自然语言指令，例如\"分析秘境危险度\"\"根据已知情报推断敌人的弱点\"。"
                "严禁使用{变量名}、{{占位符}}格式——那会在运行时由系统自动注入，创建链时不需要写。"
                "如果你确认操作已完成，回复时请简要告知用户结果。"
            ),
        },
        {"role": "user", "content": prompt},
    ]
    return await async_llm_stream(messages, req.api_key or LLM_API_KEY, req.base_url or LLM_BASE_URL, req.model or LLM_MODEL, tools=AGENT_TOOLS)


# ═══════════ Agent Tool Execution ═══════════

class ToolExecuteReq(BaseModel):
    tool_name: str
    arguments: dict


@app.post("/api/agent/execute/{novel_id}")
def execute_agent_tool(novel_id: int, req: ToolExecuteReq, db: Session = Depends(get_db)):
    novel = db.get(Novel, novel_id)
    if novel is None:
        raise HTTPException(404, "小说不存在")

    if req.tool_name == "advance_world_time":
        days = int(req.arguments.get("elapsed_days", 0))
        hours = int(req.arguments.get("elapsed_hours", 0))
        cfg = novel.calendar_config or {}
        hpd = cfg.get("hours_per_day", 24)
        added_ticks = (days * hpd) + hours
        novel.current_tick += added_ticks
        db.commit()
        return {"status": "success", "msg": f"世界时间已推进 {days} 天 {hours} 小时"}

    if req.tool_name == "modify_character":
        char_id = req.arguments.get("character_id")
        char = db.get(Character, char_id)
        if char is None:
            return {"status": "error", "msg": "人物不存在"}

        snap = CharacterSnapshot(
            character_id=char.id,
            attributes=copy.deepcopy(char.attributes),
            status=char.status or "",
            is_active=char.is_active,
            timestamp=datetime.utcnow(),
        )
        db.add(snap)

        if "status" in req.arguments:
            char.status = req.arguments["status"]
        if "is_active" in req.arguments:
            char.is_active = req.arguments["is_active"]
        loc_id = req.arguments.get("location_id")
        if loc_id is not None:
            loc = db.get(Location, loc_id)
            if loc:
                db.add(CharacterTrajectory(
                    character_id=char.id,
                    location_id=loc_id,
                    start_tick=novel.current_tick,
                    reason=req.arguments.get("reason", "AI 指令调度"),
                ))
            else:
                return {"status": "error", "msg": f"地点ID {loc_id} 不存在"}
        if char.attributes and isinstance(char.attributes, dict):
            flag_modified(char, "attributes")
        db.commit()
        return {"status": "success", "msg": f"人物 [{char.name}] 状态已更新。"}

    if req.tool_name == "manage_world_element":
        etype = req.arguments.get("element_type")
        action = req.arguments.get("action")
        eid = req.arguments.get("element_id")
        data = req.arguments.get("data", {})

        if etype == "outline":
            if action == "add":
                db.add(Outline(
                    novel_id=novel_id,
                    title=data.get("title", ""),
                    description=data.get("description", ""),
                    category=data.get("category", "剧情"),
                ))
            elif action == "update" and eid:
                item = db.get(Outline, eid)
                if item:
                    if "title" in data:
                        item.title = data["title"]
                    if "description" in data:
                        item.description = data["description"]
                    if "category" in data:
                        item.category = data["category"]
            elif action == "delete" and eid:
                db.query(Outline).filter(Outline.id == eid).delete()

        elif etype == "location":
            if action == "add":
                db.add(Location(
                    novel_id=novel_id,
                    name=data.get("name", ""),
                    description=data.get("description", ""),
                ))
            elif action == "update" and eid:
                item = db.get(Location, eid)
                if item:
                    if "name" in data:
                        item.name = data["name"]
                    if "description" in data:
                        item.description = data["description"]
            elif action == "delete" and eid:
                db.query(Location).filter(Location.id == eid).delete()

        elif etype == "timeline":
            if action == "add":
                db.add(TimelineEvent(
                    novel_id=novel_id,
                    event_type=TimelineEventType.MAIN_STORY,
                    time_label=data.get("time_label", data.get("time_str", "")),
                    content=data.get("content", data.get("event_desc", "")),
                    absolute_tick=novel.current_tick if novel else 0,
                    timeline_index=0,
                ))
            elif action == "update" and eid:
                item = db.get(TimelineEvent, eid)
                if item:
                    if "time_label" in data or "time_str" in data:
                        item.time_label = data.get("time_label", data.get("time_str", item.time_label))
                    if "content" in data or "event_desc" in data:
                        item.content = data.get("content", data.get("event_desc", item.content))
                    if "event_type" in data:
                        item.event_type = data["event_type"]
            elif action == "delete" and eid:
                db.query(TimelineEvent).filter(TimelineEvent.id == eid).delete()

        db.commit()
        return {"status": "success", "msg": f"{etype} {action} 成功。"}

    if req.tool_name == "generate_reasoning_chain":
        action = req.arguments.get("action")
        title = req.arguments.get("title", "未命名推理链")
        steps = req.arguments.get("steps", [])
        now_ts = int(datetime.utcnow().timestamp())
        built_nodes = []
        for i, step in enumerate(steps):
            node_id = str(f"node_{now_ts}_{i}")
            next_node_id = str(f"node_{now_ts}_{i + 1}") if i < len(steps) - 1 else ""
            built_nodes.append({
                "id": node_id,
                "x": 50,
                "y": 50 + (i * 250),
                "premise": step.get("premise", ""),
                "prompt": step.get("prompt", ""),
                "target": step.get("target", "正文"),
                "next_node_id": next_node_id,
                "branches": [],
                "output": "",
            })

        if action == "create_new":
            new_chain = ReasoningChain(
                novel_id=novel_id,
                title=title,
                nodes=built_nodes,
                context_settings={"use_outlines": True, "use_characters": True, "use_timeline": True},
            )
            db.add(new_chain)
            db.commit()
            return {"status": "success", "msg": f"推理链 [{title}] 创建成功！"}
        if action == "overwrite_existing":
            chain_id = req.arguments.get("chain_id")
            if chain_id:
                chain = db.get(ReasoningChain, chain_id)
                if chain:
                    chain.title = title
                    chain.nodes = built_nodes
                    flag_modified(chain, "nodes")
                    db.commit()
                    return {"status": "success", "msg": f"推理链 [{title}] 已被更新！"}
        return {"status": "error", "msg": "操作参数不完整"}

    return {"status": "error", "msg": "未知工具"}


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
