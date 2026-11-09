"""#T010 MCP 桥接层 — 为外部 LLM agent (harness) 提供 NovelAssist 内容的 MCP 通道。

传输：stdio（harness spawn 本进程）
复用：直接调用 backend.main 的端点函数（不改动 main.py）
返回：统一 JSON 字符串（避免 mcp 2.x -> list 注解丢失列表的陷阱）
排除：本软件 LLM 调用相关功能（见设计稿 §5）
"""
# ruff: noqa: E402  （自定位 chdir/sys.path 必须发生在 backend 相关 import 之前）
import enum
import json
import os
import sys
from typing import Any

# ── 自定位（全局任意工作区可用；非硬编码，跟随 mcp_server.py 物理位置）──
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))   # .../NovelAssist/backend
_NOVELASSIST_DIR = os.path.dirname(_BACKEND_DIR)           # .../NovelAssist
if _NOVELASSIST_DIR not in sys.path:
    sys.path.insert(0, _NOVELASSIST_DIR)
os.chdir(_NOVELASSIST_DIR)  # database.py 以 os.path.abspath(".") 定位 .data/novel.db

from fastapi import HTTPException
from mcp.server.mcpserver import MCPServer
from sqlalchemy.orm import Session

from backend import main as M
from backend.database import Base, engine, SessionLocal
from backend.models import Chapter
from sqlalchemy import select

# ── Schema 初始化（桥接层不执行 lifespan，须自行保证库就绪）──────────

def _init_schema() -> None:
    """幂等：create_all(checkfirst) 对已有库是 no-op；run_migration 检查列后补。"""
    Base.metadata.create_all(bind=engine)
    M.run_migration()
    db = SessionLocal()
    try:
        existing = db.scalars(select(M.Novel).limit(1)).first()
        if not existing:
            db.add(M.Novel(title="默认小说"))
            db.commit()
    finally:
        db.close()


_init_schema()

server = MCPServer(
    name="novelassist",
    instructions="NovelAssist 内容管理。novel_id 缺省为 1。所有工具返回 JSON 字符串。",
)


# ── 序列化 ──────────────────────────────────────────────────────

def _to_jsonable(obj: Any) -> Any:
    if obj is None:
        return None
    if isinstance(obj, list):
        return [_to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, enum.Enum):
        return _to_jsonable(obj.value)
    if hasattr(obj, "body") and hasattr(obj, "media_type"):
        try:
            return json.loads(obj.body)
        except (ValueError, TypeError):
            return obj.body
    if hasattr(obj, "__table__"):
        return {c.name: _to_jsonable(getattr(obj, c.name)) for c in obj.__table__.columns}
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return obj


def _ok(result: Any) -> str:
    return json.dumps(_to_jsonable(result), ensure_ascii=False)


def _fail(status: int, detail: str) -> str:
    return json.dumps({"error": True, "status": status, "detail": detail}, ensure_ascii=False)


# ── 调用包装 ────────────────────────────────────────────────────

def _call(fn, **kwargs) -> str:
    db = SessionLocal()
    try:
        return _ok(fn(db=db, **kwargs))
    except HTTPException as e:
        return _fail(e.status_code, str(e.detail))
    except Exception as e:
        return _fail(500, f"{type(e).__name__}: {e}")
    finally:
        db.close()


async def _call_async(fn, **kwargs) -> str:
    db = SessionLocal()
    try:
        return _ok(await fn(db=db, **kwargs))
    except HTTPException as e:
        return _fail(e.status_code, str(e.detail))
    except Exception as e:
        return _fail(500, f"{type(e).__name__}: {e}")
    finally:
        db.close()


# ════════════════════════════════════════════════════════════════
# 4.1 项目 / 时间
# ════════════════════════════════════════════════════════════════

@server.tool()
def list_novels() -> str:
    """列出所有小说项目。"""
    return _call(M.list_novels)


@server.tool()
def get_novel(novel_id: int = M.NOVEL_ID_DEFAULT) -> str:
    """获取指定小说的详情（含模板/历法/当前 tick）。"""
    return _call(M.get_novel, novel_id=novel_id)


@server.tool()
def create_novel(body: M.NovelCreate) -> str:
    """创建新小说项目。"""
    return _call(M.create_novel, body=body)


@server.tool()
def delete_novel(novel_id: int = M.NOVEL_ID_DEFAULT) -> str:
    """删除小说项目（级联删除全部关联内容）。"""
    return _call(M.delete_novel, novel_id=novel_id)


@server.tool()
def set_novel_template(novel_id: int = M.NOVEL_ID_DEFAULT, body: list = []) -> str:
    """设置角色模板（list of template definitions）。"""
    return _call(M.update_novel_template, novel_id=novel_id, body=body)


@server.tool()
def get_novel_time(novel_id: int = M.NOVEL_ID_DEFAULT) -> str:
    """获取小说当前时间（历法配置 + current_tick）。"""
    return _call(M.get_novel_time, novel_id=novel_id)


@server.tool()
def set_novel_time(req: M.NovelTimeUpdate, novel_id: int = M.NOVEL_ID_DEFAULT) -> str:
    """推进/回拨小说时间（自动触发世界事件判定、传闻扩散等既有联动）。"""
    return _call(M.update_novel_time, novel_id=novel_id, req=req)


@server.tool()
def get_calendar_format(tick: int, novel_id: int = M.NOVEL_ID_DEFAULT) -> str:
    """将 tick 格式化为历法日期字符串。"""
    return _call(M.calendar_format, tick=tick, novel_id=novel_id)


@server.tool()
def reset_prompts() -> str:
    """恢复所有提示词为默认值。"""
    return _call(M.reset_prompt_templates)


@server.tool()
def get_prompt_settings() -> str:
    """获取当前所有自定义提示词设置。"""
    return _call(M.get_prompts)


@server.tool()
def set_prompt_settings(body: dict) -> str:
    """更新自定义提示词设置（dict）。"""
    return _call(M.update_prompts, body=body)


# ════════════════════════════════════════════════════════════════
# 4.2 正文（Volume / Chapter）
# ════════════════════════════════════════════════════════════════

@server.tool()
def list_volumes(novel_id: int = M.NOVEL_ID_DEFAULT) -> str:
    """列出指定小说的所有卷。"""
    return _call(M.list_volumes, novel_id=novel_id)


@server.tool()
def create_volume(body: M.VolumeCreate, novel_id: int = M.NOVEL_ID_DEFAULT) -> str:
    """创建新卷。"""
    return _call(M.create_volume, body=body, novel_id=novel_id)


@server.tool()
def update_volume(volume_id: int, req: M.VolumeUpdate) -> str:
    """更新卷（改标题）。"""
    return _call(M.update_volume, volume_id=volume_id, req=req)


@server.tool()
def delete_volume(volume_id: int) -> str:
    """删除卷。"""
    return _call(M.delete_volume, volume_id=volume_id)


@server.tool()
def list_chapters(novel_id: int = M.NOVEL_ID_DEFAULT) -> str:
    """列出指定小说的所有章节（含完整字段，含正文 content）。"""
    return _call(M.list_chapters, novel_id=novel_id)


@server.tool()
def get_chapter(chapter_id: int) -> str:
    """按 id 获取单章详情（含完整正文）。"""
    def _impl(db: Session):
        ch = db.get(Chapter, chapter_id)
        if ch is None:
            raise HTTPException(404, "章节不存在")
        return ch
    return _call(_impl)


@server.tool()
def create_chapter(body: M.ChapterCreate, novel_id: int = M.NOVEL_ID_DEFAULT) -> str:
    """创建新章节。"""
    return _call(M.create_chapter, body=body, novel_id=novel_id)


@server.tool()
def update_chapter(chapter_id: int, body: M.ChapterCreate) -> str:
    """更新章节（标题/正文/排序/卷归属）。"""
    return _call(M.update_chapter, chapter_id=chapter_id, body=body)


@server.tool()
def delete_chapter(chapter_id: int) -> str:
    """删除章节。"""
    return _call(M.delete_chapter, chapter_id=chapter_id)


@server.tool()
async def archive_chapter(chapter_id: int, req: M.ArchiveRequest) -> str:
    """归档章节选中文本（LLM 总结后存入 archived_content；无 key 时总结质量有限）。"""
    return await _call_async(M.archive_chapter, chapter_id=chapter_id, req=req)


# ════════════════════════════════════════════════════════════════
# 4.3 大纲
# ════════════════════════════════════════════════════════════════

@server.tool()
def list_outlines(novel_id: int = M.NOVEL_ID_DEFAULT) -> str:
    """列出指定小说的所有大纲条目。"""
    return _call(M.list_outlines, novel_id=novel_id)


@server.tool()
def create_outline(body: M.OutlineCreate, novel_id: int = M.NOVEL_ID_DEFAULT) -> str:
    """创建新大纲条目。"""
    return _call(M.create_outline, body=body, novel_id=novel_id)


@server.tool()
def update_outline(outline_id: int, body: M.OutlineCreate) -> str:
    """更新大纲条目。"""
    return _call(M.update_outline, outline_id=outline_id, body=body)


@server.tool()
def delete_outline(outline_id: int) -> str:
    """删除大纲条目。"""
    return _call(M.delete_outline, outline_id=outline_id)


# ════════════════════════════════════════════════════════════════
# 4.4 人物
# ════════════════════════════════════════════════════════════════

@server.tool()
def list_characters(novel_id: int = M.NOVEL_ID_DEFAULT) -> str:
    """列出指定小说的所有角色（含势力关联）。"""
    return _call(M.list_characters, novel_id=novel_id)


@server.tool()
def get_character(char_id: int) -> str:
    """获取单个角色详情（含属性/势力/当前状态）。"""
    return _call(M.get_character, char_id=char_id)


@server.tool()
def create_character(body: M.CharacterCreate, novel_id: int = M.NOVEL_ID_DEFAULT) -> str:
    """创建新角色。"""
    return _call(M.create_character, body=body, novel_id=novel_id)


@server.tool()
def update_character(char_id: int, body: M.CharacterCreate) -> str:
    """更新角色（姓名/别名/描述/状态/属性/势力）。"""
    return _call(M.update_character, char_id=char_id, body=body)


@server.tool()
def delete_character(char_id: int) -> str:
    """删除角色（级联清理快照/日程/轨迹/关系）。"""
    return _call(M.delete_character, char_id=char_id)


@server.tool()
def set_character_factions(char_id: int, body: list) -> str:
    """整体替换角色的所有势力关联。body 为 [{faction_id, faction_role}, ...]；首个为主势力。"""
    items = [M.CharacterFactionLinkIn(**x) for x in body]
    return _call(M.set_character_factions, char_id=char_id, body=items)


@server.tool()
def list_snapshots(char_id: int) -> str:
    """列出角色的所有快照版本。"""
    return _call(M.list_snapshots, char_id=char_id)


@server.tool()
def create_snapshot(char_id: int, body: M.SnapshotCreate) -> str:
    """为角色创建属性快照。"""
    return _call(M.create_snapshot, char_id=char_id, body=body)


@server.tool()
def delete_snapshot(snap_id: int) -> str:
    """删除角色快照。"""
    return _call(M.delete_snapshot, snap_id=snap_id)


@server.tool()
def get_time_data(char_id: int) -> str:
    """获取角色的日程（routine）和轨迹（trajectory）数据。"""
    return _call(M.get_char_time, char_id=char_id)


@server.tool()
def set_time_data(char_id: int, req: M.CharacterTimeData) -> str:
    """设置角色的日程和轨迹数据（整体替换）。"""
    return _call(M.update_char_time, char_id=char_id, req=req)


# ════════════════════════════════════════════════════════════════
# 4.5 势力
# ════════════════════════════════════════════════════════════════

@server.tool()
def list_factions(novel_id: int = M.NOVEL_ID_DEFAULT) -> str:
    """列出指定小说的所有势力（含 faction_members 各势力职位）。"""
    return _call(M.get_factions, novel_id=novel_id)


@server.tool()
def create_faction(data: dict) -> str:
    """创建新势力。data 需含 novel_id，可选 name/description/base_location_id。"""
    return _call(M.create_faction, data=data)


@server.tool()
def update_faction(faction_id: int, data: dict) -> str:
    """更新势力（名称/描述/基地地点）。"""
    return _call(M.update_faction, faction_id=faction_id, data=data)


@server.tool()
def delete_faction(faction_id: int) -> str:
    """删除势力（级联清理角色关联）。"""
    return _call(M.delete_faction, faction_id=faction_id)


# ════════════════════════════════════════════════════════════════
# 4.6 地点
# ════════════════════════════════════════════════════════════════

@server.tool()
def list_locations(
    novel_id: int = M.NOVEL_ID_DEFAULT,
    parent_id: int | None = None,
    scale_level: str | None = None,
) -> str:
    """列出指定地点（含 enrich_location_out 计算属性）。可按 parent_id/scale_level 过滤。"""
    return _call(
        M.list_locations,
        novel_id=novel_id,
        parent_id=parent_id,
        filter_null_parent=(parent_id is not None),
        scale_level=scale_level,
    )


@server.tool()
def create_location(body: M.LocationCreate, novel_id: int = M.NOVEL_ID_DEFAULT) -> str:
    """创建新地点。"""
    return _call(M.create_location, body=body, novel_id=novel_id)


@server.tool()
def update_location(location_id: int, body: M.LocationCreate) -> str:
    """更新地点（名称/描述/层级/坐标/属性）。"""
    return _call(M.update_location, location_id=location_id, body=body)


@server.tool()
def delete_location(location_id: int) -> str:
    """删除地点（级联清理关联）。"""
    return _call(M.delete_location, location_id=location_id)


@server.tool()
def get_location_presence(loc_id: int, novel_id: int = M.NOVEL_ID_DEFAULT) -> str:
    """获取地点当前在场的角色列表。"""
    return _call(M.get_location_presence, loc_id=loc_id, novel_id=novel_id)


@server.tool()
def get_location_templates(novel_id: int = M.NOVEL_ID_DEFAULT) -> str:
    """获取地点属性模板配置。"""
    return _call(M.get_location_templates, novel_id=novel_id)


@server.tool()
def set_location_templates(novel_id: int = M.NOVEL_ID_DEFAULT, body: dict = {}) -> str:
    """设置地点属性模板配置。"""
    return _call(M.update_location_templates, novel_id=novel_id, body=body)


# ════════════════════════════════════════════════════════════════
# 4.7 事件 / 时间线
# ════════════════════════════════════════════════════════════════

@server.tool()
def list_events(novel_id: int = M.NOVEL_ID_DEFAULT) -> str:
    """列出指定小说的所有事件（EventCategory）。"""
    return _call(M.list_events, novel_id=novel_id)


@server.tool()
def create_event(body: M.EventCreate, novel_id: int = M.NOVEL_ID_DEFAULT) -> str:
    """创建新事件。"""
    return _call(M.create_event, body=body, novel_id=novel_id)


@server.tool()
def delete_event(event_id: int) -> str:
    """删除事件。"""
    return _call(M.delete_event, event_id=event_id)


@server.tool()
def list_world_events(status: str = "pending", novel_id: int = M.NOVEL_ID_DEFAULT) -> str:
    """列出世界事件（按 status 过滤：pending/accepted/rejected）。"""
    return _call(M.list_world_events, status=status, novel_id=novel_id)


@server.tool()
def accept_world_event(event_id: int) -> str:
    """采纳世界事件（计数清零并写入时间线；触发传闻扩散初始化）。"""
    return _call(M.accept_world_event, event_id=event_id)


@server.tool()
def reject_world_event(event_id: int) -> str:
    """拒绝世界事件（计数减半后继续累计）。"""
    return _call(M.reject_world_event, event_id=event_id)


@server.tool()
def get_world_event_config(novel_id: int = M.NOVEL_ID_DEFAULT) -> str:
    """获取世界事件设置（enabled/prob_per_month/spread_base_speed）。"""
    return _call(M.get_world_event_config, novel_id=novel_id)


@server.tool()
def set_world_event_config(req: M.WorldEventSettings) -> str:
    """更新世界事件设置。"""
    return _call(M.update_world_event_config, req=req)


@server.tool()
def list_timeline(novel_id: int = M.NOVEL_ID_DEFAULT, event_type: str = "") -> str:
    """列出时间线事件（统一混排：history/main_story/world）。可按 event_type 过滤。"""
    return _call(M.list_timeline, novel_id=novel_id, event_type=event_type)


@server.tool()
def create_timeline(body: M.TimelineCreate, novel_id: int = M.NOVEL_ID_DEFAULT) -> str:
    """创建时间线事件。"""
    return _call(M.create_timeline, body=body, novel_id=novel_id)


@server.tool()
def update_timeline(event_id: int, body: M.TimelineCreate) -> str:
    """更新时间线事件。"""
    return _call(M.update_timeline, event_id=event_id, body=body)


@server.tool()
def delete_timeline(event_id: int) -> str:
    """删除时间线事件。"""
    return _call(M.delete_timeline, event_id=event_id)


@server.tool()
def list_timeline_relations(event_id: int) -> str:
    """列出指定时间线事件的关联边。"""
    return _call(M.list_event_relations, event_id=event_id)


@server.tool()
def create_timeline_relation(body: M.TimelineRelationIn, novel_id: int = M.NOVEL_ID_DEFAULT) -> str:
    """创建时间线事件间的关联边（导致/影响等）。"""
    return _call(M.create_event_relation, body=body, novel_id=novel_id)


@server.tool()
def delete_timeline_relation(relation_id: int) -> str:
    """删除时间线事件间的关联边。"""
    return _call(M.delete_event_relation, relation_id=relation_id)


@server.tool()
def list_active_events(novel_id: int = M.NOVEL_ID_DEFAULT) -> str:
    """列出活跃事件树（主干/分支/突发，含级联子树）。"""
    return _call(M.list_active_events, novel_id=novel_id)


@server.tool()
def create_active_event(body: M.ActiveEventCreate, novel_id: int = M.NOVEL_ID_DEFAULT) -> str:
    """创建活跃事件（主干全局唯一校验自动生效）。"""
    return _call(M.create_active_event, body=body, novel_id=novel_id)


@server.tool()
def update_active_event(node_id: int, body: M.ActiveEventCreate) -> str:
    """更新活跃事件。"""
    return _call(M.update_active_event, node_id=node_id, body=body)


@server.tool()
def delete_active_event(node_id: int) -> str:
    """删除活跃事件（级联删除子树）。"""
    return _call(M.delete_active_event, node_id=node_id)


@server.tool()
async def record_branch(body: M.RecordBranchRequest, novel_id: int = M.NOVEL_ID_DEFAULT) -> str:
    """将正文选中文本记录为支线事件（含 LLM 总结；无 key 时退化为原文截取）。"""
    return await _call_async(M.record_active_branch, body=body, novel_id=novel_id)


@server.tool()
async def graduate_active_event(node_id: int, body: M.GraduateRequest) -> str:
    """毕业活跃事件（LLM 总结写入 main_story 后从事件树移除；无 key 时退化为语料汇总）。"""
    return await _call_async(M.graduate_active_event, node_id=node_id, body=body)


# ════════════════════════════════════════════════════════════════
# 4.8 角色关系 / 推理链
# ════════════════════════════════════════════════════════════════

@server.tool()
def list_character_relations(novel_id: int = M.NOVEL_ID_DEFAULT) -> str:
    """列出指定小说的所有角色关系。"""
    return _call(M.get_relations, novel_id=novel_id)


@server.tool()
def create_character_relation(body: M.RelationCreate, novel_id: int = M.NOVEL_ID_DEFAULT) -> str:
    """创建角色关系（标签/权重/描述/颜色）。"""
    return _call(M.create_relation, body=body, novel_id=novel_id)


@server.tool()
def update_character_relation(rel_id: int, body: M.RelationCreate) -> str:
    """更新角色关系。"""
    return _call(M.update_relation, rel_id=rel_id, body=body)


@server.tool()
def delete_character_relation(rel_id: int) -> str:
    """删除角色关系。"""
    return _call(M.delete_relation, rel_id=rel_id)


@server.tool()
def list_reasoning_chains(novel_id: int = M.NOVEL_ID_DEFAULT) -> str:
    """列出指定小说的所有推理链。"""
    return _call(M.list_reasoning_chains, novel_id=novel_id)


@server.tool()
def get_reasoning_chain(chain_id: int) -> str:
    """获取单条推理链详情（含节点/上下文/todos）。"""
    return _call(M.get_reasoning_chain, chain_id=chain_id)


@server.tool()
def create_reasoning_chain(body: M.ReasoningChainCreate, novel_id: int = M.NOVEL_ID_DEFAULT) -> str:
    """创建推理链（含节点列表/上下文设置）。"""
    return _call(M.create_reasoning_chain, body=body, novel_id=novel_id)


@server.tool()
def update_reasoning_chain(chain_id: int, body: M.ReasoningChainCreate) -> str:
    """更新推理链。"""
    return _call(M.update_reasoning_chain, chain_id=chain_id, body=body)


@server.tool()
def delete_reasoning_chain(chain_id: int) -> str:
    """删除推理链。"""
    return _call(M.delete_reasoning_chain, chain_id=chain_id)


@server.tool()
def restore_reasoning_chain(chain_id: int) -> str:
    """恢复推理链为系统默认。"""
    return _call(M.restore_system_chain, chain_id=chain_id)


@server.tool()
def clear_chain_todos(chain_id: int) -> str:
    """清除推理链的 todos。"""
    return _call(M.clear_chain_todos, chain_id=chain_id)


# ════════════════════════════════════════════════════════════════
# 4.9 批量交换
# ════════════════════════════════════════════════════════════════

@server.tool()
def export_settings(novel_id: int = M.NOVEL_ID_DEFAULT, fmt: str = "json") -> str:
    """导出设定集（JSON bundle 或 MD 文本）。fmt: json|md。"""
    db = SessionLocal()
    try:
        novel = db.get(M.Novel, novel_id)
        if novel is None:
            return _fail(404, "小说不存在")
        bundle = M._collect_settings_bundle(db, novel)
        if fmt == "md":
            return _ok({"fmt": "md", "content": M._render_settings_markdown(bundle)})
        return _ok(bundle)
    except Exception as e:
        return _fail(500, f"{type(e).__name__}: {e}")
    finally:
        db.close()


@server.tool()
def import_settings(req: M.ImportSettingsReq, novel_id: int = M.NOVEL_ID_DEFAULT) -> str:
    """导入设定集（按名增量 upsert + 级联删除）。dry_run=true 时只预览不落库。"""
    return _call(M.import_settings_bundle, novel_id=novel_id, req=req)


# ════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    server.run()
