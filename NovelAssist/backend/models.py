"""数据模型定义 — 世界观核心实体"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import Enum as SAEnum, ForeignKey, Integer, String, Text, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

try:
    from .database import Base
except ImportError:
    from database import Base


class EventType(enum.Enum):
    MAIN = "MAIN"
    WORLD = "WORLD"
    RANDOM = "RANDOM"


class LocationScale(enum.Enum):
    REGION = "REGION"
    GRID_25 = "GRID_25"


class TimelineEventType(enum.Enum):
    HISTORY = "history"
    MAIN_STORY = "main_story"
    WORLD = "world"


class Novel(Base):
    __tablename__ = "novels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="默认项目")
    character_template: Mapped[dict] = mapped_column(JSON, nullable=False, default=lambda: [
        {"group": "基础信息", "fields": ["姓名", "年龄", "性别", "性格"]},
        {"group": "外貌特征", "fields": ["身高", "发色", "瞳色"]},
        {"group": "背景故事", "fields": ["出身", "经历"]},
    ])
    location_templates: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    volumes: Mapped[list[Volume]] = relationship(back_populates="novel")
    outlines: Mapped[list[Outline]] = relationship(back_populates="novel")
    chapters: Mapped[list[Chapter]] = relationship(back_populates="novel")
    characters: Mapped[list[Character]] = relationship(back_populates="novel")
    locations: Mapped[list[Location]] = relationship(back_populates="novel")
    world_events: Mapped[list[WorldEvent]] = relationship(back_populates="novel")
    timeline_events: Mapped[list[TimelineEvent]] = relationship(back_populates="novel")


class Volume(Base):
    """分卷 — 章节的上层组织单元"""
    __tablename__ = "volumes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    novel_id: Mapped[int] = mapped_column(ForeignKey("novels.id"), nullable=False, default=1)
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    novel: Mapped[Novel] = relationship(back_populates="volumes")
    chapters: Mapped[list[Chapter]] = relationship(back_populates="volume")


class Chapter(Base):
    __tablename__ = "chapters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    archived_content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    volume_id: Mapped[Optional[int]] = mapped_column(ForeignKey("volumes.id"), nullable=True)
    novel_id: Mapped[int] = mapped_column(ForeignKey("novels.id"), nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    novel: Mapped[Novel] = relationship(back_populates="chapters")
    volume: Mapped[Optional[Volume]] = relationship(back_populates="chapters")


class Outline(Base):
    """大纲笔记 — 独立于分卷的知识管理"""
    __tablename__ = "outlines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="剧情")
    is_always_context: Mapped[bool] = mapped_column(default=False)
    parent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("outlines.id"), nullable=True)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    novel_id: Mapped[int] = mapped_column(ForeignKey("novels.id"), nullable=False, default=1)

    novel: Mapped[Novel] = relationship(back_populates="outlines")
    parent: Mapped[Optional[Outline]] = relationship(
        back_populates="children", remote_side="Outline.id"
    )
    children: Mapped[list[Outline]] = relationship(back_populates="parent")


class Character(Base):
    __tablename__ = "characters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    aliases: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="存活")
    is_active: Mapped[bool] = mapped_column(default=True)
    is_always_context: Mapped[bool] = mapped_column(default=False)
    attributes: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    novel_id: Mapped[int] = mapped_column(ForeignKey("novels.id"), nullable=False, default=1)

    novel: Mapped[Novel] = relationship(back_populates="characters")


class Location(Base):
    __tablename__ = "locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    parent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("locations.id"), nullable=True)
    scale_level: Mapped[str] = mapped_column(String(64), nullable=False, default="星球")
    scale: Mapped[LocationScale] = mapped_column(
        SAEnum(LocationScale), nullable=False, default=LocationScale.REGION
    )
    grid_x: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    grid_y: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    attributes: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    novel_id: Mapped[int] = mapped_column(ForeignKey("novels.id"), nullable=False, default=1)

    novel: Mapped[Novel] = relationship(back_populates="locations")
    parent: Mapped[Optional[Location]] = relationship(
        back_populates="children", remote_side="Location.id"
    )
    children: Mapped[list[Location]] = relationship(back_populates="parent")
    events: Mapped[list[WorldEvent]] = relationship(back_populates="location")


class WorldEvent(Base):
    __tablename__ = "world_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    event_type: Mapped[EventType] = mapped_column(
        SAEnum(EventType), nullable=False, default=EventType.MAIN
    )
    timeline_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    location_id: Mapped[Optional[int]] = mapped_column(ForeignKey("locations.id"), nullable=True)
    novel_id: Mapped[int] = mapped_column(ForeignKey("novels.id"), nullable=False, default=1)

    novel: Mapped[Novel] = relationship(back_populates="world_events")
    location: Mapped[Optional[Location]] = relationship(back_populates="events")


class TimelineEvent(Base):
    """时间线事件 — 历史/正文/世界三线并行"""
    __tablename__ = "timeline_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    novel_id: Mapped[int] = mapped_column(ForeignKey("novels.id"), nullable=False, default=1)
    event_type: Mapped[TimelineEventType] = mapped_column(
        SAEnum(TimelineEventType), nullable=False, default=TimelineEventType.MAIN_STORY
    )
    timeline_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    time_label: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    related_location_id: Mapped[Optional[int]] = mapped_column(ForeignKey("locations.id"), nullable=True)

    novel: Mapped[Novel] = relationship(back_populates="timeline_events")
    related_location: Mapped[Optional[Location]] = relationship()


class CharacterSnapshot(Base):
    """人物属性历史快照"""
    __tablename__ = "character_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id", ondelete="CASCADE"), nullable=False)
    version_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    attributes: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    character: Mapped[Character] = relationship()


class ReasoningChain(Base):
    """推演方案 — 卡片式工作流"""
    __tablename__ = "reasoning_chains"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    novel_id: Mapped[int] = mapped_column(ForeignKey("novels.id"), nullable=False, default=1)
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    nodes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    context_settings: Mapped[dict] = mapped_column(JSON, nullable=False, default=lambda: {"use_outlines": True, "use_characters": True, "use_timeline": True})

    novel: Mapped[Novel] = relationship()


DEFAULT_PROMPT_TEMPLATES = {
    "chat": (
        "你是一个顶尖的网文创作副驾。\n"
        "【世界观与设定集】\n{lore}\n\n"
        "【当前内容】\n{content}\n\n"
        "请遵循作者指令进行设定推演或解答。\n"
        "【指令】\n{instruction}\n\n"
        "要求：不要输出多余的客套话，直接给出结果。"
    ),
    "continue": (
        "你是一位白金级网文大神。请参考以下设定，顺着前文的语气和节奏紧接上文续写。\n"
        "【设定参考】\n{lore}\n\n"
        "【前文内容】\n{content}\n\n"
        '要求：不要换行分段，直接输出续写内容，不要包含任何\u201c好的\u201d等废话。'
    ),
    "polish": (
        "你是专业的文字修稿师。请对以下选中文字进行润色，使其画面感更强、节奏更明快。\n"
        "【设定参考】\n{lore}\n\n"
        "【选中文字】\n{selection}\n\n"
        "要求：保持原意与核心动作不变，不偏离人物性格，不扩写多余剧情。"
    ),
    "review": (
        "你是资深网文主编。请基于设定，评价下面这段正文的情节张力、节奏和文笔。\n"
        "【设定参考】\n{lore}\n\n"
        "【正文内容】\n{content}\n\n"
        "要求：给出犀利的优缺点点评，并列出3条具体的修改建议。"
    ),
    "chain_write": (
        "你是专业小说作家。以下是推演链的最终分析结果，请据此生成完整生动的小说正文。\n"
        "【设定参考】\n{lore}\n\n"
        "【推演数据】\n{chain_data}\n\n"
        "要求：具备小说应有的环境描写、动作和对话。不要输出正文以外的任何分析或提示语。"
    ),
    "outline_assist": (
        "你是大纲策划师。请基于设定对当前大纲进行分析与建议。\n"
        "【设定参考】\n{lore}\n\n"
        "【当前大纲】\n{content}\n\n"
        "要求：不过度剧透后续情节，关注结构的逻辑性和节奏起伏。不要输出无关客套话。"
    ),
    "map_assist": (
        "你是世界观设计师。请基于设定对当前地点进行分析与设计建议。\n"
        "【设定参考】\n{lore}\n\n"
        "【当前地点信息】\n{content}\n\n"
        "要求：结合地理逻辑和世界观一致性，不要输出无关客套话。"
    ),
    "character_assist": (
        "你是角色设计师。请基于设定对当前人物进行分析与设计建议。\n"
        "【设定参考】\n{lore}\n\n"
        "【当前人物信息】\n{content}\n\n"
        "要求：关注人物的动机弧光、关系网和成长曲线。不要输出无关客套话。"
    ),
    "archive_timeline": (
        '阅读以下正文，输出两行：第一行是推测故事时间（如"入冬后"、"次日清晨"等，10字内），第二行是简短剧情概括（50字内）。\n'
        "格式：\n时间: xxx\n事件: xxx\n\n"
        "正文：\n{text}"
    ),
    "archive_character": (
        "人物模板字段结构：{template}\n"
        "已有人物数据：{characters}\n\n"
        "阅读以下正文，寻找人物状态或属性的改变。请严格对照模板的分组和字段名来填写 updates，不要遗漏模板中已有但当前为空的字段。如果正文提到某个人物获得了物品，请将物品名称填入对应的分组字段中。\n\n"
        "【重要指令】：分析该人物是否已退出故事主舞台。如果人物明确死亡、归隐、去往极远之地、被长期封印，或其戏份已彻底完结（以后大概率不再登场），请将其 \"is_active\" 设为 false，并在 \"status_change\" 中说明原因（如：死亡/重伤归隐/飞升上界等）。如果人物仍活跃在当前剧情中（即使状态是重伤、被俘等），\"is_active\" 保持为 true。\n\n"
        "你必须仅返回合法的 JSON 数组，包含在 ```json ``` 代码块中，绝对不要输出任何其他解释文字。\n"
        "格式：\n```json\n[{\"character_id\": 1, \"updates\": {\"分组名\": {\"字段名\": \"新值\"}}, \"status_change\": \"退场原因\", \"is_active\": false}]\n```\n"
        "如果没有任何人物发生变化，返回空数组 []。\n\n"
        "正文：\n{text}"
    ),
    "sandbox_sim": (
        "你是一个严肃的小说沙盘演武引擎。请严格参考传入的场地环境属性和角色的境界、装备等设定。根据用户的【推演情景】，以客观、合乎逻辑的方式推演战斗或交互过程。\n"
        "注意战力平衡，展现环境对战局的影响。输出应具有明确的动作感和回合感，但不要输出任何与推演无关的客套话或分析。\n\n"
        "{content}\n\n"
        "推演指令：{instruction}"
    ),
}


class GlobalSettings(Base):
    """全局设置 — 单例"""
    __tablename__ = "global_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prompt_templates: Mapped[dict] = mapped_column(JSON, nullable=False, default=DEFAULT_PROMPT_TEMPLATES)
