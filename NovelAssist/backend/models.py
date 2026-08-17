"""数据模型定义 — 世界观核心实体"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import Enum as SAEnum, ForeignKey, Integer, String, Text, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

try:
    from .database import Base
    from .constants import LocationScaleLevel, OutlineCategory
except ImportError:
    from database import Base
    from constants import LocationScaleLevel, OutlineCategory


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


class EventCategory(enum.Enum):
    TERRAIN = "地形变动"
    CHARACTER_LIFE = "人物生死"
    POLITICAL = "政治事件"
    TREASURE = "宝物现世"
    CREATURE = "生物异动"
    NATURAL = "自然演变"
    OTHER = "其他"


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
    calendar_config: Mapped[dict] = mapped_column(JSON, nullable=False, default=lambda: {"months_per_year": 12, "days_per_month": 30, "hours_per_day": 24})
    current_tick: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    volumes: Mapped[list[Volume]] = relationship(back_populates="novel")
    outlines: Mapped[list[Outline]] = relationship(back_populates="novel")
    chapters: Mapped[list[Chapter]] = relationship(back_populates="novel")
    characters: Mapped[list[Character]] = relationship(back_populates="novel")
    locations: Mapped[list[Location]] = relationship(back_populates="novel")
    world_events: Mapped[list[WorldEvent]] = relationship(back_populates="novel")
    timeline_events: Mapped[list[TimelineEvent]] = relationship(back_populates="novel")
    factions: Mapped[list[Faction]] = relationship(back_populates="novel")
    character_relations: Mapped[list[CharacterRelation]] = relationship(back_populates="novel")


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
    pending_timeline_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
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
    category: Mapped[str] = mapped_column(String(64), nullable=False, default=OutlineCategory.LEGACY_DEFAULT.value)
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
    faction_id: Mapped[Optional[int]] = mapped_column(ForeignKey("factions.id"), nullable=True)
    faction_role: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    novel_id: Mapped[int] = mapped_column(ForeignKey("novels.id"), nullable=False, default=1)

    novel: Mapped[Novel] = relationship(back_populates="characters")
    faction: Mapped[Optional[Faction]] = relationship(back_populates="members")


class Faction(Base):
    """势力/宗门/国家"""
    __tablename__ = "factions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    novel_id: Mapped[int] = mapped_column(ForeignKey("novels.id"), nullable=False, default=1)
    name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    base_location_id: Mapped[Optional[int]] = mapped_column(ForeignKey("locations.id"), nullable=True)

    novel: Mapped[Novel] = relationship(back_populates="factions")
    base_location: Mapped[Optional[Location]] = relationship()
    members: Mapped[list[Character]] = relationship(back_populates="faction")


class Location(Base):
    __tablename__ = "locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    parent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("locations.id"), nullable=True)
    scale_level: Mapped[str] = mapped_column(String(64), nullable=False, default=LocationScaleLevel.PLANET.value)
    scale: Mapped[LocationScale] = mapped_column(
        SAEnum(LocationScale), nullable=False, default=LocationScale.REGION
    )
    grid_x: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    grid_y: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    map_x: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    map_y: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
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
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    time_label: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="其他")
    character_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    absolute_tick: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    related_location_id: Mapped[Optional[int]] = mapped_column(ForeignKey("locations.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    novel: Mapped[Novel] = relationship(back_populates="timeline_events")
    related_location: Mapped[Optional[Location]] = relationship()


class TimelineEventRelation(Base):
    """事件因果关系 — 有向边：source → target"""
    __tablename__ = "timeline_event_relations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    novel_id: Mapped[int] = mapped_column(ForeignKey("novels.id"), nullable=False, default=1)
    source_event_id: Mapped[int] = mapped_column(ForeignKey("timeline_events.id", ondelete="CASCADE"), nullable=False)
    target_event_id: Mapped[int] = mapped_column(ForeignKey("timeline_events.id", ondelete="CASCADE"), nullable=False)
    label: Mapped[str] = mapped_column(String(64), nullable=False, default="导致")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    source_event: Mapped[TimelineEvent] = relationship(foreign_keys=[source_event_id])
    target_event: Mapped[TimelineEvent] = relationship(foreign_keys=[target_event_id])


class CharacterSnapshot(Base):
    """人物属性历史快照"""
    __tablename__ = "character_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id", ondelete="CASCADE"), nullable=False)
    version_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    attributes: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    character: Mapped[Character] = relationship()


class CharacterRoutine(Base):
    """人物作息规律 — 周期性行为模式"""
    __tablename__ = "character_routines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id"), nullable=False)
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"), nullable=False)
    cycle_type: Mapped[str] = mapped_column(String(16), nullable=False, default="日")
    cycle_value: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    activity: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    character: Mapped[Character] = relationship()
    location: Mapped[Location] = relationship()


class CharacterTrajectory(Base):
    """人物特殊轨迹 — 一次性或长期移动事件"""
    __tablename__ = "character_trajectories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id"), nullable=False)
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"), nullable=False)
    start_tick: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    end_tick: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    reason: Mapped[str] = mapped_column(String(512), nullable=False, default="")

    character: Mapped[Character] = relationship()
    location: Mapped[Location] = relationship()


class ReasoningChain(Base):
    """推演方案 — 卡片式工作流"""
    __tablename__ = "reasoning_chains"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    novel_id: Mapped[int] = mapped_column(ForeignKey("novels.id"), nullable=False, default=1)
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    nodes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    context_settings: Mapped[dict] = mapped_column(JSON, nullable=False, default=lambda: {"use_outlines": False, "use_characters": False, "use_timeline": False})
    todos: Mapped[list] = mapped_column(JSON, nullable=False, default=list)  # 推理链任务清单 [{id, content, status, created_at, updated_at, notes}]

    novel: Mapped[Novel] = relationship()


DEFAULT_PROMPT_TEMPLATES = {
    "system_agent": (
        "你是本小说的全知全能系统代理（Agent）。\n"
        "【最高执行纪律】：\n"
        "1. 必须调用 manage_world_element 工具执行创建/修改，禁止仅口头敷衍。\n"
        "2. 【严禁脑补串线】：用户让你建人物就只建人物，让你建地点就只建地点！\n"
        "3. 【地图与坐标】：层级为 [大千世界-宇宙-星球-大陆-国家-城池-街区]。可推断 parent_name 补齐中间层。grid_x/grid_y 为 0-24 整数。\n"
        "4. 【防覆盖机制】：当执行 update 动作修改已有实体时，你传入的参数会直接覆盖旧值！如果原设定很长，你必须输出【原内容与新内容合并后】的完整文本，绝不能用几个字把原有长篇设定顶替掉！\n"
        "5. 【人物填表】：必须以嵌套字典格式填入 attributes。当前系统模板为: {character_template}。"
        "【警告】：对于模板中已有的字段，必须严格使用其原有的分组名！只有当你创造了模板中完全不存在的新属性时，才允许自创新的分组名！\n"
        "回答需直接、精准，直接触发工具。"
    ),
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
    "archive_timeline": (
        "【万年历时钟与时间线事件提炼器】\n"
        "请阅读以下已被归档的小说正文，提取关键剧情事件放入时间线。\n\n"
        "【时间流逝已自动计算】：\n"
        "{detected_time}\n\n"
        "注意：时间流逝已通过正文中的【时间流逝】标记自动计算，你无需操心时间推进。\n"
        "你只需专注从正文中提取关键事件。\n\n"
        "返回格式必须为干净的 JSON 对象，绝对不要包含任何 markdown 转义符号（不要 ```json 标记），直接输出以下结构的纯 JSON 字符串：\n"
        "{\n"
        "  \"events\": [\n"
        "    {\n"
        "      \"title\": \"事件名称（10字以内精炼概括）\",\n"
        "      \"category\": \"地形变动 | 人物生死 | 政治事件 | 宝物现世 | 生物异动 | 自然演变 | 其他\",\n"
        "      \"location\": \"发生地点（若有明确地名则写，无则写'未知'）\",\n"
        "      \"characters\": [\"出场人物名1\", \"出场人物名2\"],\n"
        "      \"event\": \"事件摘要（用生动、凝练的语言概括，100字以内）\"\n"
        "    }\n"
        "  ]\n"
        "}\n\n"
        "正文文本：\n{text}"
    ),
    "archive_character": (
        "【群像人设万能繁衍与生命周期强同步器】\n"
        "请阅读以下已被归档的正文故事，对照提供的人物模板和当前已有的人物简述，提炼这些角色在此段情节中发生的一切境界突破、伤病、死活，甚至是【新登场的全新角色】！\n\n"
        "【属性配置模板】：\n{template}\n\n"
        "【当前已有人物数据】：\n{characters}\n\n"
        "【刚刚发生的正文故事】：\n{text}\n\n"
        "【繁衍与同步天条】：\n"
        "1. 如果正文中出现了【之前从未登场、也未在已有人物数据中的新角色】（且有名字、有动作戏或重要台词），你必须将其作为【新人物】进行繁衍创建！\n"
        "   - 将 \"is_new\" 设为 true\n"
        "   - \"character_id\" 设为 null\n"
        "   - \"name\" 填写其名字（如：独孤剑尊）\n"
        "   - \"updates\" 中必须对照配置模板，生成包含其姓名、性格、功法境界等必填分组的饱满 attributes 字典。\n"
        "2. 如果是【已有的人物】发生改变，你必须：\n"
        "   - \"is_new\" 设为 false\n"
        "   - \"character_id\" 填写其已有 ID\n"
        "   - \"updates\" 中严格对照模板的分组和字段名来填写改变的值，不要遗漏。\n"
        "3. 生死体征：若人物在正文中明确死亡、飞升、彻底归隐，将 \"is_active\" 设为 false，并在 \"status_change\" 中说明原因；若仍活跃则 \"is_active\" 为 true。\n"
        "4. 势力更迭：若加入了新的宗门、帮会势力，\"faction_change\" 填其名称（如：暗影回廊）；若脱离或背叛了，填 '无'。\n"
        "5. 移动轨迹：若明确提到移动或去往了另一个地点，\"move_to_location\" 填对应地名，否则留空。\n"
        "6. 【重要】时间对死亡角色的影响：若角色当前 is_active 为 false（已死亡/飞升/归隐），时间流逝标记不应影响其年龄、外貌等随时间变化的属性。死亡角色的属性应保持冻结状态，无需在 updates 中更新年龄相关字段。仅在正文中有明确描述该角色（如回忆、复活）时才更新其属性。\n\n"
        "请仅返回干净的 JSON 数组（绝对不要包含 ```json 等标记，直接输出纯 JSON 数组）：\n"
        "[\n"
        "  {\n"
        "    \"character_id\": 1,\n"
        "    \"is_new\": false,\n"
        "    \"name\": \"林枫\",\n"
        "    \"updates\": {\n"
        "       \"能力数据\": { \"当前境界\": \"筑基中期\" }\n"
        "    },\n"
        "    \"status_change\": \"突破筑基中期，右臂骨折受轻伤\",\n"
        "    \"is_active\": true,\n"
        "    \"faction_change\": \"无\",\n"
        "    \"move_to_location\": \"埋骨荒原\"\n"
        "  },\n"
        "  {\n"
        "    \"character_id\": null,\n"
        "    \"is_new\": true,\n"
        "    \"name\": \"白发老者\",\n"
        "    \"updates\": {\n"
        "       \"基本信息\": { \"姓名\": \"白发老者\", \"性别\": \"男\", \"身份\": \"世外高人\" },\n"
        "       \"性格特征\": { \"性格\": \"古怪, 孤傲\" },\n"
        "       \"能力数据\": { \"当前境界\": \"化神期\" }\n"
        "    },\n"
        "    \"status_change\": \"初登场\",\n"
        "    \"is_active\": true,\n"
        "    \"faction_change\": \"无\",\n"
        "    \"move_to_location\": \"太荒深渊\"\n"
        "  }\n"
        "]"
    ),
    "sandbox_sim": (
        "你是一个严肃的小说场景沙盘引擎。请严格参考传入的场地环境属性和角色的境界、装备等设定。根据用户的【推演情景】，以客观、合乎逻辑的方式推演场景或交互过程。\n"
        "注意战力平衡，展现环境对战局的影响。输出应具有明确的动作感和回合感，但不要输出任何与推演无关的客套话或分析。\n\n"
        "{content}\n\n"
        "推演指令：{instruction}"
    ),
    "map_gen": (
        "【地图自动化生成模式】\n用户下令生成地图。你必须：\n"
        "1. 查阅世界快照，若目标地点的上级缺失，必须先建立最高级(世界)->星球->大陆等！\n"
        "2. 每个地点必须准确填写 parent_name（指向刚建立或已有的上级）。\n"
        "3. 务必为实体地点生成 map_x 和 map_y 坐标(0-24的整数)。\n"
        "【指令】\n{instruction}"
    ),
    "char_gen": (
        "【角色自动化生成模式】\n你必须：\n"
        "1. 严格使用嵌套字典格式填入 attributes。\n"
        "2. 参考已有模板：{character_template}。已有字段必须放回原分组！\n"
        "3. 在用户要求基础上，极大地丰富其背景血肉，并直接调用工具。\n"
        "【指令】\n{instruction}"
    ),
    "outline_gen": (
        "【剧情大纲自动生成模式】\n"
        "结合当前的世界观快照，为用户推演精彩的剧情大纲。要求逻辑严密，充满戏剧张力。无需调用元素管理工具。\n"
        "【指令】\n{instruction}"
    ),
    "continue_prefix": (
        "你是一位专业小说作家。请根据用户提供的上文，直接续写正文。\n"
        "【铁律】：不要解释、不要评价、不要问候语、不要输出任何非正文内容——只输出续写。\n"
        "保持与上文一致的文风、语气和叙事节奏。\n\n"
        "{lore}"
    ),
}


class CharacterRelation(Base):
    """人物关系连线 — 有向图"""
    __tablename__ = "character_relations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    novel_id: Mapped[int] = mapped_column(ForeignKey("novels.id"), nullable=False, default=1)
    source_id: Mapped[int] = mapped_column(ForeignKey("characters.id"), nullable=False)
    target_id: Mapped[int] = mapped_column(ForeignKey("characters.id"), nullable=False)
    label: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    color: Mapped[str] = mapped_column(String(20), nullable=False, default="")

    novel: Mapped[Novel] = relationship(back_populates="character_relations")
    source: Mapped[Character] = relationship(foreign_keys=[source_id])
    target: Mapped[Character] = relationship(foreign_keys=[target_id])


class GlobalSettings(Base):
    """全局设置 — 单例"""
    __tablename__ = "global_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prompt_templates: Mapped[dict] = mapped_column(JSON, nullable=False, default=DEFAULT_PROMPT_TEMPLATES)
