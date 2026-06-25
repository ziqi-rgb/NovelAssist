"""项目常量定义 — 领域枚举，前后端共享语义"""

from enum import StrEnum


class LocationScaleLevel(StrEnum):
    """地点层级 — 从宏观到微观的 8 级尺度"""
    GREAT_WORLD = "大千世界"
    UNIVERSE = "宇宙"
    PLANET = "星球"
    CONTINENT = "大陆"
    NATION = "国家"
    CITY = "城池"
    DISTRICT = "街区"
    BUILDING = "建筑"

    @classmethod
    def ordered(cls) -> list["LocationScaleLevel"]:
        return [
            cls.GREAT_WORLD, cls.UNIVERSE, cls.PLANET, cls.CONTINENT,
            cls.NATION, cls.CITY, cls.DISTRICT, cls.BUILDING,
        ]

    @classmethod
    def fuzzy_match(cls, raw: str) -> "LocationScaleLevel":
        """将 LLM 输出的模糊层级名映射到标准枚举值"""
        s = raw.lower()
        if "大千世界" in s:
            return cls.GREAT_WORLD
        if "世界" in s or "宇宙" in s or "universe" in s:
            return cls.UNIVERSE
        if "星球" in s or "planet" in s:
            return cls.PLANET
        if "大陆" in s or "continent" in s:
            return cls.CONTINENT
        if "国" in s or "empire" in s:
            return cls.NATION
        if "城" in s or "镇" in s or "city" in s or "town" in s:
            return cls.CITY
        if "村" in s or "village" in s or "街" in s or "建筑" in s or "building" in s:
            return cls.DISTRICT
        return cls.DISTRICT  # fallback


class OutlineCategory(StrEnum):
    """大纲分类"""
    WORLD = "世界观"
    FACTION = "世界势力"
    GEO = "地理"
    POWER = "能力体系设定"
    CHARACTER = "人物设定"
    STORY = "剧情大纲"

    # Legacy default in DB — not part of the active category set but used as
    # the SQLAlchemy column default (models.Outline.category).
    LEGACY_DEFAULT = "剧情"

    @classmethod
    def active(cls) -> list["OutlineCategory"]:
        """用户可见的 6 个标准分类"""
        return [
            cls.WORLD, cls.FACTION, cls.GEO, cls.POWER,
            cls.CHARACTER, cls.STORY,
        ]

    @classmethod
    def with_legacy(cls) -> list[str]:
        """包含历史遗留默认值的全量名称列表"""
        return [c.value for c in cls.active()] + [cls.LEGACY_DEFAULT.value]


class ElementType(StrEnum):
    """Agent 工具 manage_world_element 支持的元素类型"""
    OUTLINE = "outline"
    LOCATION = "location"
    TIMELINE = "timeline"
    FACTION = "faction"
    RELATION = "relation"
    CHARACTER = "character"
    VOLUME = "volume"
    CHARACTER_TEMPLATE = "character_template"

    @classmethod
    def all_values(cls) -> list[str]:
        return [e.value for e in cls]


# ═══════════ Compound constants ═══════════

SYSTEM_CHAIN_NAMES: list[str] = [
    "🗺️ 蓝图规划与逐层搭建流",
    "👤 严谨人设与模板质检流",
    "📖 全维世界观与大纲流水线",
]

# 元素类型 → 中文标签（用于 LLM prompt 和 UI 展示）
ELEMENT_TYPE_LABELS: dict[str, str] = {
    ElementType.CHARACTER: "人物",
    ElementType.LOCATION: "地点",
    ElementType.OUTLINE: "大纲",
    ElementType.FACTION: "势力",
    ElementType.TIMELINE: "时间线事件",
}
