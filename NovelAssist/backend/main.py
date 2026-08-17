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
import uuid
import webbrowser
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx


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
        DEFAULT_PROMPT_TEMPLATES, TimelineEventType, Faction, CharacterRelation, TimelineEventRelation, EventCategory,
    )
    from .world_calendar import CalendarEngine  # noqa: E402
    from .constants import LocationScaleLevel, SYSTEM_CHAIN_NAMES  # noqa: E402
except ImportError:
    from database import Base, engine, get_db, run_migration  # noqa: E402
    from models import (  # noqa: E402
        Chapter, Character, Outline, Location,
        WorldEvent, Novel, Volume, TimelineEvent, CharacterSnapshot, ReasoningChain, GlobalSettings,
        CharacterRoutine, CharacterTrajectory,
        DEFAULT_PROMPT_TEMPLATES, TimelineEventType, Faction, CharacterRelation, TimelineEventRelation, EventCategory,
    )
    from world_calendar import CalendarEngine  # noqa: E402
    from constants import LocationScaleLevel, SYSTEM_CHAIN_NAMES  # noqa: E402

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
            "name": "manage_world_element",
            "description": "对世界观元素进行全权增删改：人物(character)、大纲(outline)、地点(location)、时间线(timeline)、势力(faction)、关系(relation)、分卷(volume)。",
            "parameters": {
                "type": "object",
                "properties": {
                    "element_type": {"type": "string", "enum": ["outline", "location", "timeline", "faction", "relation", "character", "volume", "character_template"]},
                    "action": {"type": "string", "enum": ["add", "update", "delete"]},
                    "element_id": {"type": "integer", "description": "更新或删除时必填"},
                    "data": {
                        "type": "object",
                        "description": "⚠️【强制格式】：必须是标准的嵌套 JSON 对象(Object)，绝对不能是转义的字符串！新增/更新数据。\noutline: title/description/category/parent_name(父大纲标题)/order_index。\nlocation: name/description/scale(必须从大千世界,宇宙,星球,大陆,国家,城池,街区,建筑中选)/parent_id/parent_name(父地点名)/grid_x/grid_y(0-24整数的相对坐标)。\ncharacter: name/description/status/is_active(布尔)/faction_name(势力名)/faction_role/attributes(必须以嵌套字典结构传入,外层key为分组名如'基础信息',内层为具体属性键值对,可直接自创新分组)。\nvolume: title/order_index。\nfaction: name/description/base_location_name(驻地地点名)。\nrelation: source_name/target_name(人物名)/label/weight(-100~100)。\ntimeline: title(事件名称)/time_label(时间标签)/category(分类：地形变动/人物生死/政治事件/宝物现世/生物异动/自然演变/其他)/character_names(参与人物名称列表,如[\"张三\",\"李四\"],后端自动解析为ID)/content(事件详情)/event_type(history|main_story|world)/related_location_name(关联地点名)。",
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
                                "premise": {"type": "string", "description": "本节点的前置逻辑或目标说明。可使用系统内置占位符获取动态信息。"},
                                "prompt": {
                                    "type": "string",
                                    "description": (
                                        "发给AI的提示词命令。强烈建议使用系统内置占位符动态获取信息。\n"
                                        "【合法占位符白名单 — 仅限以下，禁止生造】:\n"
                                        "{content} - 正文框当前文本\n"
                                        "{characters_list} - 所有活跃人物的档案与时空坐标(含势力归属)\n"
                                        "{characters_inactive} - 已退场人物列表\n"
                                        "{characters_full_detail} - 全量角色精细档案(含attributes字典)\n"
                                        "{factions_list} - 势力与宗门目录(含成员)\n"
                                        "{relations_graph} - 人物关系网(有向连线)\n"
                                        "{timeline} - 正文时间线历史\n"
                                        "{timeline_main} - 正文时间线({timeline}等效别名)\n"
                                        "{timeline_history} - 历史时间线\n"
                                        "{timeline_world} - 世界时间线\n"
                                        "{outlines_list} - 世界大纲列表\n"
                                        "{outlines_full_detail} - 全维大纲目录(按分类分组)\n"
                                        "{locations_list} - 全量地点扁平名录\n"
                                        "{current_time} - 当前世界历法时间\n"
                                        "{lore} - 世界观基础设定集\n"
                                        "{character:名字} - 提取特定人物属性\n"
                                        "{location:地名} - 提取特定地点属性\n"
                                        "{outline:标题} - 提取特定大纲条目\n"
                                        "{novel_name} - 小说名称\n"
                                        "{chapter_title} - 当前章节标题\n"
                                        "{character_template} - 当前世界人物模板字段列表\n"
                                        "{locations_full_detail} - 高密度全维物理地图树\n"
                                        "{outline_cat_world} - 世界观分类大纲树\n"
                                        "{outline_cat_faction} - 世界势力分类大纲树\n"
                                        "{outline_cat_geo} - 地理分类大纲树\n"
                                        "{outline_cat_power} - 能力体系分类大纲树\n"
                                        "{outline_cat_story} - 剧情大纲分类树\n"
                                        "绝对禁止生造不在白名单内的 {xxx} 占位符！其他信息请用自然语言描述。"
                                    ),
                                },
                                "target": {
                                    "type": "array",
                                    "description": (
                                        "节点的结果指定(积木数组)。定义该节点向下一节点传递什么信息。\n"
                                        "如要传递全局信息(时间等)连同推理结果，请组合placeholder和text块。\n"
                                        "示例: [{\"type\":\"placeholder\",\"value\":\"current_time\"}, {\"type\":\"text\",\"value\":\"本节点的战力推演结果\"}]"
                                    ),
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "type": {
                                                "type": "string",
                                                "enum": ["text", "placeholder", "bool", "enum"],
                                                "description": "块类型: text(纯文本), placeholder(系统占位符,value填不带{}的名称如current_time), bool(条件判断), enum(选项分支)",
                                            },
                                            "value": {"type": "string", "description": "占位符名称(可带或不带大括号，如current_time或{current_time})或文本内容"},
                                        },
                                        "required": ["type", "value"],
                                    },
                                },
                            },
                            "required": ["premise", "prompt"],
                        },
                    },
                },
                "required": ["action", "title", "steps"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_chain_todos",
            "description": "管理当前推理链的任务清单（Todo List）。可新增任务、更新状态、追加备注。禁止删除任务。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["add", "update_status", "append_note"], "description": "add=新增任务, update_status=更新状态, append_note=追加备注"},
                    "todo_id": {"type": "string", "description": "任务ID（update_status/append_note 时必填）"},
                    "content": {"type": "string", "description": "任务内容（add 时必填）"},
                    "status": {"type": "string", "enum": ["pending", "in_progress", "done"], "description": "任务状态（update_status 时必填）"},
                    "note": {"type": "string", "description": "追加的备注文本（append_note 时必填）"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_world_state",
            "description": (
                "查询当前世界的实时状态数据。可查询人物、势力、关系图谱、地点、时间线、大纲。"
                "当需要了解世界当前局面、角色关系、势力分布、剧情规划时调用。\n"
                "【重要】大纲(outline)内容为初期规划，仅供参考。实际创作方向必须以用户的实时指令和当前正文内容为准，"
                "不可机械照搬大纲。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query_type": {
                        "type": "string",
                        "enum": ["characters", "factions", "relations", "locations", "timeline", "outline", "all"],
                        "description": "数据类型：characters(人物列表含势力归属), factions(势力目录含成员), relations(关系有向图谱), locations(地点列表), timeline(时间线), outline(大纲规划), all(全部)",
                    },
                    "filter": {"type": "string", "description": "可选的名称过滤关键词"},
                    "event_index": {"type": "integer", "description": "仅对 timeline 有效：指定要检索的时间线事件序号(timeline_index)，返回该事件的完整详情(title/time_label/content/event_type/absolute_tick/关联地点)。不填则返回全部事件。"},
                },
                "required": ["query_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "continue_writing",
            "description": (
                "调用DeepSeek对话前缀续写引擎续写正文。模型应自行提炼与当前情节相关的设定"
                "作为system_context传入（不要照搬整个设定集，只放对续写有用的信息——如当前场景的"
                "人物属性、地点描述、近期时间线事件、相关大纲）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "chapter_content": {"type": "string", "description": "当前章节的完整正文文本"},
                    "system_context": {"type": "string", "description": "注入system提示词的设定上下文（由模型自行从lore中提炼与当前情节相关的部分，如人物当前状态、所在场景、近期事件）"},
                },
                "required": ["chapter_content", "system_context"],
            },
        },
    },
]

# SYSTEM_CHAIN_NAMES imported from constants.py (line 213 area kept for reference but unused)


# ═══════════════════════════════════════════════════════════════════════════════
# AUTO-GENERATED node definitions — edit via backend/_gen_chains.js, not by hand.
# To regenerate:  node backend/_gen_chains.js  →  paste the output below.
# ═══════════════════════════════════════════════════════════════════════════════
def _get_system_chain_nodes(title: str) -> list[dict]:
    if title == "🗺️ 蓝图规划与逐层搭建流":
        stages = [
            ("world", "大千世界与宇宙", "大千世界→宇宙", "大千世界", "宇宙"),
            ("planet", "星球与大陆", "星球→大陆", "星球", "大陆"),
            ("nation", "国家", "国家", "国家", "国家"),
            ("city", "城池", "城池", "城池", "城池"),
            ("district", "街区", "街区", "街区", "街区"),
            ("building", "建筑", "具体地点", "建筑", "具体地点"),
        ]
        nodes: list[dict] = []
        _C = {
            "m_plan": (30, -450),
            "m_todos": (890, -450),
            "m_world_pick": (-339, 79),
            "m_world_create": (35, 26),
            "m_world_write": (474, 45),
            "m_world_validate": (890, 20),
            "m_world_todos": (1326, 38),
            "m_world_verify": (1743, 17),
            "m_world_total": (2133, 81),
            "m_planet_pick": (-339, 535),
            "m_planet_create": (30, 500),
            "m_planet_write": (460, 500),
            "m_planet_validate": (890, 500),
            "m_planet_todos": (1358, 500),
            "m_planet_verify": (1745, 513),
            "m_planet_total": (2168, 562),
            "m_nation_pick": (-336, 1030),
            "m_nation_create": (30, 1000),
            "m_nation_write": (460, 1000),
            "m_nation_validate": (890, 1000),
            "m_nation_todos": (1320, 1000),
            "m_nation_verify": (1750, 1000),
            "m_nation_total": (2176, 1039),
            "m_city_pick": (-340, 1556),
            "m_city_create": (30, 1500),
            "m_city_write": (460, 1500),
            "m_city_validate": (890, 1500),
            "m_city_todos": (1320, 1500),
            "m_city_verify": (1750, 1500),
            "m_city_total": (2179, 1539),
            "m_district_pick": (-341, 2037),
            "m_district_create": (30, 2000),
            "m_district_write": (460, 2000),
            "m_district_validate": (890, 2000),
            "m_district_todos": (1320, 2000),
            "m_district_verify": (1750, 2000),
            "m_district_total": (2178, 2022),
            "m_building_pick": (-343, 2523),
            "m_building_create": (30, 2500),
            "m_building_write": (460, 2500),
            "m_building_validate": (890, 2500),
            "m_building_todos": (1320, 2500),
            "m_building_verify": (1750, 2500),
            "m_building_total": (2174, 2526),
        }
        # Row -450: init
        nodes.append({"id":"m_plan","x":_C["m_plan"][0],"y":_C["m_plan"][1],"premise":"明确需要建立的核心地点清单。","prompt":"【全域地点规划】\n请根据用户输入和已有设定，列出需要创建的核心地点名称清单。不要无节制地列出一百个地点——只列与用户故事直接相关的关键地点，控制在 20 个以内。\n\n格式：\n- [ ] 地点名 (层级: 大千世界/星球/大陆/国家/城池/街区/建筑, 父级: xxx)\n不要输出详细属性——详细内容将由后续节点逐个生成。\n\n⚠️ 层级必须严格从高到低，父级只能比自身高一个层级：\n  大千世界→宇宙→星球→大陆→国家→城池→街区→建筑\n  例：国家只能以大陆为父级，大陆只能以星球为父级，不能跨级或逆级\n⚠️ 必须自顶向下覆盖：从最高层级（大千世界）开始，逐级向下顺延到目标建筑。即使只需要一栋建筑，也必须列出其完整层级链（街区→城池→国家→大陆→星球→大千世界）。允许适当编纂中间层级的名称和属性。\n【全景对齐】：100% 参照已有地理数据：{locations_full_detail}。\n末尾输出 <ROUTE>DONE</ROUTE>。","format":"","target":[],"branches":[],"next_node_id":"m_todos","output":""})
        nodes.append({"id":"m_todos","type":"write","x":_C["m_todos"][0],"y":_C["m_todos"][1],"premise":"将所有地点写入推理链待办清单。","prompt":"","target":[],"branches":[],"next_node_id":"m_world_pick","output":"","write_to":{"entity":"chain_todos","field":"content","action":"create"}})
        for i, (s_id, s_label, s_scales, s_top, s_bottom) in enumerate(stages):
            next_s_id = stages[i + 1][0] if i + 1 < len(stages) else "end"
            # 层级 verify filter
            v_filter = s_top if s_top == s_bottom else f"{s_top}|{s_bottom}"
            # 🎯 程序选取待办
            nodes.append({"id":f"m_{s_id}_pick","type":"pick_todo","x":_C[f"m_{s_id}_pick"][0],"y":_C[f"m_{s_id}_pick"][1],"premise":f"选取【{s_label}】层级第一个未完成待办","pick_filter":v_filter,"branches":[{"condition":"HAS_TODO","next_node_id":f"m_{s_id}_create"},{"condition":"NO_TODO","next_node_id":f"m_{s_id}_verify"}],"next_node_id":"","output":""})
            # 创建节点（不再负责选取待办）
            nodes.append({"id":f"m_{s_id}_create","x":_C[f"m_{s_id}_create"][0],"y":_C[f"m_{s_id}_create"][1],"premise":"为选取的待办项生成精炼属性。","prompt":f"【{s_label} 地点创建】\n层级标尺: {s_scales}。最顶层从 {s_top} 开始，最底层到 {s_bottom}。\n已有地点: {{locations_full_detail}}\n\n请根据上方「选取待办」传入的条目名称，为其生成精炼的属性字典。description 不超过 300 字，其他字段值不超过 100 字。只写核心设定，不要叙事展开。","format":"location","target":[],"branches":[],"next_node_id":f"m_{s_id}_write","output":""})
            nodes.append({"id":f"m_{s_id}_write","type":"write","x":_C[f"m_{s_id}_write"][0],"y":_C[f"m_{s_id}_write"][1],"premise":f"将生成的【{s_label}】地点写入数据库。","prompt":"","target":[],"branches":[],"next_node_id":f"m_{s_id}_todos","output":"","write_to":{"entity":"location","field":"description","action":"create"}})
            nodes.append({"id":f"m_{s_id}_todos","type":"write","x":_C[f"m_{s_id}_todos"][0],"y":_C[f"m_{s_id}_todos"][1],"premise":"将待办中对应项标记为已完成。","prompt":"","target":[],"branches":[],"next_node_id":f"m_{s_id}_verify","output":"","write_to":{"entity":"chain_todos","field":"status","action":"update"}})
            nodes.append({"id":f"m_{s_id}_verify","type":"verify","x":_C[f"m_{s_id}_verify"][0],"y":_C[f"m_{s_id}_verify"][1],"premise":f"校验：【{s_label}】层级是否全部完成？","verify_filter":v_filter,"branches":[{"condition":"CONTINUE","next_node_id":f"m_{s_id}_pick"},{"condition":"NEXT","next_node_id":f"m_{s_id}_total"}],"next_node_id":"","output":""})
            # 总 verify
            first_s_id = stages[0][0]
            if next_s_id != "end":
                nodes.append({"id":f"m_{s_id}_total","type":"verify","x":_C[f"m_{s_id}_total"][0],"y":_C[f"m_{s_id}_total"][1],"premise":"全部层级是否已完成？","verify_filter":"","branches":[{"condition":"CONTINUE","next_node_id":f"m_{next_s_id}_pick"},{"condition":"DONE","next_node_id":""}],"next_node_id":"","output":""})
            else:
                nodes.append({"id":f"m_{s_id}_total","type":"verify","x":_C[f"m_{s_id}_total"][0],"y":_C[f"m_{s_id}_total"][1],"premise":"全部层级是否已完成？","verify_filter":"","branches":[{"condition":"CONTINUE","next_node_id":f"m_{first_s_id}_pick"},{"condition":"DONE","next_node_id":""}],"next_node_id":"","output":""})
        return nodes
    if title == "👤 严谨人设与模板质检流":
        X1, X2 = 400, 890  # 两列布局
        return [
            {
                "id": "c_plan", "x": X1, "y": 60,
                "premise": "确认需要创建哪些角色，列出完整名单。",
                "prompt": "【角色名单确认】\n根据用户输入，列出需要创建的核心角色姓名。控制在 15 个以内，只列与故事直接相关的角色。每个角色注明所属势力（无则写'散修'）。格式：\n- [ ] 角色名 (所属势力)\n末尾输出 <ROUTE>DONE</ROUTE>。",
                "target": [], "branches": [], "next_node_id": "c_todos", "output": ""
            },
            {
                "id": "c_todos", "type": "write", "x": X2, "y": 60,
                "premise": "将角色名单写入推理链待办清单。",
                "prompt": "", "target": [], "branches": [], "next_node_id": "c_snapshot", "output": "",
                "write_to": {"entity": "chain_todos", "field": "content", "action": "create"}
            },
            {
                "id": "c_snapshot", "type": "program", "x": X2, "y": 250,
                "premise": "冻结当前人物属性模板，后续校验均以此快照为基准。",
                "prompt": "", "target": [], "branches": [], "next_node_id": "c_pick", "output": "",
                "program_action": "capture_template"
            },
            {
                "id": "c_pick", "type": "pick_todo", "x": X1-50, "y": 450,
                "premise": "选取第一个未完成的角色待办",
                "pick_filter": "",
                "branches": [
                    {"condition": "HAS_TODO", "next_node_id": "c_create"},
                    {"condition": "NO_TODO", "next_node_id": "c_verify"}
                ],
                "next_node_id": "", "output": ""
            },
            {
                "id": "c_create", "x": X1, "y": 550,
                "premise": "为选取的待办项生成完整角色属性。",
                "prompt": "【角色属性生成】\n系统模板：{character_template}\n已有角色：{characters_full_detail}\n\n请根据上方「选取待办」传入的角色名称，为其生成精炼的属性字典。\n\n⚠️ 输出规范：\n- 只能输出以下结构化格式，绝对不要输出任何叙事文本、场景描写、对话或续写内容\n- 每个字段值不超过 200 字，description 不超过 500 字\n- 只写核心设定，不要填充无信息量的套话\n- 所有字段必须填写具体内容，禁止输出「未知」「不详」「待定」等占位词——信息不足时请根据已有设定合理推测\n- 【name】角色名\n- 【aliases】别名\n- 【status】存活/阵亡/飞升/失踪\n- 【is_active】true/false\n- 【faction_name】必须填写所属势力全名！参考已有角色和世界观中的势力名称。无势力归属的散人填「散修」，不要填「无」或留空\n- 【faction_role】在势力中的职位/身份\n- 【is_always_context】true/false\n- 【attributes】严格只包含模板中定义的分组和字段，不得新增任何分组名或字段名\n- 【description】一句话概述角色核心特质（非叙事）\n末尾输出 <ROUTE>DONE</ROUTE>",
                "format": "character", "target": [], "branches": [], "next_node_id": "c_validate", "output": ""
            },
            {
                "id": "c_validate", "type": "validate", "x": X1, "y": 750,
                "premise": "校验角色属性是否与模板快照完全吻合（不多不少）。",
                "prompt": "", "format": "character", "target": [], "branches": [
                    {"condition": "VALID", "next_node_id": "c_write"},
                    {"condition": "INVALID", "next_node_id": "c_fix"}
                ],
                "next_node_id": "", "output": ""
            },
            {
                "id": "c_fix", "x": X1, "y": 950,
                "premise": "根据校验反馈修正角色属性。",
                "prompt": "【角色属性修正】\n系统模板：{character_template}\n已有角色：{characters_full_detail}\n\n上方的校验未通过，请根据错误信息修正角色属性，严格按模板填写。\n禁止输出「未知」「不详」「待定」——信息不足时合理推测。\n末尾输出 <ROUTE>DONE</ROUTE>。",
                "format": "character", "target": [], "branches": [], "next_node_id": "c_validate", "output": ""
            },
            {
                "id": "c_write", "type": "write", "x": X2, "y": 750,
                "premise": "将生成的完整角色属性写入数据库（势力自动关联）。",
                "prompt": "", "target": [], "branches": [], "next_node_id": "c_todos_update", "output": "",
                "write_to": {"entity": "character", "field": "attributes", "action": "create"}
            },
            {
                "id": "c_todos_update", "type": "write", "x": X2, "y": 950,
                "premise": "将待办中对应角色标记为已完成。",
                "prompt": "", "target": [], "branches": [], "next_node_id": "c_verify", "output": "",
                "write_to": {"entity": "chain_todos", "field": "status", "action": "update"}
            },
            {
                "id": "c_verify", "type": "verify", "x": X1, "y": 1150,
                "premise": "校验：所有角色是否已全部创建完成？",
                "verify_filter": "",
                "branches": [
                    {"condition": "CONTINUE", "next_node_id": "c_pick"},
                    {"condition": "DONE", "next_node_id": ""}
                ],
                "next_node_id": "", "output": ""
            }
        ]
    if title == "📖 全维世界观与大纲流水线":
        modules = [
            ("world", "世界观设定", "世界观", True),
            ("faction", "世界势力设定", "世界势力", True),
            ("geo", "地理知识设定", "地理", True),
            ("char", "人物设定（简略）", "人物设定", False),
            ("power", "能力体系设定", "能力体系设定", True),
        ]
        contracts = {
            "世界观": "阐述世界的底层物理定律、超凡法则、文明起源、历史纪元。",
            "世界势力": "定义世界中割据或隐藏的门派、宗族、联邦、商会等社会组织。",
            "地理": "描绘大陆、国度、禁地、城池等地理实体的分布。",
            "人物设定": "列出非核心主角的简略人设概述，不展开详细属性。",
            "能力体系设定": "系统化拆解科技水平、超凡体系、修行境界、等级晋升要求。包括但不限于：世界观内的科技树（如生物改造技术、契约能量应用）、超凡能力的来源与分级、员工的技能培训与考核体系。",
        }
        nodes: list[dict] = []
        _C = {
            "o_plan": (30, -450),
            "o_todos": (890, -450),
            "o_world_pick": (-332, 51),
            "o_world_create": (35, 35),
            "o_world_write": (460, 26),
            "o_world_validate": (890, -16),
            "o_world_todos": (1320, -2),
            "o_world_verify": (1750, -9),
            "o_world_total": (2189, 66),
            "o_faction_pick": (-341, 528),
            "o_faction_create": (30, 500),
            "o_faction_write": (460, 500),
            "o_faction_validate": (890, 500),
            "o_faction_todos": (1352, 494),
            "o_faction_verify": (1753, 506),
            "o_faction_total": (2183, 530),
            "o_geo_pick": (-343, 1028),
            "o_geo_create": (30, 1000),
            "o_geo_write": (460, 1000),
            "o_geo_validate": (890, 1000),
            "o_geo_todos": (1320, 1000),
            "o_geo_verify": (1750, 1000),
            "o_geo_total": (2180, 1024),
            "o_char_pick": (-336, 1515),
            "o_char_create": (30, 1500),
            "o_char_write": (460, 1500),
            "o_char_validate": (890, 1500),
            "o_char_todos": (1320, 1500),
            "o_char_verify": (1750, 1500),
            "o_char_total": (2183, 1516),
            "o_power_pick": (-336, 2019),
            "o_power_create": (30, 2000),
            "o_power_write": (460, 2000),
            "o_power_validate": (890, 2000),
            "o_power_todos": (1320, 2000),
            "o_power_verify": (1750, 2000),
            "o_power_total": (2190, 2017),
        }
        # Row -450: init
        nodes.append({"id":"o_plan","x":_C["o_plan"][0],"y":_C["o_plan"][1],"premise":"规划核心大纲条目清单。","prompt":"【全域大纲规划】\n请根据用户输入和已有设定，列出需要创建的核心大纲条目名称清单。控制在 30 个以内，只列与故事直接相关的关键条目。\n\n格式：\n- [ ] 条目名 (分类: 世界观设定/世界势力/地理知识/人物设定/能力体系设定)\n不要输出详细描述——详细内容将由后续节点逐个生成。\n\n模块包括：世界观设定、世界势力、地理知识、人物设定（简略）、能力体系设定。\n【全景对齐】：100% 参照已有数据：\n地理位置：{locations_full_detail}\n人物档案：{characters_full_detail}\n末尾输出 <ROUTE>DONE</ROUTE>。","format":"","target":[],"branches":[],"next_node_id":"o_todos","output":""})
        nodes.append({"id":"o_todos","type":"write","x":_C["o_todos"][0],"y":_C["o_todos"][1],"premise":"将所有大纲条目写入推理链待办清单。","prompt":"","target":[],"branches":[],"next_node_id":"o_world_pick","output":"","write_to":{"entity":"chain_todos","field":"content","action":"create"}})
        for i, (m_id, m_name, cat, use_format) in enumerate(modules):
            next_m_id = modules[i + 1][0] if i + 1 < len(modules) else "end"
            fmt = "outline" if use_format else ""
            contract = contracts.get(cat, "")
            # 🎯 程序选取待办
            nodes.append({"id":f"o_{m_id}_pick","type":"pick_todo","x":_C[f"o_{m_id}_pick"][0],"y":_C[f"o_{m_id}_pick"][1],"premise":f"选取【{m_name}】分类第一个未完成待办","pick_filter":cat,"branches":[{"condition":"HAS_TODO","next_node_id":f"o_{m_id}_create"},{"condition":"NO_TODO","next_node_id":f"o_{m_id}_verify"}],"next_node_id":"","output":""})
            # 创建节点（不再负责选取待办）
            nodes.append({"id":f"o_{m_id}_create","x":_C[f"o_{m_id}_create"][0],"y":_C[f"o_{m_id}_create"][1],"premise":"为选取的待办项生成精炼内容。","prompt":f"【{m_name} 条目创建】\n硬性契约：{contract}\n已有大纲：{{outlines_full_detail}}\n已有地理：{{locations_full_detail}}\n已有人物：{{characters_full_detail}}\n\n请根据上方「选取待办」传入的条目名称，为其生成精炼的 title、category、description（Markdown 格式）。description 不超过 500 字，只写核心设定，不要叙事展开。条目可以存在父子嵌套关系——通过 parent_name 字段指定父级（如'修真体系'的子条目'筑基期'写 parent_name: 修真体系）。顶级条目 parent_name 写'无'。","format":fmt,"target":[],"branches":[],"next_node_id":f"o_{m_id}_write","output":""})
            nodes.append({"id":f"o_{m_id}_write","type":"write","x":_C[f"o_{m_id}_write"][0],"y":_C[f"o_{m_id}_write"][1],"premise":f"将生成的【{m_name}】条目写入 {cat} 分类。","prompt":"","target":[],"branches":[],"next_node_id":f"o_{m_id}_todos","output":"","write_to":{"entity":"outline","field":"category","sub_field":cat,"action":"create"}})
            nodes.append({"id":f"o_{m_id}_todos","type":"write","x":_C[f"o_{m_id}_todos"][0],"y":_C[f"o_{m_id}_todos"][1],"premise":"将待办中对应项标记为已完成。","prompt":"","target":[],"branches":[],"next_node_id":f"o_{m_id}_verify","output":"","write_to":{"entity":"chain_todos","field":"status","action":"update"}})
            nodes.append({"id":f"o_{m_id}_verify","type":"verify","x":_C[f"o_{m_id}_verify"][0],"y":_C[f"o_{m_id}_verify"][1],"premise":f"校验：【{m_name}】是否全部完成？","verify_filter":cat,"branches":[{"condition":"CONTINUE","next_node_id":f"o_{m_id}_pick"},{"condition":"NEXT","next_node_id":f"o_{m_id}_total"}],"next_node_id":"","output":""})
            # 总 verify
            first_m_id = modules[0][0]
            if next_m_id != "end":
                nodes.append({"id":f"o_{m_id}_total","type":"verify","x":_C[f"o_{m_id}_total"][0],"y":_C[f"o_{m_id}_total"][1],"premise":"全部模块是否已完成？","verify_filter":"","branches":[{"condition":"CONTINUE","next_node_id":f"o_{next_m_id}_pick"},{"condition":"DONE","next_node_id":""}],"next_node_id":"","output":""})
            else:
                nodes.append({"id":f"o_{m_id}_total","type":"verify","x":_C[f"o_{m_id}_total"][0],"y":_C[f"o_{m_id}_total"][1],"premise":"全部模块是否已完成？","verify_filter":"","branches":[{"condition":"CONTINUE","next_node_id":f"o_{first_m_id}_pick"},{"condition":"DONE","next_node_id":""}],"next_node_id":"","output":""})
        return nodes
    return []


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    run_migration()
    # Seed default novel — ensure at least one novel always exists
    try:
        from .database import SessionLocal
    except ImportError:
        from database import SessionLocal
    with SessionLocal() as db0:
        existing = db0.scalars(select(Novel).limit(1)).first()
        if not existing:
            default_novel = Novel(title="默认小说")
            db0.add(default_novel)
            db0.commit()
            print("[Seed] 已创建默认小说项目")
    # Data healing: normalize timeline_events event_type to enum names (MAIN_STORY/HISTORY/WORLD)
    with SessionLocal() as db:
        db.execute(text("UPDATE timeline_events SET event_type = 'MAIN_STORY' WHERE UPPER(event_type) = 'MAIN_STORY' AND event_type != 'MAIN_STORY'"))
        db.execute(text("UPDATE timeline_events SET event_type = 'HISTORY' WHERE UPPER(event_type) = 'HISTORY' AND event_type != 'HISTORY'"))
        db.execute(text("UPDATE timeline_events SET event_type = 'WORLD' WHERE UPPER(event_type) = 'WORLD' AND event_type != 'WORLD'"))
        db.commit()
        # Auto-heal coordinate conflicts across all locations
        _auto_heal_all_coord_conflicts(db)
    # Prompt auto-heal in independent session
    with SessionLocal() as db2:
        gs = db2.get(GlobalSettings, 1)
        if gs and isinstance(gs.prompt_templates, dict):
            tpl = dict(gs.prompt_templates)
            healed = False
            if "archive_timeline" not in tpl or "\"events\"" not in tpl["archive_timeline"]:
                tpl["archive_timeline"] = DEFAULT_PROMPT_TEMPLATES["archive_timeline"]
                healed = True
            if "archive_character" not in tpl or "faction_change" not in tpl["archive_character"]:
                tpl["archive_character"] = DEFAULT_PROMPT_TEMPLATES["archive_character"]
                healed = True
            if healed:
                gs.prompt_templates = tpl
                flag_modified(gs, "prompt_templates")
                db2.commit()
    # 🛡️ 创世系统推理链自愈热修复迁移器 (Chain Auto-Migration Hotfix)
    # 此迁移仅执行一次：检测并修复旧版节点结构
    try:
        with SessionLocal() as db_hf:
            # 版本标记检测：缺少 _version 或旧版本的链全部迁移
            for chain_title in SYSTEM_CHAIN_NAMES:
                chains_to_migrate = db_hf.query(ReasoningChain).filter(ReasoningChain.title == chain_title).all()
                for t_chain in chains_to_migrate:
                    if not isinstance(t_chain.nodes, list):
                        continue
                    cs = t_chain.context_settings if isinstance(t_chain.context_settings, dict) else {}
                    ver = cs.get("_version", 0)
                    if ver is None or ver != 2:
                        print(f"[Hotfix Migration] 链「{chain_title}」(id={t_chain.id}) 版本 {ver}→19，执行迁移...")
                        t_chain.nodes = _get_system_chain_nodes(chain_title)
                        t_chain.todos = []  # 清空旧待办，防止残留 pending 干扰 verify
                        cs = dict(t_chain.context_settings) if isinstance(t_chain.context_settings, dict) else {}
                        cs["_version"] = 2
                        t_chain.context_settings = cs
                        flag_modified(t_chain, "nodes")
                        flag_modified(t_chain, "todos")
                        flag_modified(t_chain, "context_settings")
            db_hf.commit()
    except Exception as migration_error:
        print(f"[Hotfix Migration Warning] 启动迁移器报错（已安全拦截）: {str(migration_error)}")
    # Seed official reasoning chains — protected: only create if not exists
    with SessionLocal() as db3:
        legacy = [
            "🤖 智能建图 SOP",
            "🗺️ 智能建图判定流",
            "🗺️ 深度生态与地图建构流",
            "🗺️ 严谨建图层级推演流",
            "👤 深度人设与关系推演流",
            "👤 深度人设与势力羁绊流",
            "📖 剧情大纲多幕结构流",
        ]
        db3.query(ReasoningChain).filter(ReasoningChain.title.in_(legacy)).delete(synchronize_session=False)
        db3.commit()

        system_chains = [
            "🗺️ 蓝图规划与逐层搭建流",
            "👤 严谨人设与模板质检流",
            "📖 全维世界观与大纲流水线",
        ]
        for name in system_chains:
            existing = db3.query(ReasoningChain).filter(ReasoningChain.title == name).first()
            if not existing:
                nodes = _get_system_chain_nodes(name)
                db3.add(ReasoningChain(
                    title=name,
                    context_settings={"use_outlines": False, "use_characters": False, "use_timeline": False, "_version": 2},
                    nodes=nodes,
                ))
                db3.commit()
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
    faction_id: Optional[int] = None
    faction_role: str = ""

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
    faction_id: Optional[int] = None
    faction_role: str = ""
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
    title: str = ""
    time_label: str = ""
    category: str = "其他"
    character_ids: list[int] = []
    character_names: list[str] = []  # resolved to IDs if character_ids is empty
    content: str = ""
    related_location_id: Optional[int] = None
    related_location_name: Optional[str] = None


def _normalize_timeline_type(raw: str) -> TimelineEventType:
    """Convert any case variant of event_type string to TimelineEventType enum."""
    upper = raw.strip().upper()
    if upper in ("HISTORY",):
        return TimelineEventType.HISTORY
    if upper in ("WORLD",):
        return TimelineEventType.WORLD
    return TimelineEventType.MAIN_STORY


def _resolve_character_ids(db: Session, novel_id: int, ids: list[int] | None, names: list[str] | None) -> list[int]:
    """Resolve character references to IDs. Names take priority if ids is empty."""
    if ids:
        return ids
    if not names:
        return []
    result: list[int] = []
    for name in names:
        ch = db.query(Character).filter(
            Character.novel_id == novel_id, Character.name == name.strip()
        ).first()
        if ch:
            result.append(ch.id)
    return result

class TimelineOut(BaseModel):
    id: int
    novel_id: int
    event_type: str
    timeline_index: int = 0
    title: str = ""
    time_label: str
    category: str = "其他"
    character_ids: list = []
    content: str
    absolute_tick: int = 0
    related_location_id: Optional[int] = None
    created_at: Optional[datetime] = None
    class Config:
        from_attributes = True


class TimelineRelationIn(BaseModel):
    source_event_id: int
    target_event_id: int
    label: str = "导致"
    description: str = ""


class TimelineRelationOut(BaseModel):
    id: int
    novel_id: int
    source_event_id: int
    target_event_id: int
    label: str
    description: str = ""
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
    todos: list = []
    class Config:
        from_attributes = True


# ═══════════ Novels ═══════════

@app.get("/api/novels", response_model=list[NovelOut])
def list_novels(db: Session = Depends(get_db)):
    return db.scalars(select(Novel).order_by(Novel.id)).all()

@app.get("/api/novels/{novel_id}", response_model=NovelOut)
def get_novel(novel_id: int, db: Session = Depends(get_db)):
    n = db.get(Novel, novel_id)
    if n is None:
        raise HTTPException(404, "小说不存在")
    return n

@app.post("/api/novels", response_model=NovelOut)
def create_novel(body: NovelCreate, db: Session = Depends(get_db)):
    n = Novel(title=body.title)
    db.add(n)
    db.commit()
    db.refresh(n)
    return n


@app.delete("/api/novels/{novel_id}")
def delete_novel(novel_id: int, db: Session = Depends(get_db)):
    """删除整本小说及其全部关联数据"""
    novel = db.get(Novel, novel_id)
    if novel is None:
        raise HTTPException(404, "小说不存在")

    # 级联删除顺序：从叶子到根，避免外键约束冲突
    db.execute(text("DELETE FROM character_snapshots WHERE character_id IN (SELECT id FROM characters WHERE novel_id=:nid)"), {"nid": novel_id})
    db.execute(text("DELETE FROM character_routines WHERE character_id IN (SELECT id FROM characters WHERE novel_id=:nid)"), {"nid": novel_id})
    db.execute(text("DELETE FROM character_trajectories WHERE character_id IN (SELECT id FROM characters WHERE novel_id=:nid)"), {"nid": novel_id})
    db.execute(text("DELETE FROM character_relations WHERE novel_id=:nid"), {"nid": novel_id})
    db.execute(text("DELETE FROM characters WHERE novel_id=:nid"), {"nid": novel_id})
    db.execute(text("DELETE FROM timeline_events WHERE novel_id=:nid"), {"nid": novel_id})
    db.execute(text("DELETE FROM world_events WHERE novel_id=:nid"), {"nid": novel_id})
    db.execute(text("DELETE FROM chapters WHERE novel_id=:nid"), {"nid": novel_id})
    db.execute(text("DELETE FROM volumes WHERE novel_id=:nid"), {"nid": novel_id})
    db.execute(text("DELETE FROM outlines WHERE novel_id=:nid"), {"nid": novel_id})
    db.execute(text("DELETE FROM locations WHERE novel_id=:nid"), {"nid": novel_id})
    db.execute(text("DELETE FROM factions WHERE novel_id=:nid"), {"nid": novel_id})
    db.execute(text("DELETE FROM reasoning_chains WHERE novel_id=:nid AND title NOT IN ('🗺️ 蓝图规划与逐层搭建流', '👤 严谨人设与模板质检流', '📖 全维世界观与大纲流水线')"), {"nid": novel_id})
    db.execute(text("DELETE FROM novels WHERE id=:nid"), {"nid": novel_id})

    db.commit()

    # 将幸存系统链重新绑定到剩余的第一本小说
    remaining = db.scalars(select(Novel).order_by(Novel.id).limit(1)).first()
    if remaining:
        db.execute(text(
            "UPDATE reasoning_chains SET novel_id=:new_nid WHERE novel_id=:old_nid"
        ), {"new_nid": remaining.id, "old_nid": novel_id})
        db.commit()

    return {"ok": True, "deleted_novel_id": novel_id}


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


@app.post("/api/novels/{novel_id}/genesis/clear")
def genesis_clear(novel_id: int, db: Session = Depends(get_db)):
    """覆写建纲前置：清空大纲/地点/人物/势力及其关联数据"""
    novel = db.get(Novel, novel_id)
    if novel is None:
        raise HTTPException(404, "小说不存在")

    # 级联删除：从叶子到根
    db.execute(text("DELETE FROM character_snapshots WHERE character_id IN (SELECT id FROM characters WHERE novel_id=:nid)"), {"nid": novel_id})
    db.execute(text("DELETE FROM character_routines WHERE character_id IN (SELECT id FROM characters WHERE novel_id=:nid)"), {"nid": novel_id})
    db.execute(text("DELETE FROM character_trajectories WHERE character_id IN (SELECT id FROM characters WHERE novel_id=:nid)"), {"nid": novel_id})
    db.execute(text("DELETE FROM character_relations WHERE novel_id=:nid"), {"nid": novel_id})
    db.execute(text("DELETE FROM characters WHERE novel_id=:nid"), {"nid": novel_id})
    db.execute(text("DELETE FROM outlines WHERE novel_id=:nid"), {"nid": novel_id})
    db.execute(text("DELETE FROM locations WHERE novel_id=:nid"), {"nid": novel_id})
    db.execute(text("DELETE FROM factions WHERE novel_id=:nid"), {"nid": novel_id})
    db.commit()

    return {"ok": True, "msg": f"已清空小说 {novel_id} 的大纲/地点/人物/势力数据"}


@app.post("/api/novels/{novel_id}/genesis/logs")
async def genesis_save_logs(novel_id: int, body: dict, db: Session = Depends(get_db)):
    """实时写入建纲日志到文件（支持追加模式）"""
    logs = body.get("logs", "")
    chain_title = body.get("chain_title", "unknown")
    append = body.get("append", False)
    existing_path = body.get("log_path", "")
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    if append and existing_path:
        log_path = Path(existing_path)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(logs)
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_title = chain_title.replace(" ", "_").replace("/", "_")[:40]
        log_path = log_dir / f"genesis_{novel_id}_{safe_title}_{timestamp}.log"
        log_path.write_text(logs, encoding="utf-8")
    return {"ok": True, "path": str(log_path)}


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
    selection_start: int = 0
    selection_end: int = 0
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

    gs = db.get(GlobalSettings, 1)
    templates = gs.prompt_templates if gs else DEFAULT_PROMPT_TEMPLATES

    client = AsyncOpenAI(api_key=api_key, base_url=base_url or None)

    # Build full text: accumulated pending + current selection
    pending = ch.pending_timeline_text.strip() if ch.pending_timeline_text else ""
    full_text = (pending + "\n\n" + text).strip() if pending else text

    # ── Parse time-passage markers BEFORE AI call ──
    novel = db.get(Novel, ch.novel_id)
    cfg = novel.calendar_config if novel and novel.calendar_config else {}
    marker_ticks, markers_found = CalendarEngine.parse_time_markers(full_text, cfg)

    # Strip markers from text sent to AI (time info goes via {detected_time})
    clean_full_text = CalendarEngine.strip_time_markers(full_text)

    # Build detected-time description for the AI prompt
    if markers_found:
        detected_parts = [f"- {m['description']} (+{m['ticks']} ticks)" for m in markers_found]
        detected_time_str = "正文中检测到以下时间流逝标记：\n" + "\n".join(detected_parts) + f"\n总计推进：{marker_ticks} ticks"
    else:
        detected_time_str = "本次归档未检测到【时间流逝】标记，时间不推进。请基于正文内容正常提取事件。"

    # 2. Timeline summary — time is marker-driven, AI only extracts events
    elapsed_years = 0
    elapsed_months = 0
    elapsed_days = 0
    elapsed_hours = 0
    timeline_added = False
    timeline_events_added: list[str] = []
    timeline_error: Optional[str] = None
    pending_timeline_events: list[TimelineEvent] = []
    pending_tick_advance = marker_ticks  # Time from markers, NOT from AI

    # Compute elapsed breakdown from markers for the response
    if marker_ticks > 0:
        hpd = cfg.get("hours_per_day", 24)
        dpm = cfg.get("days_per_month", 30)
        mpy = cfg.get("months_per_year", 12)
        remaining = marker_ticks
        elapsed_years = remaining // (mpy * dpm * hpd)
        remaining %= (mpy * dpm * hpd)
        elapsed_months = remaining // (dpm * hpd)
        remaining %= (dpm * hpd)
        elapsed_days = remaining // hpd
        elapsed_hours = remaining % hpd

    try:
        tl_prompt = templates.get("archive_timeline", DEFAULT_PROMPT_TEMPLATES["archive_timeline"])
        tl_prompt = tl_prompt.replace("{detected_time}", detected_time_str).replace("{text}", full_text)
        tl_resp = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": tl_prompt}],
            temperature=0.5,
        )
        raw_tl = (tl_resp.choices[0].message.content or "").strip()
        # Robust JSON extraction — handle markdown fences and bare JSON
        mj = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw_tl, re.DOTALL)
        if mj:
            raw_clean = mj.group(1).strip()
        else:
            raw_clean = raw_tl
        try:
            parsed_tl = json.loads(raw_clean)
        except json.JSONDecodeError:
            raise HTTPException(422, f"归档时间线JSON解析失败。LLM原始输出:\n{raw_tl[:300]}")
        if isinstance(parsed_tl, dict):
            # NOTE: AI's elapsed_years/months/days/hours fields are IGNORED.
            # Time advancement is exclusively driven by 【时间流逝】 markers.
            tick_increment = pending_tick_advance
            cal_time = format_ticks_to_calendar((novel.current_tick if novel else 0) + tick_increment)
            events_list = parsed_tl.get("events", [])
            for evt in events_list:
                max_idx = db.scalar(
                    select(func.max(TimelineEvent.timeline_index)).where(
                        TimelineEvent.novel_id == ch.novel_id,
                        TimelineEvent.event_type == TimelineEventType.MAIN_STORY.name,
                    )
                )
                new_idx = (max_idx or 0) + 1
                # Extract structured fields
                evt_title = str(evt.get("title", "")).strip()
                evt_category = str(evt.get("category", "其他")).strip()
                evt_location = str(evt.get("location", "")).strip()
                evt_desc = str(evt.get("event", "")).strip()
                # Parse character_names: accept both list and comma-string
                raw_chars = evt.get("characters", [])
                if isinstance(raw_chars, str):
                    char_names = [c.strip() for c in raw_chars.split(",") if c.strip()]
                elif isinstance(raw_chars, list):
                    char_names = [str(c).strip() for c in raw_chars if str(c).strip()]
                else:
                    char_names = []
                char_ids = _resolve_character_ids(db, ch.novel_id, [], char_names)
                # Resolve location name → ID
                rel_loc_id = None
                if evt_location and evt_location != "未知":
                    loc_obj = db.query(Location).filter(
                        Location.novel_id == ch.novel_id, Location.name == evt_location
                    ).first()
                    if loc_obj:
                        rel_loc_id = loc_obj.id
                # Build content as readable summary
                content_str = evt_desc
                if evt_location and evt_location != "未知":
                    content_str = f"📍 [{evt_location}] {content_str}"
                if char_names:
                    content_str = f"({', '.join(char_names)}) {content_str}"
                te = TimelineEvent(
                    novel_id=ch.novel_id,
                    event_type=TimelineEventType.MAIN_STORY.name,
                    timeline_index=new_idx,
                    title=evt_title,
                    time_label=cal_time,
                    category=evt_category,
                    character_ids=char_ids,
                    content=content_str,
                    absolute_tick=(novel.current_tick if novel else 0) + tick_increment,
                    related_location_id=rel_loc_id,
                )
                pending_timeline_events.append(te)
                timeline_added = True
                # Include event type in report: 正文/历史/世界
                type_label = {"MAIN_STORY": "正文", "HISTORY": "历史", "WORLD": "世界"}.get(te.event_type, te.event_type)
                timeline_events_added.append(f"[{type_label}] [{cal_time}] {evt_title or content_str[:40]}")
    except HTTPException:
        raise
    except Exception as e:
        timeline_error = str(e)

    # 3. Character updates
    updated_names: list[str] = []
    character_error: Optional[str] = None
    try:
        char_template = novel.character_template if novel and novel.character_template else []
        chars = db.scalars(select(Character).where(Character.novel_id == ch.novel_id)).all()
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
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            raise HTTPException(422, f"归档人物JSON解析失败。LLM原始输出:\n{raw[:300]}")
        if isinstance(parsed, list):
            for upd in parsed:
                    is_new = upd.get("is_new", False)
                    cid = upd.get("character_id")
                    u = upd.get("updates", {})
                    target = None
                    name = ""

                    if is_new or not cid:
                        attrs_init: dict = {}
                        if u and isinstance(u, dict):
                            for gk, gv in u.items():
                                if isinstance(gv, dict):
                                    attrs_init[gk] = {fk: str(fv) for fk, fv in gv.items()}
                                else:
                                    attrs_init[gk] = str(gv)
                        new_char = Character(
                            novel_id=ch.novel_id,
                            name=upd.get("name", "未命名角色"),
                            status=upd.get("status_change", "初登场"),
                            is_active=bool(upd.get("is_active", True)),
                            attributes=attrs_init,
                        )
                        db.add(new_char)
                        db.flush()
                        db.refresh(new_char)
                        name = new_char.name
                        updated_names.append(f"✨ [新登场] {name} ({new_char.status})")
                        target = new_char
                    else:
                        target = db.get(Character, cid)
                        if target:
                            old_attrs = copy.deepcopy(target.attributes or {})
                            db.add(CharacterSnapshot(character_id=cid, version_name="自动归档前快照", attributes=old_attrs))
                            # Track changes for report
                            changes: list[str] = []
                            # Merge attributes and detect diffs
                            if u and isinstance(u, dict):
                                merged = copy.deepcopy(target.attributes or {})
                                for group, fields in u.items():
                                    if isinstance(fields, dict):
                                        merged.setdefault(group, {})
                                        if isinstance(merged[group], dict):
                                            for k, v in fields.items():
                                                old_val = merged[group].get(k)
                                                new_val = str(v)
                                                if old_val is None:
                                                    changes.append(f"  + {group}.{k}: {new_val}")
                                                elif str(old_val) != new_val:
                                                    changes.append(f"  ~ {group}.{k}: {str(old_val)} → {new_val}")
                                                merged[group][k] = new_val
                                target.attributes = merged
                                flag_modified(target, "attributes")
                            # Handle status_change and is_active
                            if upd.get("status_change"):
                                old_status = target.status
                                new_status = str(upd["status_change"])
                                target.status = new_status
                                if old_status != new_status:
                                    changes.append(f"  📌 状态: {old_status} → {new_status}")
                            if "is_active" in upd:
                                was_active = target.is_active
                                target.is_active = bool(upd["is_active"])
                                if was_active and not target.is_active:
                                    changes.append(f"  🚪 退场")
                                elif not was_active and target.is_active:
                                    changes.append(f"  🎬 重新登场")
                            name = target.name
                            if target.attributes and isinstance(target.attributes, dict):
                                base = target.attributes.get("基础信息", {})
                                if isinstance(base, dict) and base.get("姓名"):
                                    name = str(base["姓名"])
                            if changes:
                                updated_names.append(f"✏️ {name}:\n" + "\n".join(changes))
                            else:
                                updated_names.append(f"✓ {name} (无变化)")

                    # === Unified post-processing for both new and existing characters ===
                    if target:
                        # Auto-create trajectory from move_to_location
                        if upd.get("move_to_location") and isinstance(upd["move_to_location"], str) and upd["move_to_location"].strip():
                            loc_name = upd["move_to_location"].strip()
                            loc_candidates = db.query(Location).filter(
                                Location.novel_id == ch.novel_id,
                                Location.name.like(f"%{loc_name}%"),
                            ).all()
                            if loc_candidates:
                                best_loc = sorted(
                                    loc_candidates,
                                    key=lambda loc: (0 if loc.name == loc_name else 1, len(loc.name))
                                )[0]
                                db.add(CharacterTrajectory(
                                    character_id=target.id,
                                    location_id=best_loc.id,
                                    start_tick=novel.current_tick if novel else 0,
                                    reason=f"正文归档：移动至 {best_loc.name}",
                                ))
                                updated_names.append(f"📍 {name} 成功移动至 [{best_loc.name}]")
                            else:
                                updated_names.append(f"⚠️ {name} 试图去往 [{loc_name}] (地理库中无匹配地点)")

                        # Handle faction_change
                        fac_change = upd.get("faction_change")
                        if fac_change:
                            fac_name = str(fac_change).strip()
                            if fac_name in ["无", "None", "退出", "叛逃", "脱离"]:
                                target.faction_id = None
                            else:
                                faction_obj = db.query(Faction).filter(
                                    Faction.novel_id == ch.novel_id,
                                    Faction.name == fac_name,
                                ).first()
                                if faction_obj:
                                    target.faction_id = faction_obj.id
                                else:
                                    new_fac = Faction(
                                        novel_id=ch.novel_id,
                                        name=fac_name,
                                        description=f"由归档事件自动创建：{fac_name}",
                                    )
                                    db.add(new_fac)
                                    db.flush()
                                    db.refresh(new_fac)
                                    target.faction_id = new_fac.id

    except HTTPException:
        raise
    except Exception as e:
        character_error = str(e)

    # ── All processing succeeded — now apply all pending writes atomically ──

    # Checkpoint: if timeline events found OR markers advanced time, clear accumulator; otherwise accumulate
    if pending_timeline_events or pending_tick_advance > 0:
        ch.pending_timeline_text = ""  # reset checkpoint — time moved or events extracted
    else:
        ch.pending_timeline_text = full_text  # accumulate for next archive

    # Apply world time advancement
    if pending_tick_advance > 0 and novel:
        novel.current_tick += pending_tick_advance

    # Create timeline events
    for te in pending_timeline_events:
        db.add(te)

    # Move text from content → archived (strip time markers from archived text)
    clean_text = CalendarEngine.strip_time_markers(text)
    if req.selection_start < req.selection_end and ch.content[req.selection_start:req.selection_end] == text:
        remaining = (ch.content[:req.selection_start] + ch.content[req.selection_end:]).strip()
    else:
        remaining = ch.content.replace(text, "", 1).strip()
    # Safety: strip any leftover time markers from remaining body text
    remaining = CalendarEngine.strip_time_markers(remaining)
    if ch.archived_content:
        ch.archived_content += "\n\n" + clean_text
    else:
        ch.archived_content = clean_text
    ch.content = remaining

    db.commit()
    return {
        "message": "归档完成",
        "elapsed_years": elapsed_years,
        "elapsed_months": elapsed_months,
        "elapsed_days": elapsed_days,
        "elapsed_hours": elapsed_hours,
        "timeline_added": timeline_added,
        "timeline_error": timeline_error,
        "timeline_events": timeline_events_added,
        "characters_updated": updated_names,
        "updated_characters": updated_names,
        "character_error": character_error,
        "archived_text": clean_text,
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
        faction_id=body.faction_id, faction_role=body.faction_role,
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
    if body.faction_id is not None:
        c.faction_id = body.faction_id
    if body.faction_role:
        c.faction_role = body.faction_role
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
    if body.scale_level:
        loc.scale_level = body.scale_level
    if body.parent_id is not None:
        if int(body.parent_id) == location_id:
            raise HTTPException(400, "不能将上级设置为自己")
        loc.parent_id = body.parent_id
    if body.grid_x is not None:
        loc.grid_x = body.grid_x
        loc.map_x = body.grid_x
    if body.grid_y is not None:
        loc.grid_y = body.grid_y
        loc.map_y = body.grid_y
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


def _resolve_coord_conflict(db: Session, novel_id: int, parent_id: int | None, grid_x: int, grid_y: int, exclude_id: int | None = None) -> tuple[int, int]:
    """Auto-shift coordinates if another location with the same parent already uses (grid_x, grid_y)."""
    MAX_ATTEMPTS = 625  # 25×25 grid
    for _ in range(MAX_ATTEMPTS):
        q = db.query(Location).filter(
            Location.novel_id == novel_id,
            Location.grid_x == grid_x,
            Location.grid_y == grid_y,
        )
        if parent_id is None:
            q = q.filter(Location.parent_id.is_(None))
        else:
            q = q.filter(Location.parent_id == parent_id)
        if exclude_id is not None:
            q = q.filter(Location.id != exclude_id)
        if q.first() is None:
            break  # No conflict
        # Shift: x+1, wrap at 24; (24,24) wraps to (0,0)
        if grid_x >= 24:
            grid_x = 0
            if grid_y >= 24:
                grid_y = 0
            else:
                grid_y += 1
        else:
            grid_x += 1
    return grid_x, grid_y


def _auto_heal_all_coord_conflicts(db: Session) -> None:
    """Startup healing: scan all locations grouped by (novel_id, parent_id), shift duplicate coords."""
    all_locs = db.query(Location).order_by(Location.id).all()
    # Group by (novel_id, parent_id) — use -1 as sentinel for NULL parent
    groups: dict[tuple[int, int], list[Location]] = {}
    for loc in all_locs:
        if loc.grid_x is None or loc.grid_y is None:
            continue
        key = (loc.novel_id, loc.parent_id if loc.parent_id is not None else -1)
        groups.setdefault(key, []).append(loc)

    fixed = 0
    for key, locs in groups.items():
        novel_id, _ = key
        seen: set[tuple[int, int]] = set()
        for loc in locs:
            x, y = loc.grid_x, loc.grid_y
            original = (x, y)
            # Resolve conflict against already-seen coords in this group
            while (x, y) in seen:
                if x >= 24:
                    x = 0
                    if y >= 24:
                        y = 0
                    else:
                        y += 1
                else:
                    x += 1
            seen.add((x, y))
            if (x, y) != original:
                loc.grid_x, loc.grid_y = x, y
                loc.map_x, loc.map_y = x, y
                fixed += 1
    if fixed:
        db.commit()
        print(f"[Coord Heal] 自动修复 {fixed} 个坐标冲突")


def _sync_attrs_to_templates(db: Session, novel_id: int, scale_level: str, attrs: dict) -> None:
    """Sync new attribute keys into the novel's location_templates under an「AI 创建」group."""
    if not attrs or not scale_level:
        return
    novel = db.get(Novel, novel_id)
    if novel is None:
        return
    templates = dict(novel.location_templates or {})
    groups: list = list(templates.get(scale_level, []))
    if not isinstance(groups, list):
        groups = []

    # Collect all existing field names across all groups
    existing_fields: set[str] = set()
    for g in groups:
        for f in (g.get("fields") or []):
            if isinstance(f, dict) and f.get("name"):
                existing_fields.add(f["name"])

    # Find or create the「AI 创建」group
    ai_group = None
    for g in groups:
        if g.get("group") == "AI 创建":
            ai_group = g
            break
    if ai_group is None:
        ai_group = {"group": "AI 创建", "fields": []}
        groups.append(ai_group)

    # Add new keys
    ai_fields: list = list(ai_group.get("fields") or [])
    added = False
    for key in attrs:
        if key not in existing_fields:
            ai_fields.append({"name": key, "type": "text", "inheritable": False})
            existing_fields.add(key)
            added = True

    if added:
        ai_group["fields"] = ai_fields
        templates[scale_level] = groups
        novel.location_templates = templates
        db.commit()


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
                lines.append(CalendarEngine.strip_time_markers(ch.content))
            if ch.archived_content:
                lines.append(f"\n> *已归档内容:*\n{CalendarEngine.strip_time_markers(ch.archived_content)}")
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
            zf.writestr(file_path, CalendarEngine.strip_time_markers(ch.content or ""))

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
    evt_type = _normalize_timeline_type(body.event_type)
    # Auto-assign timeline_index: max + 1 per novel × event_type
    max_idx = db.scalar(
        select(func.max(TimelineEvent.timeline_index)).where(
            TimelineEvent.novel_id == novel_id,
            TimelineEvent.event_type == evt_type,
        )
    )
    # Resolve location name if provided
    rel_loc_id = body.related_location_id
    if not rel_loc_id and body.related_location_name:
        loc_obj = db.query(Location).filter(
            Location.novel_id == novel_id, Location.name == body.related_location_name
        ).first()
        if loc_obj:
            rel_loc_id = loc_obj.id
    t = TimelineEvent(
        event_type=evt_type,
        title=body.title,
        time_label=body.time_label,
        category=body.category,
        character_ids=_resolve_character_ids(db, novel_id, body.character_ids, body.character_names),
        content=body.content,
        timeline_index=(max_idx or 0) + 1,
        related_location_id=rel_loc_id, novel_id=novel_id,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


@app.put("/api/timeline/{event_id}", response_model=TimelineOut)
def update_timeline(event_id: int, body: TimelineCreate, db: Session = Depends(get_db)):
    t = db.get(TimelineEvent, event_id)
    if not t:
        raise HTTPException(404, "事件不存在")
    t.event_type = _normalize_timeline_type(body.event_type)
    t.title = body.title
    t.time_label = body.time_label
    t.category = body.category
    if body.character_ids or body.character_names:
        t.character_ids = _resolve_character_ids(db, t.novel_id, body.character_ids, body.character_names)
    t.content = body.content
    if body.related_location_id is not None:
        t.related_location_id = body.related_location_id
    elif body.related_location_name is not None:
        loc_obj = db.query(Location).filter(
            Location.novel_id == t.novel_id, Location.name == body.related_location_name
        ).first()
        if loc_obj:
            t.related_location_id = loc_obj.id
    db.commit()
    db.refresh(t)
    return t


@app.delete("/api/timeline/{event_id}")
def delete_timeline(event_id: int, db: Session = Depends(get_db)):
    t = db.get(TimelineEvent, event_id)
    if not t:
        raise HTTPException(404, "事件不存在")
    # Cascade: delete relations referencing this event
    db.query(TimelineEventRelation).filter(
        (TimelineEventRelation.source_event_id == event_id) | (TimelineEventRelation.target_event_id == event_id)
    ).delete()
    db.delete(t)
    db.commit()
    return {"ok": True}


# ═══════════ Timeline Event Relations ═══════════

@app.get("/api/timeline/{event_id}/relations", response_model=list[TimelineRelationOut])
def list_event_relations(event_id: int, db: Session = Depends(get_db)):
    return db.query(TimelineEventRelation).filter(
        (TimelineEventRelation.source_event_id == event_id) | (TimelineEventRelation.target_event_id == event_id)
    ).all()


@app.post("/api/timeline/relations", response_model=TimelineRelationOut)
def create_event_relation(body: TimelineRelationIn, novel_id: int = Query(default=NOVEL_ID_DEFAULT), db: Session = Depends(get_db)):
    r = TimelineEventRelation(
        novel_id=novel_id,
        source_event_id=body.source_event_id,
        target_event_id=body.target_event_id,
        label=body.label,
        description=body.description,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


@app.delete("/api/timeline/relations/{relation_id}")
def delete_event_relation(relation_id: int, db: Session = Depends(get_db)):
    r = db.get(TimelineEventRelation, relation_id)
    if not r:
        raise HTTPException(404, "关系不存在")
    db.delete(r)
    db.commit()
    return {"ok": True}


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
    # 🔧 运行时版本迁移：检测并更新旧版链
    for name in SYSTEM_CHAIN_NAMES:
        chain = db.scalars(
            select(ReasoningChain).where(
                ReasoningChain.novel_id == novel_id,
                ReasoningChain.title == name,
            )
        ).first()
        if chain and isinstance(chain.nodes, list):
            cs = chain.context_settings if isinstance(chain.context_settings, dict) else {}
            ver = cs.get("_version", 0) if isinstance(cs, dict) else 0
            if ver != 2:
                print(f"[Runtime Migration] 链「{name}」版本 {ver}→19")
                chain.nodes = _get_system_chain_nodes(name)
                chain.todos = []
                if isinstance(chain.context_settings, dict):
                    chain.context_settings["_version"] = 2
                else:
                    chain.context_settings = {"_version": 2}
                flag_modified(chain, "nodes")
                flag_modified(chain, "todos")
                flag_modified(chain, "context_settings")
                db.commit()
    # 兜底补种：若当前小说缺少系统链则自动创建
    for name in SYSTEM_CHAIN_NAMES:
        exists = db.scalars(
            select(ReasoningChain).where(
                ReasoningChain.novel_id == novel_id,
                ReasoningChain.title == name,
            )
        ).first()
        if not exists:
            nodes = _get_system_chain_nodes(name)
            db.add(ReasoningChain(
                novel_id=novel_id,
                title=name,
                context_settings={"use_outlines": False, "use_characters": False, "use_timeline": False, "_version": 2},
                nodes=nodes,
            ))
    db.commit()
    # 🛡️ 系统链去重：若存在同名重复（历史遗留），保留最旧(ID最小)的，删除其余
    for name in SYSTEM_CHAIN_NAMES:
        dupes = db.scalars(
            select(ReasoningChain).where(
                ReasoningChain.novel_id == novel_id,
                ReasoningChain.title == name,
            ).order_by(ReasoningChain.id)
        ).all()
        if len(dupes) > 1:
            for dupe in dupes[1:]:
                db.delete(dupe)
            db.commit()
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
    # 系统链标题受保护，不可通过 API 修改
    if rc.title in SYSTEM_CHAIN_NAMES:
        if body.title != rc.title:
            raise HTTPException(400, "系统预设链不可重命名！节点和上下文设置仍可自由修改。")
    else:
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
    system_chains = [
        "🗺️ 蓝图规划与逐层搭建流",
        "👤 严谨人设与模板质检流",
        "📖 全维世界观与大纲流水线",
    ]
    if rc.title in system_chains:
        raise HTTPException(400, "此方案为系统内置创世模板，无法删除！")
    db.delete(rc)
    db.commit()
    return {"ok": True}


@app.post("/api/reasoning_chains/{chain_id}/restore")
def restore_system_chain(chain_id: int, db: Session = Depends(get_db)):
    chain = db.get(ReasoningChain, chain_id)
    if chain is None:
        raise HTTPException(404, "未找到目标方案")
    system_chains = [
        "🗺️ 蓝图规划与逐层搭建流",
        "👤 严谨人设与模板质检流",
        "📖 全维世界观与大纲流水线",
    ]
    if chain.title not in system_chains:
        raise HTTPException(400, "该方案非内置模板，不支持重置恢复！")
    chain.nodes = _get_system_chain_nodes(chain.title)
    chain.todos = []
    flag_modified(chain, "nodes")
    flag_modified(chain, "todos")
    db.commit()
    return {"status": "success", "message": f"方案 '{chain.title}' 已恢复至系统初始状态！"}


@app.post("/api/reasoning_chains/{chain_id}/todos/clear")
def clear_chain_todos(chain_id: int, db: Session = Depends(get_db)):
    """清空推理链任务清单（链执行完成后调用）"""
    chain = db.get(ReasoningChain, chain_id)
    if chain is None:
        raise HTTPException(404, "推演方案不存在")
    chain.todos = []
    flag_modified(chain, "todos")
    db.commit()
    return {"status": "success", "msg": "任务清单已清空"}


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
    api_key: str = ""
    base_url: str = ""
    model: str = "gpt-3.5-turbo"

@app.post("/api/ai/stream")
async def ai_stream(req: AIChatRequest):
    api_key = req.api_key or LLM_API_KEY
    if not api_key:
        raise HTTPException(400, "未配置 API Key，请在设置中配置或在 .env 中设置 LLM_API_KEY")
    async def gen():
        try:
            client = AsyncOpenAI(api_key=api_key, base_url=req.base_url or None)
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
    api_key: str = ""
    base_url: str = ""
    model: str = "gpt-3.5-turbo"
    content: str = ""

@app.post("/api/ai/reasoning")
async def ai_reasoning(req: AIReasoningRequest, novel_id: int = Query(default=NOVEL_ID_DEFAULT), db: Session = Depends(get_db)):
    api_key = req.api_key or LLM_API_KEY
    if not api_key:
        raise HTTPException(400, "未配置 API Key，请在设置中配置或在 .env 中设置 LLM_API_KEY")
    world_lore = get_world_lore(db, novel_id)

    async def gen():
        import json
        client = AsyncOpenAI(api_key=api_key, base_url=req.base_url or None)
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
    write_to: dict | None = None  # {entity, field, sub_field, action}
    chain_id: int | None = None   # 推理链ID（解析 {chain_todos} 时使用）
    response_format: dict | None = None  # {'type': 'json_object'} 用于 DeepSeek 结构化输出
    no_tools: bool = False  # 禁用工具调用（Genesis 建纲模式）
    max_loops: int = 5     # Agent 工具调用最大轮数


async def async_llm_stream(messages: list[dict], api_key: str, base_url: str, model: str, tools: Optional[list[dict]] = None, response_format: Optional[dict] = None, novel_id: int = None, db: Optional[Session] = None, max_loops: int = 5) -> StreamingResponse:

    async def _execute_readonly_tool(tool_name: str, arguments: dict) -> str:
        if tool_name == "query_world_state" and novel_id and db:
            qtype = arguments.get("query_type", "all")
            filt = (arguments.get("filter") or "").strip()
            evt_idx = arguments.get("event_index")
            result = {}
            def _m(s): return not filt or filt.lower() in (s or "").lower()
            if qtype in ("characters","all"):
                chars = db.query(Character).filter(Character.novel_id==novel_id).all()
                lines = []
                for c in chars:
                    if filt and not _m(c.name): continue
                    f2 = db.get(Faction,c.faction_id) if c.faction_id else None
                    fs = f" [势力: {f2.name} / {c.faction_role}]" if f2 and c.faction_role else ""
                    lines.append(f"• [ID:{c.id}] {c.name}({c.status}){fs} — {c.description or ''}")
                result["characters"] = "\n".join(lines) if lines else "(无)"
            if qtype in ("factions","all"):
                factions = db.query(Faction).filter(Faction.novel_id==novel_id).all()
                lines = []
                for f in factions:
                    if filt and not _m(f.name): continue
                    m2 = db.query(Character).filter(Character.faction_id==f.id).all()
                    ms = ", ".join(f"{x.name}({x.faction_role})" for x in m2 if x.faction_role) or "暂无成员"
                    lines.append(f"【[ID:{f.id}] {f.name}】{f.description or ''} | 成员: {ms}")
                result["factions"] = "\n".join(lines) if lines else "(无)"
            if qtype in ("relations","all"):
                rels = db.query(CharacterRelation).filter(CharacterRelation.novel_id==novel_id).all()
                lines = []
                for r in rels:
                    s2 = db.get(Character,r.source_id); t2 = db.get(Character,r.target_id)
                    if not s2 or not t2: continue
                    if filt and not (_m(s2.name) or _m(t2.name) or _m(r.label)): continue
                    w = r.weight
                    g = " [极度亲密]" if w>=80 else " [友好]" if w>=40 else " [不共戴天]" if w<=-80 else " [敌对]" if w<=-40 else ""
                    lines.append(f"[ID:{r.id}] [{s2.name}]→<{r.label}>→[{t2.name}](好感:{w:+d}){g}")
                result["relations"] = "\n".join(lines) if lines else "(无)"
            if qtype in ("locations","all"):
                locs = db.query(Location).filter(Location.novel_id==novel_id).all()
                lines = [f"• [ID:{l.id}] {l.name}[{l.scale_level}] — {l.description or ''}" for l in locs if not filt or _m(l.name)]
                result["locations"] = "\n".join(lines) if lines else "(无)"
            if qtype in ("timeline","all"):
                evts_q = db.query(TimelineEvent).filter(TimelineEvent.novel_id==novel_id)
                if evt_idx is not None:
                    evts_q = evts_q.filter(TimelineEvent.timeline_index == evt_idx)
                evts = evts_q.order_by(TimelineEvent.timeline_index, TimelineEvent.time_label).all()
                if evt_idx is not None:
                    if evts:
                        e = evts[0]
                        loc_name = db.get(Location, e.related_location_id).name if e.related_location_id else "无"
                        char_names = []
                        for cid in (e.character_ids or []):
                            ch = db.get(Character, cid)
                            if ch: char_names.append(ch.name)
                        chars_str = ", ".join(char_names) if char_names else "(无)"
                        # Fetch related events
                        rels = db.query(TimelineEventRelation).filter(
                            (TimelineEventRelation.source_event_id == e.id) | (TimelineEventRelation.target_event_id == e.id)
                        ).all()
                        causes_lines = []
                        caused_by_lines = []
                        for r in rels:
                            if r.source_event_id == e.id:
                                tgt = db.get(TimelineEvent, r.target_event_id)
                                causes_lines.append(f"  → #{tgt.timeline_index} [{tgt.time_label}] {tgt.title or '(无)'} ({r.label})")
                            else:
                                src = db.get(TimelineEvent, r.source_event_id)
                                caused_by_lines.append(f"  ← #{src.timeline_index} [{src.time_label}] {src.title or '(无)'} ({r.label})")
                        result["timeline"] = (
                            f"📌 事件 #{e.timeline_index} [DB-ID:{e.id}]\n"
                            f"标题: {e.title or '(无)'}\n"
                            f"时间: {e.time_label}\n"
                            f"类型: {e.event_type}\n"
                            f"分类: {e.category}\n"
                            f"参与人物: {chars_str}\n"
                            f"关联地点: {loc_name}\n"
                            f"绝对Tick: {e.absolute_tick}\n"
                            f"详情:\n{e.content}"
                        )
                        if caused_by_lines:
                            result["timeline"] += "\n前因:\n" + "\n".join(caused_by_lines)
                        if causes_lines:
                            result["timeline"] += "\n导致:\n" + "\n".join(causes_lines)
                    else:
                        result["timeline"] = f"未找到序号为 {evt_idx} 的时间线事件"
                else:
                    lines = [f"[DB-ID:{e.id}] {e.timeline_index}. [{e.time_label}] {e.title}{' — ' + e.content if e.content else ''}" for e in evts if not filt or (_m(e.time_label) or _m(e.content) or _m(e.title))]
                    result["timeline"] = "\n".join(lines) if lines else "(无)"
            if qtype in ("outline","all"):
                outlines = db.query(Outline).filter(Outline.novel_id==novel_id).order_by(Outline.order_index).all()
                if filt:
                    outlines = [o for o in outlines if _m(o.title) or _m(o.description) or _m(o.category)]
                if outlines:
                    # Build tree grouped by category
                    o_by_cat: dict[str, list[Outline]] = {}
                    for o in outlines:
                        o_by_cat.setdefault(o.category, []).append(o)
                    blocks: list[str] = []
                    for (cat, items) in o_by_cat.items():
                        lines = [f"## {cat}"]
                        # Build parent-child tree
                        p_map: dict[int, list[Outline]] = {}
                        top: list[Outline] = []
                        for o in items:
                            pid = o.parent_id if o.parent_id else 0
                            p_map.setdefault(pid, []).append(o)
                        def _render_outline(pid: int, depth: int) -> list[str]:
                            result_lines: list[str] = []
                            children = p_map.get(pid, [])
                            for o in sorted(children, key=lambda x: x.order_index):
                                prefix = "  " * depth + ("├─" if depth > 0 else "•")
                                result_lines.append(f"{prefix} {o.title}{' [常驻]' if o.is_always_context else ''}")
                                if o.description:
                                    desc_preview = o.description[:80] + "..." if len(o.description) > 80 else o.description
                                    result_lines.append(f"{'  ' * (depth+1)}{desc_preview}")
                                result_lines.extend(_render_outline(o.id, depth+1))
                            return result_lines
                        lines.extend(_render_outline(0, 0))
                        blocks.append("\n".join(lines))
                    result["outline"] = "\n\n".join(blocks)
                else:
                    result["outline"] = "(无)"
            return "查询结果:\n" + "\n\n".join(f"--- {k} ---\n{v}" for k,v in result.items())
        if tool_name == "continue_writing" and novel_id:
            chapter_content = str(arguments.get("chapter_content", ""))
            system_context = str(arguments.get("system_context", ""))
            if not chapter_content.strip():
                return "错误：chapter_content 为空，无法续写"
            # Split at nearest period to midpoint
            text = chapter_content
            if len(text) > 60:
                mid = len(text) // 2
                left = text.rfind("。", 0, mid)
                right = text.find("。", mid)
                if left >= 0 and (right < 0 or (mid - left) <= (right - mid)):
                    split_at = left + 1
                elif right >= 0:
                    split_at = right + 1
                else:
                    split_at = mid
                first_half = text[:split_at].rstrip()
                second_half = text[split_at:].lstrip()
            else:
                first_half = "请续写下文"
                second_half = text
            import random, time
            nonce = f"{random.randint(100000,999999)}-{int(time.time()*1000)%1000000}"
            msgs = [
                {"role": "system", "content": system_context},
                {"role": "user", "content": f"[#{nonce}] 请续写下文\n\n{first_half}"},
                {"role": "assistant", "content": second_half, "prefix": True},
            ]
            api_key = LLM_API_KEY
            beta_url = LLM_BASE_URL.rstrip("/").replace("/v1", "") + "/beta"
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"{beta_url}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={"model": LLM_MODEL, "messages": msgs, "temperature": 0.8, "stream": False},
                )
                if resp.status_code != 200:
                    return f"续写引擎返回错误 [{resp.status_code}]: {resp.text[:500]}"
                data = resp.json()
                content = data["choices"][0]["message"].get("content", "")
                return f"续写结果:\n{content}"

    READONLY_TOOLS = {"query_world_state", "continue_writing"}

    async def gen():
        nonlocal messages
        for _ in range(max_loops):
            tool_calls_acc: dict[int, dict[str, str]] = {}
            try:
                client = AsyncOpenAI(api_key=api_key, base_url=base_url or None)
                extra: dict = {}
                if tools and not response_format:
                    extra["tools"] = tools
                    extra["tool_choice"] = "auto"
                if response_format:
                    extra["response_format"] = response_format
                stream = await client.chat.completions.create(
                    model=model, messages=messages, temperature=0.8, stream=True, **extra,
                )
                has_content = False
                async for chunk in stream:
                    delta = chunk.choices[0].delta
                    if delta.content:
                        has_content = True
                        yield f"data: {json.dumps({'type':'chunk','content':delta.content})}\n\n"
                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx not in tool_calls_acc:
                                tool_calls_acc[idx] = {"id": "", "name": "", "arguments": ""}
                            acc = tool_calls_acc[idx]
                            if tc.id: acc["id"] = tc.id
                            if tc.function:
                                if tc.function.name: acc["name"] += tc.function.name
                                if tc.function.arguments: acc["arguments"] += tc.function.arguments
            except Exception as e:
                yield f"data: {json.dumps({'type':'error','message':str(e)})}\n\n"
                return

            if not tool_calls_acc: break
            tc_list = [v for v in tool_calls_acc.values() if v["name"]]
            if not tc_list: break

            ro_calls = [tc for tc in tc_list if tc["name"] in READONLY_TOOLS]
            rw_calls = [tc for tc in tc_list if tc["name"] not in READONLY_TOOLS]

            if rw_calls:
                for tc_data in tc_list:
                    try: args = json.loads(tc_data["arguments"]) if tc_data["arguments"] else {}
                    except json.JSONDecodeError: args = {}
                    yield f"data: {json.dumps({'type':'tool_proposal','tool_name':tc_data['name'],'arguments':args,'tool_call_id':tc_data['id']})}\n\n"
                yield f"data: {json.dumps({'type':'done'})}\n\n"
                return

            # 只读工具 → 执行并闭环，前端仅展示
            for tc_data in ro_calls:
                try: args = json.loads(tc_data["arguments"]) if tc_data["arguments"] else {}
                except json.JSONDecodeError: args = {}
                yield f"data: {json.dumps({'type':'tool_query','tool_name':tc_data['name'],'arguments':args})}\n\n"

            assistant_msg = {"role": "assistant", "content": None, "tool_calls": []}
            for tc_data in ro_calls:
                try: margs = json.loads(tc_data["arguments"]) if tc_data["arguments"] else {}
                except json.JSONDecodeError: margs = {}
                assistant_msg["tool_calls"].append({
                    "id": tc_data["id"], "type": "function",
                    "function": {"name": tc_data["name"], "arguments": json.dumps(margs, ensure_ascii=False)},
                })
            messages.append(assistant_msg)

            for tc_data in ro_calls:
                try: cargs = json.loads(tc_data["arguments"]) if tc_data["arguments"] else {}
                except json.JSONDecodeError: cargs = {}
                result_str = await _execute_readonly_tool(tc_data["name"], cargs)
                messages.append({"role": "tool", "tool_call_id": tc_data["id"], "content": result_str})

            # 继续循环 → 模型基于工具结果继续生成
        yield f"data: {json.dumps({'type':'done'})}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/reasoning/execute")
async def reasoning_execute(req: ReasoningExecuteRequest, db: Session = Depends(get_db)):
    # 🛡️ 核心大修补：强力穿透占位符，将 raw 字符串转换为饱满的数据库真实数据
    try:
        resolved_premise = resolve_placeholders(req.premise or "", req.novel_id, db)
        resolved_prompt = resolve_placeholders(req.prompt or "", req.novel_id, db)
        resolved_target = resolve_placeholders(req.target or "", req.novel_id, db)
    except Exception as ph_err:
        print(f"[Placeholder Penetration Error] 占位符解析失败，使用原文本兜底: {str(ph_err)}")
        resolved_premise = req.premise or ""
        resolved_prompt = req.prompt or ""
        resolved_target = req.target or ""

    # 🎯 {chain_todos} 专用解析
    ct_str = "(当前推理链无活跃任务清单)"
    if req.chain_id:
        chain = db.get(ReasoningChain, req.chain_id)
        if chain and isinstance(chain.todos, list) and chain.todos:
            lines = []
            for t in chain.todos:
                status_icon = {"pending": "⏳", "in_progress": "🔄", "done": "✅"}.get(t.get("status", ""), "❓")
                line = f"- [{status_icon} {t.get('id','')}] {t.get('content','')}"
                notes = t.get("notes", [])
                if isinstance(notes, list) and notes:
                    for n in notes:
                        if isinstance(n, dict):
                            line += f"\n    📝 {n.get('text','')}"
                lines.append(line)
            ct_str = "【推理链任务清单】:\n" + "\n".join(lines)
    resolved_premise = resolved_premise.replace("{chain_todos}", ct_str)
    resolved_prompt = resolved_prompt.replace("{chain_todos}", ct_str)
    resolved_target = resolved_target.replace("{chain_todos}", ct_str)

    novel = db.get(Novel, req.novel_id)
    ctx = req.context_settings or {}
    final_prompt = resolved_prompt
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

        context_header = (
            "==== 当前世界状态参考 ====\n"
            + "\n\n".join(blocks)
            + "\n\n【⚠️ 增量创建原则 — 必须遵守】:\n"
            + "1. 以上是当前世界已有的全部数据。你必须在此基础之上进行补充和扩展。\n"
            + "2. 严禁创建与已有条目标题重复或内容高度相似的条目。如果某个主题已被覆盖，直接跳过。\n"
            + "3. 只规划/创建那些确实缺失且需要新增的内容。\n"
            + "==========================\n\n"
        )
        final_prompt = context_header + resolved_prompt

    user_parts = []
    if req.previous_output:
        user_parts.append(f"[前置内容/上文]:\n{req.previous_output}")
    if resolved_premise:
        user_parts.append(f"[当前情景]:\n{resolved_premise}")
    if final_prompt:
        user_parts.append(f"[推演指令]:\n{final_prompt}")
    if resolved_target:
        user_parts.append(f"[输出要求]:\n{resolved_target}")

    # 🎯 写入目标硬编码注入
    wt = req.write_to
    if wt and wt.get("entity"):
        entity_labels = {"character": "人物", "location": "地点", "outline": "大纲", "faction": "势力", "timeline": "时间线事件"}
        el = entity_labels.get(wt["entity"], wt["entity"])
        field_desc = wt.get("field", "")
        if wt.get("sub_field"):
            field_desc += f".{wt['sub_field']}"
        action_desc = {"create": "新建", "update": "更新"}.get(wt.get("action", "create"), "写入")
        user_parts.append(
            f"【🎯 硬编码写入目标 — 强制执行，不得忽略】:\n"
            f"你必须将本节点的推理结果写入到: {el} → {field_desc or '默认字段'}\n"
            f"操作类型: {action_desc}\n"
            f"执行方式: 调用 manage_world_element 工具，将你输出的纯文本内容（不含ROUTE标签）精准写入指定位置。\n"
            f"如果本节点不产生数据内容（仅做路由判断），则忽略此写入指令。"
        )

    messages = [
        {"role": "system", "content": resolve_placeholders(
            "你是小说推演引擎，严格遵守以下【输出铁律】：\n"
            "1. 绝对不要输出任何解释、客套话、分析性文字（如'好的'、'以下是'、'根据设定'等）。\n"
            "2. 禁止用 ``` 或 markdown 代码块包裹输出。\n"
            "3. 如果API要求JSON输出，必须只输出合法JSON，禁止使用【】标记或任何其他格式。\n"
            "4. 如果[输出要求]中指定了区块格式，必须严格按照区块编号顺序输出，不得跳过或打乱。\n"
            "5. 如果[输出要求]中要求输出 <ROUTE> 标签，必须在输出末尾精确包含该标签，不得遗漏。\n"
            "6. 如果[输出要求]中有布尔/选项判断，必须从给定选项中选一个，不得自创。\n"
            "7. 当用户指令明确要求调用工具操作某个实体时，必须调用对应工具，参数中的名称、描述等必须与指令完全一致。\n"
            "{outlines_list}\n{characters_list}",
            req.novel_id, db,
        )},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]
    return await async_llm_stream(messages, req.api_key or LLM_API_KEY, req.base_url or LLM_BASE_URL, req.model or LLM_MODEL, tools=(None if req.no_tools else AGENT_TOOLS), response_format=req.response_format, max_loops=req.max_loops)


def resolve_placeholders(text: str, novel_id: int, db: Session) -> str:
    """Replace all {placeholder} patterns with database content."""
    def find_outline_title(outlines: list[Outline], oid: int | None) -> str:
        if oid is None:
            return "?"
        for o in outlines:
            if o.id == oid:
                return o.title
        return "?"
    novel = db.get(Novel, novel_id)
    current_tick = novel.current_tick if novel else 0

    # ═══════════ Lazy-batch cache — eliminates N+1 queries ═══════════
    class _Cache:
        """Lazily loads and indexes all per-novel reference data so inner
        callbacks never issue individual DB queries inside loops."""
        __slots__ = ()
        _chars: dict[int, Character] | None = None
        _locs: dict[int, Location] | None = None
        _factions: dict[int, Faction] | None = None
        _traj: dict[int, CharacterTrajectory | None] | None = None  # best active traj per char_id
        _routines: dict[int, list[CharacterRoutine]] | None = None
        _faction_members: dict[int, list[Character]] | None = None  # faction_id → members

        @classmethod
        def _ensure_chars(cls):
            if cls._chars is None:
                all_c = db.scalars(select(Character).where(Character.novel_id == novel_id)).all()
                cls._chars = {c.id: c for c in all_c}

        @classmethod
        def _ensure_locs(cls):
            if cls._locs is None:
                all_l = db.scalars(select(Location).where(Location.novel_id == novel_id)).all()
                cls._locs = {loc.id: loc for loc in all_l}

        @classmethod
        def _ensure_factions(cls):
            if cls._factions is None:
                all_f = db.scalars(select(Faction).where(Faction.novel_id == novel_id)).all()
                cls._factions = {f.id: f for f in all_f}

        @classmethod
        def _ensure_traj(cls):
            if cls._traj is None:
                cls._traj = {}
                trajs = db.scalars(
                    select(CharacterTrajectory).where(
                        CharacterTrajectory.character_id.in_(
                            select(Character.id).where(Character.novel_id == novel_id)
                        ),
                        CharacterTrajectory.start_tick <= current_tick,
                    ).order_by(CharacterTrajectory.start_tick.desc())
                ).all()
                for t in trajs:
                    if t.character_id not in cls._traj:
                        cls._traj[t.character_id] = t

        @classmethod
        def _ensure_routines(cls):
            if cls._routines is None:
                cls._routines = {}
                all_r = db.scalars(
                    select(CharacterRoutine).where(
                        CharacterRoutine.character_id.in_(
                            select(Character.id).where(Character.novel_id == novel_id)
                        )
                    )
                ).all()
                for r in all_r:
                    cls._routines.setdefault(r.character_id, []).append(r)

        @classmethod
        def _ensure_faction_members(cls):
            if cls._faction_members is None:
                cls._faction_members = {}
                cls._ensure_chars()
                for c in cls._chars.values():
                    if c.faction_id:
                        cls._faction_members.setdefault(c.faction_id, []).append(c)

        @classmethod
        def char_status(cls, char_id: int) -> dict:
            """Cached replacement for get_character_current_status()."""
            cls._ensure_traj()
            cls._ensure_locs()
            cls._ensure_routines()
            cls._ensure_chars()

            traj = cls._traj.get(char_id)
            if traj and (traj.end_tick is None or traj.end_tick > current_tick):
                loc = cls._locs.get(traj.location_id)
                return {"type": "trajectory", "location_id": traj.location_id,
                        "location_name": loc.name if loc else "未知地", "desc": traj.reason}

            fallback_routine = None
            routines = cls._routines.get(char_id, [])
            config = novel.calendar_config if novel and novel.calendar_config else {}
            for r in routines:
                if not r.cycle_value or str(r.cycle_value).strip() == "常驻":
                    fallback_routine = r
                elif CalendarEngine.is_routine_active(current_tick, config, r.cycle_type, r.cycle_value):
                    loc = cls._locs.get(r.location_id)
                    return {"type": "routine", "location_id": r.location_id,
                            "location_name": loc.name if loc else "未知地", "desc": r.activity}
            if fallback_routine:
                loc = cls._locs.get(fallback_routine.location_id)
                loc_name = loc.name if loc else "未知地"
                return {"type": "routine_fallback", "location_id": fallback_routine.location_id,
                        "location_name": loc_name, "desc": f"常驻于{loc_name}"}
            return {"type": "unknown", "location_id": None, "location_name": "行踪不明", "desc": ""}

        @classmethod
        def computed_attrs(cls, location_id: int) -> dict:
            """Cached replacement for get_computed_attributes — no recursive DB hits."""
            cls._ensure_locs()
            result: dict = {}
            cur: int | None = location_id
            visited: set[int] = set()
            while cur is not None and cur not in visited:
                visited.add(cur)
                loc = cls._locs.get(cur)
                if loc and loc.attributes and isinstance(loc.attributes, dict):
                    for k, v in loc.attributes.items():
                        if v and k not in result:
                            result[k] = v
                cur = loc.parent_id if loc else None
            return result

        @classmethod
        def get_faction(cls, faction_id: int) -> Faction | None:
            cls._ensure_factions()
            return cls._factions.get(faction_id)

        @classmethod
        def get_char(cls, char_id: int) -> Character | None:
            cls._ensure_chars()
            return cls._chars.get(char_id)

        @classmethod
        def get_loc(cls, loc_id: int) -> Location | None:
            cls._ensure_locs()
            return cls._locs.get(loc_id)

        @classmethod
        def all_chars(cls) -> list[Character]:
            cls._ensure_chars()
            return list(cls._chars.values())

        @classmethod
        def all_locs(cls) -> list[Location]:
            cls._ensure_locs()
            return list(cls._locs.values())

        @classmethod
        def faction_members_of(cls, faction_id: int) -> list[Character]:
            cls._ensure_faction_members()
            return cls._faction_members.get(faction_id, [])

    # ═══════════ End cache ═══════════

    def _fmt_char(c) -> str:
        name = c.name
        if c.attributes and isinstance(c.attributes, dict):
            base = c.attributes.get("基础信息", {})
            if isinstance(base, dict) and base.get("姓名"):
                name = str(base["姓名"])
        parts: list[str] = [f"- **{name}** (ID:{c.id}) [{c.status}] {'🏃活跃' if c.is_active else '🚪退场'}"]
        status = _Cache.char_status(c.id)
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
        comp = _Cache.computed_attrs(lc.id)
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
        _Cache._ensure_chars()
        always_chars = [c for c in _Cache.all_chars() if c.is_always_context]
        if always_chars:
            lines = ["## 常驻人物"]
            for c in always_chars:
                lines.append(_fmt_char(c))
            parts.append("\n".join(lines))
        locs = _Cache.all_locs()
        if locs:
            lines = ["## 地点列表 (索引 — 用 query_world_state(locations) 获取详情)"]
            for idx, lc in enumerate(locs, 1):
                sc = lc.scale.value if hasattr(lc.scale, "value") else str(lc.scale)
                lines.append(f"{idx}. {lc.name} [{sc}]")
            parts.append("\n".join(lines))
        # Add current calendar time
        lore_time = CalendarEngine.format_tick_to_lore_time(current_tick, novel.calendar_config if novel else {})
        parts.append(f"## 当前世界时间\n{lore_time}")

        # All three timeline types with contextual annotations
        timeline_types: list[tuple[str, str, str]] = [
            ("history", "历史时间线", "（曾经发生过这些事）"),
            ("main_story", "正文时间线", "（现在主角们正在经历这些事）"),
            ("world", "世界时间线", "（与此同时，摄像机之外的世界中，这些事情发生了）"),
        ]
        for et, label, annotation in timeline_types:
            events = db.scalars(select(TimelineEvent).where(
                TimelineEvent.novel_id == novel_id,
                TimelineEvent.event_type == et,
            ).order_by(TimelineEvent.timeline_index, TimelineEvent.time_label)).all()
            if events:
                lines = [f"## {label} (索引 — 用 query_world_state(timeline) 获取详情)\n{annotation}"]
                for e in events:
                    title_part = f" — {e.title}" if e.title else ""
                    lines.append(f"{e.timeline_index}. [{e.time_label}]{title_part}")
                parts.append("\n".join(lines))
        return "\n\n".join(parts) if parts else "(无上下文)"

    def _outline_tree_by_cat(cat_name: str) -> str:
        items = db.scalars(select(Outline).where(
            Outline.novel_id == novel_id, Outline.category == cat_name,
        ).order_by(Outline.order_index)).all()
        by_parent2: dict = {}
        for it in items:
            by_parent2.setdefault(it.parent_id, []).append(it)
        def render_ol_tree(pid, depth):
            parts2: list[str] = []
            for it in by_parent2.get(pid, []):
                indent = "  " * depth
                parts2.append(f"{indent}- **{it.title}**")
                if it.description:
                    for line in it.description.splitlines():
                        parts2.append(f"{indent}  {line}")
                parts2.extend(render_ol_tree(it.id, depth + 1))
            return parts2
        return "\n".join(render_ol_tree(None, 0)) or f"【当前分类 {cat_name} 暂无大纲数据】"

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
            chars = _Cache.all_chars()
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
                status_info = _Cache.char_status(c.id)
                faction_info = ""
                if c.faction_id:
                    faction = _Cache.get_faction(c.faction_id)
                    if faction:
                        faction_info = f" | 🏛️ {faction.name}"
                        if c.faction_role:
                            faction_info += f" - {c.faction_role}"
                lines.append(f"- **{name}** [{c.status}] {active_tag} | 📍 {status_info['location_name']} ({status_info['desc']}){faction_info}")
            return "\n".join(lines)

        if ph == "characters_inactive":
            chars = _Cache.all_chars()
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

        if ph == "characters_full_detail":
            chars = _Cache.all_chars()
            if not chars:
                return "【当前系统内暂无任何人物档案，等待创建中】"
            lines = []
            for c in chars:
                lines.append(f"### 👤 {c.name} [ID: {c.id}] (当前状态: {c.status or '活跃'}, 生命周期: {'存活/活跃' if c.is_active else '退场/冷冻'})")
                if hasattr(c, "faction") and c.faction:
                    lines.append(f"- **所属势力**: {c.faction.name}")
                else:
                    lines.append("- **所属势力**: 无")
                lines.append(f"- **人物基本描述**: {c.description or '无'}")
                if c.attributes:
                    lines.append("- **核心 nested attributes 详细字典**:")
                    lines.append(f"```json\n{json.dumps(c.attributes, ensure_ascii=False, indent=2)}\n```")
                lines.append("---")
            return "\n".join(lines)

        if ph == "locations_list":
            locs = _Cache.all_locs()
            if not locs:
                return "(无地点)"
            lines = ["## 地点列表 (含ID与层级)"]
            for lc in locs:
                sc = lc.scale_level or (lc.scale.value if hasattr(lc.scale, "value") else "")
                parent_info = f" (父ID:{lc.parent_id})" if lc.parent_id else ""
                lines.append(f"- ID:{lc.id} **{lc.name}** [{sc}]{parent_info}")
            return "\n".join(lines)

        if ph == "locations_full_detail":
            locs = _Cache.all_locs()
            by_parent: dict = {}
            for lc in locs:
                by_parent.setdefault(lc.parent_id, []).append(lc)
            def render_loc_tree(pid, depth):
                parts: list[str] = []
                for lc in by_parent.get(pid, []):
                    indent = "  " * depth
                    coord = f"({lc.grid_x},{lc.grid_y})" if (lc.grid_x is not None and lc.grid_y is not None) else ""
                    parts.append(f"{indent}- **{lc.name}** [{lc.scale_level}] {coord}")
                    if lc.description:
                        desc_clean = lc.description.replace('\n', ' ').strip()
                        parts.append(f"{indent}  *{desc_clean[:120]}*")
                    parts.extend(render_loc_tree(lc.id, depth + 1))
                return parts
            return "\n".join(render_loc_tree(None, 0)) or "【当前系统暂无地点数据】"

        if ph == "outlines_full_detail":
            outlines = db.scalars(select(Outline).where(
                Outline.novel_id == novel_id,
            ).order_by(Outline.order_index)).all()
            if not outlines:
                return "【当前系统暂无大纲数据】"
            lines = ["## 全维大纲目录"]
            by_cat: dict[str, list] = {}
            for o in outlines:
                by_cat.setdefault(o.category or "未分类", []).append(o)
            for cat, items in by_cat.items():
                lines.append(f"\n### {cat}")
                for o in items:
                    ctx_tag = " 📌常驻" if o.is_always_context else ""
                    parent_tag = f" (父: {find_outline_title(outlines, o.parent_id)})" if o.parent_id else ""
                    lines.append(f"- [{o.order_index}] **{o.title}**{ctx_tag}{parent_tag}")
                    if o.description:
                        lines.append(f"  {o.description[:150].replace(chr(10), ' ')}")
            return "\n".join(lines)

        if ph == "timeline_history":
            events = db.scalars(select(TimelineEvent).where(
                TimelineEvent.novel_id == novel_id,
                TimelineEvent.event_type == "history",
            ).order_by(TimelineEvent.timeline_index, TimelineEvent.time_label)).all()
            if not events:
                return "(无历史时间线)"
            lines = ["## 历史时间线 (索引列表 — 用 query_world_state(timeline) 获取详情)"]
            for e in events:
                title_part = f" — {e.title}" if e.title else ""
                lines.append(f"{e.timeline_index}. [{e.time_label}]{title_part}")
            return "\n".join(lines)

        if ph == "timeline_world":
            events = db.scalars(select(TimelineEvent).where(
                TimelineEvent.novel_id == novel_id,
                TimelineEvent.event_type == "world",
            ).order_by(TimelineEvent.timeline_index, TimelineEvent.time_label)).all()
            if not events:
                return "(无世界时间线)"
            lines = ["## 世界时间线 (索引列表 — 用 query_world_state(timeline) 获取详情)"]
            for e in events:
                title_part = f" — {e.title}" if e.title else ""
                lines.append(f"{e.timeline_index}. [{e.time_label}]{title_part}")
            return "\n".join(lines)

        if ph == "timeline_main":
            events = db.scalars(select(TimelineEvent).where(
                TimelineEvent.novel_id == novel_id,
                TimelineEvent.event_type == "main_story",
            ).order_by(TimelineEvent.timeline_index, TimelineEvent.time_label)).all()
            if not events:
                return "(无正文时间线)"
            lines = ["## 正文时间线 (索引列表 — 用 query_world_state(timeline) 获取详情)"]
            for e in events:
                title_part = f" — {e.title}" if e.title else ""
                lines.append(f"{e.timeline_index}. [{e.time_label}]{title_part}")
            return "\n".join(lines)

        if ph == "timeline":
            events = db.scalars(select(TimelineEvent).where(
                TimelineEvent.novel_id == novel_id,
                TimelineEvent.event_type == "main_story",
            ).order_by(TimelineEvent.timeline_index, TimelineEvent.time_label)).all()
            if not events:
                return "(无正文时间线)"
            lines = ["## 正文时间线 (索引列表 — 用 query_world_state(timeline) 获取详情)"]
            for e in events:
                title_part = f" — {e.title}" if e.title else ""
                lines.append(f"{e.timeline_index}. [{e.time_label}]{title_part}")
            return "\n".join(lines)

        if ph == "lore":
            return _lore()

        if ph == "current_time":
            lore_time = CalendarEngine.format_tick_to_lore_time(current_tick, novel.calendar_config if novel else {})
            return f"当前世界时间：{lore_time}"

        if ph == "novel_name":
            return novel.title if novel else "(无小说名)"

        if ph == "chapter_title":
            chapter = db.scalars(select(Chapter).where(
                Chapter.novel_id == novel_id
            ).order_by(Chapter.order_index.desc()).limit(1)).first()
            return chapter.title if chapter else "(无章节)"

        if ph == "character_template":
            tmpl = novel.character_template if novel else []
            if not tmpl:
                return "(无人物模板)"
            parts: list[str] = []
            for g in tmpl:
                if isinstance(g, dict):
                    g_name = g.get("group", "未命名")
                    fields = g.get("fields", [])
                    parts.append(f"{g_name}: [{', '.join(fields)}]")
            return " ; ".join(parts) if parts else "(无字段)"


        if ph == "outline_cat_world":
            return _outline_tree_by_cat("世界观")
        if ph == "outline_cat_faction":
            return _outline_tree_by_cat("世界势力")
        if ph == "outline_cat_geo":
            return _outline_tree_by_cat("地理")
        if ph == "outline_cat_power":
            return _outline_tree_by_cat("能力体系设定")
        if ph == "outline_cat_story":
            return _outline_tree_by_cat("剧情大纲")

        if ph == "factions_list":
            _Cache._ensure_factions()
            factions = list(_Cache._factions.values()) if _Cache._factions else []
            if not factions:
                return "(无势力数据)"
            res = ["## 势力名录", ""]
            for f in factions:
                res.append(f"### 🏛️ {f.name}")
                if f.description:
                    res.append(f"- 描述: {f.description}")
                # Resolve base location name + scale
                if f.base_location_id:
                    loc = _Cache.get_loc(f.base_location_id)
                    if loc:
                        sc = loc.scale_level or ""
                        res.append(f"- 驻地: {loc.name} [{sc}]" if sc else f"- 驻地: {loc.name}")
                    else:
                        res.append(f"- 驻地: (ID:{f.base_location_id}, 已删除)")
                else:
                    res.append("- 驻地: 无固定驻地")
                members = _Cache.faction_members_of(f.id)
                if members:
                    mem_strs = [f"{m.name}({m.faction_role})" for m in members if m.faction_role]
                    mem_info = f"- 成员({len(members)}): {', '.join(mem_strs)}" if mem_strs else f"- 成员({len(members)}): (无职位信息)"
                else:
                    mem_info = "- 成员: 暂无成员"
                res.append(mem_info)
                res.append("")  # blank line between factions
            return "\n".join(res).rstrip()

        if ph == "relations_graph":
            relations = db.query(CharacterRelation).filter(CharacterRelation.novel_id == novel_id).all()
            if not relations:
                return "(无关系连线)"
            res = []
            for r in relations:
                src = _Cache.get_char(r.source_id)
                tgt = _Cache.get_char(r.target_id)
                if src and tgt:
                    weight_desc = ""
                    if r.weight >= 80:
                        weight_desc = " [极度亲密]"
                    elif r.weight >= 40:
                        weight_desc = " [友好]"
                    elif r.weight <= -80:
                        weight_desc = " [不共戴天]"
                    elif r.weight <= -40:
                        weight_desc = " [敌对]"
                    reason = f"。原因: {r.description}" if r.description else ""
                    res.append(f"[{src.name}] 对 [{tgt.name}] 有着 <{r.label}> 关系 (好感度: {r.weight}){weight_desc}{reason}")
            return "\n".join(res) if res else "(无关系连线)"

        m = re.match(r"character:(.+)$", ph)
        if m:
            name = m.group(1).strip()
            chars = _Cache.all_chars()
            for c in chars:
                if c.name == name or name in (c.aliases or ""):
                    info = [f"## 人物: {c.name}"]
                    if c.aliases:
                        info.append(f"别名: {c.aliases}")
                    if c.description:
                        info.append(f"描述: {c.description}")
                    info.append(f"状态: {c.status}")
                    info.append(f"活跃: {'是' if c.is_active else '否（已退场）'}")
                    status = _Cache.char_status(c.id)
                    info.append(f"📍 当前时空坐标: 位于【{status['location_name']}】({status['desc']})")
                    _Cache._ensure_routines()
                    routines = _Cache._routines.get(c.id, []) if _Cache._routines else []
                    if routines:
                        info.append("📅 日常作息规律:")
                        for r in routines:
                            r_loc = _Cache.get_loc(r.location_id)
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
            _Cache._ensure_locs()
            locs = [loc for loc in _Cache.all_locs() if loc.name == name]
            if locs:
                lc = locs[0]
                sc = lc.scale.value if hasattr(lc.scale, "value") else str(lc.scale)
                info = [f"## 地点: {lc.name}", f"尺度: {sc}"]
                if lc.description:
                    info.append(f"描述: {lc.description}")
                comp = _Cache.computed_attrs(lc.id)
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


def format_ticks_to_calendar(ticks: int) -> str:
    total_hours = max(0, ticks)
    hour = total_hours % 24
    total_days = total_hours // 24
    day = (total_days % 30) + 1
    total_months = total_days // 30
    month = (total_months % 12) + 1
    year = (total_months // 12) + 1
    return f"{year}年 {month}月 {day}日 {hour}时"


@app.get("/api/calendar/format")
def calendar_format(tick: int = Query(...), novel_id: int = Query(default=NOVEL_ID_DEFAULT), db: Session = Depends(get_db)):
    novel = db.get(Novel, novel_id)
    if novel:
        cfg = novel.calendar_config if novel.calendar_config else {}
        formatted = CalendarEngine.format_tick_to_lore_time(tick, cfg)
    else:
        formatted = format_ticks_to_calendar(tick)
    return {"formatted": formatted, "tick": tick}


@app.get("/api/settings/prompts/defaults")
def get_default_prompts():
    return DEFAULT_PROMPT_TEMPLATES


@app.post("/api/settings/reset_prompts")
def reset_prompt_templates(db: Session = Depends(get_db)):
    gs = db.get(GlobalSettings, 1)
    if gs is None:
        gs = GlobalSettings(id=1, prompt_templates=DEFAULT_PROMPT_TEMPLATES)
        db.add(gs)
    else:
        gs.prompt_templates = copy.deepcopy(DEFAULT_PROMPT_TEMPLATES)
        flag_modified(gs, "prompt_templates")
    db.commit()
    return {"status": "success"}


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
    mode: str = "chat"  # "chat" | "prefix" (DeepSeek Beta prefix completion)
    chain_data: str = ""
    max_loops: int = 5     # Agent 工具调用最大轮数
    history: list = []


@app.post("/api/writing/copilot")
async def writing_copilot(req: CopilotRequest, db: Session = Depends(get_db)):
    gs = db.get(GlobalSettings, 1)
    templates = gs.prompt_templates if gs else DEFAULT_PROMPT_TEMPLATES

    # ── Prefix completion mode (DeepSeek Beta) ──
    if req.mode == "prefix":
        system_tmpl = templates.get("continue_prefix", DEFAULT_PROMPT_TEMPLATES["continue_prefix"])
        system_content = resolve_placeholders(system_tmpl, req.novel_id, db)

        # Split chapter at nearest period (。) to midpoint
        text = req.current_chapter_content
        if len(text) > 60:
            mid = len(text) // 2
            left = text.rfind("。", 0, mid)
            right = text.find("。", mid)
            if left >= 0 and (right < 0 or (mid - left) <= (right - mid)):
                split_at = left + 1  # include 。in first half
            elif right >= 0:
                split_at = right + 1
            else:
                split_at = mid
            first_half = text[:split_at].rstrip()
            second_half = text[split_at:].lstrip()
        else:
            first_half = "请续写下文"
            second_half = text

        import random, time
        nonce = f"{random.randint(100000,999999)}-{int(time.time()*1000)%1000000}"
        msgs: list[dict] = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": f"[#{nonce}] 请续写下文\n\n{first_half}"},
            {"role": "assistant", "content": second_half, "prefix": True},
        ]
        # Use raw httpx to preserve non-standard "prefix" key in message dict
        base_url = req.base_url or LLM_BASE_URL
        beta_url = (base_url.rstrip("/").replace("/v1", "") + "/beta") if "deepseek" in base_url.lower() else base_url
        api_key = req.api_key or LLM_API_KEY
        model = req.model or LLM_MODEL

        async def prefix_gen():
            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream(
                    "POST", f"{beta_url}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={"model": model, "messages": msgs, "temperature": 0.8, "stream": True},
                ) as resp:
                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            data = line[6:]
                            if data == "[DONE]":
                                yield f"data: {json.dumps({'type':'done'})}\n\n"
                                return
                            try:
                                chunk = json.loads(data)
                                delta = chunk["choices"][0]["delta"]
                                if delta.get("content"):
                                    yield f"data: {json.dumps({'type':'chunk','content':delta['content']})}\n\n"
                            except (json.JSONDecodeError, KeyError, IndexError):
                                pass
        return StreamingResponse(prefix_gen(), media_type="text/event-stream")

    # ── Normal chat mode ──
    tmpl = templates.get(req.scene, templates.get("chat", "{instruction}"))

    # Merge system + user prompt so resolve_placeholders runs once (halves DB queries)
    _SEP = "\n\x1eSYSTEM_USER_SEP\x1e\n"
    combined = templates.get("system_agent", DEFAULT_PROMPT_TEMPLATES["system_agent"]) + _SEP + tmpl
    resolved = resolve_placeholders(combined, req.novel_id, db)
    system_content, _, user_content = resolved.partition(_SEP)

    # Replace user-content placeholders AFTER DB resolution (prevents leakage
    # of unresolved {content} etc. from DB-stored text into the final prompt)
    user_content = user_content.replace("{content}", req.current_chapter_content) \
                               .replace("{selection}", req.selected_text) \
                               .replace("{instruction}", req.instruction) \
                               .replace("{chain_data}", req.chain_data)

    messages: list[dict] = [{"role": "system", "content": system_content}]

    if req.history:
        for msg in req.history[-10:]:
            if isinstance(msg, dict) and "role" in msg and "content" in msg:
                messages.append({"role": msg["role"], "content": msg["content"]})

    messages.append({"role": "user", "content": user_content})
    return await async_llm_stream(messages, req.api_key or LLM_API_KEY, req.base_url or LLM_BASE_URL, req.model or LLM_MODEL, tools=AGENT_TOOLS, novel_id=req.novel_id, db=db, max_loops=req.max_loops)


# ═══════════ Agent Tool Execution ═══════════

@app.post("/api/outlines/export-markdown")
def export_outlines_markdown(data: dict, db: Session = Depends(get_db)):
    novel_id = data.get("novel_id")
    if not novel_id:
        raise HTTPException(400, "novel_id is required")
    items = db.scalars(select(Outline).where(Outline.novel_id == novel_id).order_by(Outline.order_index)).all()
    by_parent: dict = {}
    for it in items:
        by_parent.setdefault(it.parent_id, []).append(it)
    lines: list[str] = []
    categories = ["世界观", "世界势力", "地理", "能力体系设定", "人物设定", "剧情大纲"]
    lines.append("# 📖 小说设定大纲全本设定集")
    lines.append("> 生成时间: 基于创世重构引擎实时导出\n")

    def render_branch(node, depth):
        indent = "  " * depth
        lines.append(f"{indent}- **{node.title}**")
        if node.description:
            for desc_line in node.description.splitlines():
                lines.append(f"{indent}  {desc_line}")
        for child in by_parent.get(node.id, []):
            render_branch(child, depth + 1)

    for cat in categories:
        cat_items = [it for it in items if it.category == cat]
        if not cat_items:
            continue
        lines.append(f"## 📁 {cat}")
        lines.append("---")
        cat_ids = {it.id for it in cat_items}
        roots = [it for it in cat_items if it.parent_id not in cat_ids]
        for rt in roots:
            render_branch(rt, 0)
        lines.append("")
    return {"markdown": "\n".join(lines)}

@app.get("/api/factions")
def get_factions(novel_id: int = Query(default=NOVEL_ID_DEFAULT), db: Session = Depends(get_db)):
    return db.query(Faction).filter(Faction.novel_id == novel_id).all()


@app.post("/api/factions")
def create_faction(data: dict, db: Session = Depends(get_db)):
    novel_id = data.get("novel_id")
    if not novel_id:
        raise HTTPException(400, "novel_id is required")
    faction = Faction(
        novel_id=novel_id,
        name=data.get("name", "新势力"),
        description=data.get("description", ""),
        base_location_id=data.get("base_location_id"),
    )
    db.add(faction)
    db.commit()
    db.refresh(faction)
    return {"status": "success", "id": faction.id}


@app.put("/api/factions/{faction_id}")
def update_faction(faction_id: int, data: dict, db: Session = Depends(get_db)):
    faction = db.get(Faction, faction_id)
    if not faction:
        raise HTTPException(404, "Faction not found")
    faction.name = data.get("name", faction.name)
    faction.description = data.get("description", faction.description)
    b_id = data.get("base_location_id")
    faction.base_location_id = int(b_id) if b_id else None
    db.commit()
    return {"status": "success"}


@app.delete("/api/factions/{faction_id}")
def delete_faction(faction_id: int, db: Session = Depends(get_db)):
    faction = db.get(Faction, faction_id)
    if not faction:
        raise HTTPException(404, "Faction not found")
    db.query(Character).filter(Character.faction_id == faction_id).update({"faction_id": None})
    db.delete(faction)
    db.commit()
    return {"status": "success"}


@app.get("/api/character_relations")
def get_relations(novel_id: int = Query(default=NOVEL_ID_DEFAULT), db: Session = Depends(get_db)):
    return db.query(CharacterRelation).filter(CharacterRelation.novel_id == novel_id).all()


class RelationCreate(BaseModel):
    source_id: int
    target_id: int
    label: str = ""
    weight: int = 0
    description: str = ""
    color: str = ""


@app.post("/api/character_relations", response_model=RelationCreate)
def create_relation(body: RelationCreate, novel_id: int = Query(default=NOVEL_ID_DEFAULT), db: Session = Depends(get_db)):
    rel = CharacterRelation(
        novel_id=novel_id, source_id=body.source_id, target_id=body.target_id,
        label=body.label, weight=body.weight, description=body.description, color=body.color,
    )
    db.add(rel)
    db.commit()
    return {"ok": True, "id": rel.id}


@app.put("/api/character_relations/{rel_id}")
def update_relation(rel_id: int, body: RelationCreate, db: Session = Depends(get_db)):
    rel = db.get(CharacterRelation, rel_id)
    if not rel:
        raise HTTPException(404, "关系不存在")
    rel.source_id = body.source_id
    rel.target_id = body.target_id
    rel.label = body.label
    rel.weight = body.weight
    rel.description = body.description
    rel.color = body.color
    db.commit()
    return {"ok": True}


@app.delete("/api/character_relations/{rel_id}")
def delete_relation(rel_id: int, db: Session = Depends(get_db)):
    rel = db.get(CharacterRelation, rel_id)
    if not rel:
        raise HTTPException(404, "关系不存在")
    db.delete(rel)
    db.commit()
    return {"ok": True}

class ToolExecuteReq(BaseModel):
    tool_name: str
    arguments: dict
    write_to: dict | None = None  # {entity, field, sub_field, action}
    chain_id: int | None = None   # 推理链ID（manage_chain_todos 使用）


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

    if req.tool_name == "query_world_state":
        qtype = req.arguments.get("query_type", "all")
        filt = (req.arguments.get("filter") or "").strip()
        evt_idx = req.arguments.get("event_index")
        result = {}

        def _match(s):
            return not filt or filt.lower() in (s or "").lower()

        if qtype in ("characters", "all"):
            chars = db.query(Character).filter(Character.novel_id == novel_id).all()
            lines = []
            for c in chars:
                if filt and not _match(c.name):
                    continue
                faction = db.get(Faction, c.faction_id) if c.faction_id else None
                faction_str = f" [势力: {faction.name} / {c.faction_role}]" if faction and c.faction_role else ""
                lines.append(f"• {c.name} ({c.status}){faction_str} — {c.description or '(无描述)'}")
            result["characters"] = "\n".join(lines) if lines else "(无匹配人物)"

        if qtype in ("factions", "all"):
            factions = db.query(Faction).filter(Faction.novel_id == novel_id).all()
            lines = []
            for f in factions:
                if filt and not _match(f.name):
                    continue
                members = db.query(Character).filter(Character.faction_id == f.id).all()
                mem_str = ", ".join(f"{m.name}({m.faction_role})" for m in members if m.faction_role) or "暂无成员"
                lines.append(f"【{f.name}】{f.description or ''} | 成员: {mem_str}")
            result["factions"] = "\n".join(lines) if lines else "(无匹配势力)"

        if qtype in ("relations", "all"):
            rels = db.query(CharacterRelation).filter(CharacterRelation.novel_id == novel_id).all()
            lines = []
            for r in rels:
                src = db.get(Character, r.source_id)
                tgt = db.get(Character, r.target_id)
                if not src or not tgt:
                    continue
                if filt and not (_match(src.name) or _match(tgt.name) or _match(r.label)):
                    continue
                w = r.weight
                grade = " [极度亲密]" if w >= 80 else " [友好]" if w >= 40 else " [不共戴天]" if w <= -80 else " [敌对]" if w <= -40 else ""
                lines.append(f"[{src.name}] →<{r.label}>→ [{tgt.name}] (好感: {w:+d}){grade}")
            result["relations"] = "\n".join(lines) if lines else "(无匹配关系)"

        if qtype in ("locations", "all"):
            locs = db.query(Location).filter(Location.novel_id == novel_id).all()
            lines = []
            for l in locs:
                if filt and not _match(l.name):
                    continue
                lines.append(f"• {l.name} [{l.scale_level}] — {l.description or '(无描述)'}")
            result["locations"] = "\n".join(lines) if lines else "(无匹配地点)"

        if qtype in ("timeline", "all"):
            evts_q = db.query(TimelineEvent).filter(TimelineEvent.novel_id == novel_id)
            if evt_idx is not None:
                evts_q = evts_q.filter(TimelineEvent.timeline_index == evt_idx)
            events = evts_q.order_by(TimelineEvent.timeline_index, TimelineEvent.time_label).all()
            if evt_idx is not None:
                if events:
                    e = events[0]
                    loc_name = db.get(Location, e.related_location_id).name if e.related_location_id else "无"
                    char_names = []
                    for cid in (e.character_ids or []):
                        ch = db.get(Character, cid)
                        if ch: char_names.append(ch.name)
                    chars_str = ", ".join(char_names) if char_names else "(无)"
                    # Fetch related events
                    rels = db.query(TimelineEventRelation).filter(
                        (TimelineEventRelation.source_event_id == e.id) | (TimelineEventRelation.target_event_id == e.id)
                    ).all()
                    causes_lines = []
                    caused_by_lines = []
                    for r in rels:
                        if r.source_event_id == e.id:
                            tgt = db.get(TimelineEvent, r.target_event_id)
                            causes_lines.append(f"  → #{tgt.timeline_index} [{tgt.time_label}] {tgt.title or '(无)'} ({r.label})")
                        else:
                            src = db.get(TimelineEvent, r.source_event_id)
                            caused_by_lines.append(f"  ← #{src.timeline_index} [{src.time_label}] {src.title or '(无)'} ({r.label})")
                    result["timeline"] = (
                        f"📌 事件 #{e.timeline_index}\n"
                        f"标题: {e.title or '(无)'}\n"
                        f"时间: {e.time_label}\n"
                        f"类型: {e.event_type}\n"
                        f"分类: {e.category}\n"
                        f"参与人物: {chars_str}\n"
                        f"关联地点: {loc_name}\n"
                        f"绝对Tick: {e.absolute_tick}\n"
                        f"详情:\n{e.content}"
                    )
                    if caused_by_lines:
                        result["timeline"] += "\n前因:\n" + "\n".join(caused_by_lines)
                    if causes_lines:
                        result["timeline"] += "\n导致:\n" + "\n".join(causes_lines)
                else:
                    result["timeline"] = f"未找到序号为 {evt_idx} 的时间线事件"
            else:
                lines = []
                for e in events:
                    if filt and not _match(e.time_label) and not _match(e.content) and not _match(e.title):
                        continue
                    lines.append(f"{e.timeline_index}. [{e.time_label}] {e.title}{' — ' + e.content if e.content else ''}")
                result["timeline"] = "\n".join(lines) if lines else "(无时间线事件)"

        return {"status": "success", "query_type": qtype, "data": result}

    if req.tool_name == "manage_world_element":
        etype = req.arguments.get("element_type")
        action = req.arguments.get("action")
        eid = req.arguments.get("element_id")
        data = req.arguments.get("data", {})

        # 🛡️ 写入目标硬校验
        wt = req.write_to
        if wt and wt.get("entity"):
            entity_map = {"character": "character", "location": "location", "outline": "outline",
                          "faction": "faction", "timeline": "timeline", "character_template": "character_template"}
            expected_etype = entity_map.get(wt["entity"])
            if expected_etype and etype != expected_etype:
                raise HTTPException(400,
                    f"⛔ 写入目标不匹配！节点配置为写入 [{wt['entity']}]，但工具调用了 [{etype}]。"
                    f"请检查推理链节点的写入目标设置。")
            expected_action = wt.get("action", "auto")
            if expected_action == "create" and action == "update":
                raise HTTPException(400,
                    "⛔ 写入动作不匹配！节点配置为 [新建]，但工具调用了 [更新]。")
            if expected_action == "update" and action == "add":
                raise HTTPException(400,
                    "⛔ 写入动作不匹配！节点配置为 [更新]，但工具调用了 [新建]。")
            if wt.get("field") and isinstance(data, dict) and action in ("add", "update"):
                field = wt["field"]
                if field != "attributes" and field not in data:
                    raise HTTPException(400,
                        f"⛔ 写入字段不匹配！节点配置为写入 [{field}] 字段，但工具数据中未包含此字段。"
                        f"当前 data 字段: {list(data.keys())}")
        if isinstance(data, str):
            data_str = data.strip()
            if data_str.startswith('"') and data_str.endswith('"') and len(data_str) > 1:
                try:
                    data_str = json.loads(data_str)
                except Exception:
                    data_str = data
            for _ in range(5):
                if not isinstance(data_str, str):
                    break
                try:
                    data_str = json.loads(data_str)
                except json.JSONDecodeError:
                    try:
                        cleaned = data_str.replace('\\\\', '\\').replace('\\"', '"').replace('\\n', '\n')
                        if cleaned.startswith('"') and cleaned.endswith('"'):
                            cleaned = cleaned[1:-1]
                        data_str = json.loads(cleaned)
                    except Exception:
                        break
            data = data_str if isinstance(data_str, dict) else {}

        if etype == "outline":
            # parent_name → parent_id lookup (multi-level path support)
            parent_id = data.get("parent_id")
            p_name = data.get("parent_name")
            if not parent_id and p_name:
                parts = [p.strip() for p in p_name.replace("\uff0f", "/").split("/") if p.strip()]
                last_parent_id = None
                for part in parts:
                    parent_obj = db.query(Outline).filter(
                        Outline.novel_id == novel_id,
                        Outline.title == part,
                        Outline.parent_id == last_parent_id,
                    ).first()
                    if parent_obj:
                        last_parent_id = parent_obj.id
                    else:
                        fallback_obj = db.query(Outline).filter(
                            Outline.novel_id == novel_id,
                            Outline.title == parts[-1],
                        ).first()
                        if fallback_obj:
                            last_parent_id = fallback_obj.id
                        break
                parent_id = last_parent_id

            # Upsert: check existing by title + category (prevents duplicates in incremental mode)
            existing_outline = None
            if action == "add" and data.get("title") and data.get("category"):
                existing_outline = db.query(Outline).filter(
                    Outline.novel_id == novel_id,
                    Outline.title == data["title"],
                    Outline.category == data["category"],
                ).first()
                if existing_outline:
                    action = "update"
                    eid = existing_outline.id

            if action == "add":
                db.add(Outline(
                    novel_id=novel_id,
                    title=data.get("title", ""),
                    description=data.get("description", ""),
                    category=data.get("category", "剧情"),
                    parent_id=parent_id,
                    order_index=data.get("order_index", 0),
                    is_always_context=bool(data.get("is_always_context", False)),
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
                    if parent_id is not None:
                        item.parent_id = parent_id
                    if "order_index" in data:
                        item.order_index = int(data["order_index"])
                    if "is_always_context" in data:
                        item.is_always_context = bool(data["is_always_context"])
            elif action == "delete" and eid:
                db.query(Outline).filter(Outline.id == eid).delete()

        elif etype == "location":
            # Scale fuzzy mapping via LocationScaleLevel enum
            input_scale = data.get("scale", "")
            if input_scale:
                try:
                    input_scale = LocationScaleLevel.fuzzy_match(str(input_scale)).value
                except ValueError:
                    input_scale = LocationScaleLevel.DISTRICT.value
            else:
                input_scale = LocationScaleLevel.DISTRICT.value
            data["scale"] = input_scale

            # Upsert: check existing by name
            existing_loc = None
            if "name" in data:
                existing_loc = db.query(Location).filter(
                    Location.novel_id == novel_id, Location.name == data["name"]
                ).first()
                if existing_loc and action == "add":
                    action = "update"
                    eid = existing_loc.id

            # Auto-create parent from parent_name
            parent_id = data.get("parent_id")
            if not parent_id and data.get("parent_name"):
                parent_loc = db.query(Location).filter(
                    Location.novel_id == novel_id, Location.name == data["parent_name"]
                ).first()
                if not parent_loc:
                    parent_loc = Location(
                        novel_id=novel_id, name=data["parent_name"],
                        scale_level=LocationScaleLevel.GREAT_WORLD.value, description="AI 自动溯源创建",
                    )
                    db.add(parent_loc)
                    db.commit()
                parent_id = parent_loc.id

            if action == "add":
                final_x = data.get("grid_x") if data.get("grid_x") is not None else data.get("map_x")
                final_y = data.get("grid_y") if data.get("grid_y") is not None else data.get("map_y")
                if final_x is not None and final_y is not None:
                    final_x, final_y = _resolve_coord_conflict(db, novel_id, parent_id, int(final_x), int(final_y))
                map_x_val = data.get("map_x") if data.get("map_x") is not None else final_x
                map_y_val = data.get("map_y") if data.get("map_y") is not None else final_y
                db.add(Location(
                    novel_id=novel_id,
                    name=data.get("name", ""),
                    description=data.get("description", ""),
                    parent_id=parent_id,
                    scale_level=data.get("scale_level") or data.get("scale", ""),
                    scale=data.get("scale_enum", "REGION"),
                    grid_x=final_x,
                    grid_y=final_y,
                    map_x=map_x_val,
                    map_y=map_y_val,
                    attributes=data.get("attributes", {}),
                ))
                # Sync new attribute keys to location_templates
                _sync_attrs_to_templates(db, novel_id, data.get("scale_level") or data.get("scale", ""), data.get("attributes", {}))
            elif action == "update" and eid:
                item = db.get(Location, eid)
                if item:
                    if "name" in data:
                        item.name = data["name"]
                    if "description" in data:
                        item.description = data["description"]
                    if "scale_level" in data:
                        item.scale_level = data["scale_level"]
                    elif "scale" in data:
                        item.scale_level = data["scale"]
                    if "scale_enum" in data:
                        item.scale = data["scale_enum"]
                    if parent_id is not None:
                        item.parent_id = parent_id
                    if "grid_x" in data or "map_x" in data:
                        val = data.get("grid_x") if data.get("grid_x") is not None else data.get("map_x")
                        item.grid_x = int(val) if val is not None else None
                        item.map_x = item.grid_x
                    if "grid_y" in data or "map_y" in data:
                        val = data.get("grid_y") if data.get("grid_y") is not None else data.get("map_y")
                        item.grid_y = int(val) if val is not None else None
                        item.map_y = item.grid_y
                    if "map_x" in data:
                        item.map_x = int(data["map_x"])
                    if "map_y" in data:
                        item.map_y = int(data["map_y"])
                    # Auto-resolve coordinate conflicts
                    if item.grid_x is not None and item.grid_y is not None:
                        item.grid_x, item.grid_y = _resolve_coord_conflict(
                            db, novel_id, item.parent_id, item.grid_x, item.grid_y, exclude_id=item.id
                        )
                        item.map_x, item.map_y = item.grid_x, item.grid_y
                    if "attributes" in data and isinstance(data["attributes"], dict):
                        item.attributes = {**item.attributes, **data["attributes"]}
                        _sync_attrs_to_templates(db, novel_id, item.scale_level or "", data["attributes"])
            elif action == "delete" and eid:
                db.query(Location).filter(Location.id == eid).delete()

        elif etype == "timeline":
            if action == "add":
                # Auto-assign timeline_index: max + 1 per novel × event_type
                evt_type = TimelineEventType.MAIN_STORY
                if "event_type" in data:
                    try:
                        evt_type = TimelineEventType(data["event_type"])
                    except ValueError:
                        pass
                max_idx = db.scalar(
                    select(func.max(TimelineEvent.timeline_index)).where(
                        TimelineEvent.novel_id == novel_id,
                        TimelineEvent.event_type == evt_type,
                    )
                )
                # Resolve related_location_name → related_location_id
                rel_loc_id = data.get("related_location_id")
                if not rel_loc_id and data.get("related_location_name"):
                    loc_obj = db.query(Location).filter(
                        Location.novel_id == novel_id, Location.name == data["related_location_name"]
                    ).first()
                    if loc_obj:
                        rel_loc_id = loc_obj.id
                db.add(TimelineEvent(
                    novel_id=novel_id,
                    event_type=evt_type,
                    title=data.get("title", ""),
                    time_label=data.get("time_label", data.get("time_str", "")),
                    category=data.get("category", "其他"),
                    character_ids=_resolve_character_ids(db, novel_id, data.get("character_ids", []), data.get("character_names", [])),
                    content=data.get("content", data.get("event_desc", "")),
                    absolute_tick=novel.current_tick if novel else 0,
                    timeline_index=(max_idx or 0) + 1,
                    related_location_id=rel_loc_id,
                ))
            elif action == "update" and eid:
                item = db.get(TimelineEvent, eid)
                if item:
                    if "title" in data:
                        item.title = data["title"]
                    if "time_label" in data or "time_str" in data:
                        item.time_label = data.get("time_label", data.get("time_str", item.time_label))
                    if "category" in data:
                        item.category = data["category"]
                    if "character_ids" in data or "character_names" in data:
                        item.character_ids = _resolve_character_ids(db, novel_id, data.get("character_ids", []), data.get("character_names", []))
                    if "content" in data or "event_desc" in data:
                        item.content = data.get("content", data.get("event_desc", item.content))
                    if "event_type" in data:
                        try:
                            item.event_type = TimelineEventType(data["event_type"])
                        except ValueError:
                            pass
                    if "related_location_name" in data:
                        loc_obj = db.query(Location).filter(
                            Location.novel_id == novel_id, Location.name == data["related_location_name"]
                        ).first()
                        if loc_obj:
                            item.related_location_id = loc_obj.id
                    if "related_location_id" in data:
                        item.related_location_id = data["related_location_id"]
            elif action == "delete" and eid:
                db.query(TimelineEvent).filter(TimelineEvent.id == eid).delete()

        elif etype == "faction":
            # base_location_name → base_location_id lookup
            base_location_id = data.get("base_location_id")
            if not base_location_id and data.get("base_location_name"):
                loc_obj = db.query(Location).filter(
                    Location.novel_id == novel_id, Location.name == data["base_location_name"]
                ).first()
                if loc_obj:
                    base_location_id = loc_obj.id

            if action == "add":
                db.add(Faction(
                    novel_id=novel_id,
                    name=data.get("name", ""),
                    description=data.get("description", ""),
                    base_location_id=base_location_id,
                ))
            elif action == "update" and eid:
                item = db.get(Faction, eid)
                if item:
                    if "name" in data:
                        item.name = data["name"]
                    if "description" in data:
                        item.description = data["description"]
                    if "base_location_id" in data or "base_location_name" in data:
                        item.base_location_id = base_location_id
            elif action == "delete" and eid:
                db.query(Faction).filter(Faction.id == eid).delete()

        elif etype == "relation":
            # source_name/target_name → ID lookup
            source_id = data.get("source_id")
            target_id = data.get("target_id")
            if not source_id and data.get("source_name"):
                src = db.query(Character).filter(
                    Character.novel_id == novel_id, Character.name == data["source_name"]
                ).first()
                if src:
                    source_id = src.id
            if not target_id and data.get("target_name"):
                tgt = db.query(Character).filter(
                    Character.novel_id == novel_id, Character.name == data["target_name"]
                ).first()
                if tgt:
                    target_id = tgt.id

            if action == "add":
                if not source_id or not target_id:
                    return {"status": "error", "msg": "必须提供有效的 source_id/target_id 或 source_name/target_name"}
                db.add(CharacterRelation(
                    novel_id=novel_id,
                    source_id=source_id,
                    target_id=target_id,
                    label=data.get("label", ""),
                    weight=data.get("weight", 0),
                    description=data.get("description", ""),
                ))
            elif action == "update" and eid:
                rel = db.get(CharacterRelation, eid)
                if rel:
                    if "label" in data:
                        rel.label = data["label"]
                    if "weight" in data:
                        rel.weight = int(data["weight"])
                    if "description" in data:
                        rel.description = data["description"]
            elif action == "delete" and eid:
                db.query(CharacterRelation).filter(CharacterRelation.id == eid).delete()

        elif etype == "character":
            # 🛡️ 终极角色命名容错清洗器
            current_name = data.get("name")
            if not current_name or not str(current_name).strip() or str(current_name).strip() == "未知":
                fallback_fields = [
                    data.get("character_name"),
                    data.get("char_name"),
                    data.get("title"),
                    data.get("character"),
                ]
                for val in fallback_fields:
                    if val and str(val).strip() and str(val).strip() != "未知":
                        data["name"] = str(val).strip()
                        break
                if (not data.get("name") or str(data.get("name")).strip() == "未知") and isinstance(data.get("attributes"), dict):
                    attrs = data["attributes"]
                    base_info = attrs.get("基本信息") or attrs.get("基础信息") or attrs.get("基本情况") or attrs.get("属性")
                    if isinstance(base_info, dict):
                        for name_key in ["姓名", "name", "名字", "角色名"]:
                            if base_info.get(name_key) and str(base_info[name_key]).strip() != "未知":
                                data["name"] = str(base_info[name_key]).strip()
                                break
                if not data.get("name") or str(data.get("name")).strip() == "未知":
                    # 无法解析出有效角色名 → 跳过创建，避免产生 "隐世散人_XXX" 垃圾数据
                    db.commit()
                    return {"status": "skipped", "msg": "角色名缺失，已跳过创建。请确保 AI 输出包含有效的角色名称字段。"}

            raw_attrs = data.get("attributes", {})
            if not isinstance(raw_attrs, dict):
                raw_attrs = {}
            normalized_attrs: dict[str, dict[str, str]] = {}

            if raw_attrs and isinstance(novel.character_template, list):
                tmpl = list(novel.character_template)

                group_to_fields: dict[str, set[str]] = {}
                for g in tmpl:
                    if isinstance(g, dict):
                        group_to_fields[g.get("group", "")] = set(g.get("fields", []))

                field_to_group: dict[str, str] = {}
                for g_name, fields in group_to_fields.items():
                    for f in fields:
                        field_to_group[f] = g_name

                added_new = False

                for k, v in raw_attrs.items():
                    if isinstance(v, dict):
                        g_name = k
                        if g_name not in group_to_fields:
                            group_to_fields[g_name] = set()
                            tmpl.append({"group": g_name, "fields": []})
                            added_new = True
                        if g_name not in normalized_attrs:
                            normalized_attrs[g_name] = {}
                        for f_name, f_val in v.items():
                            if f_name not in group_to_fields[g_name]:
                                for tg in tmpl:
                                    if tg.get("group") == g_name:
                                        tg.setdefault("fields", []).append(f_name)
                                        break
                                group_to_fields[g_name].add(f_name)
                                added_new = True
                            normalized_attrs[g_name][f_name] = str(f_val)
                    else:
                        g_name = field_to_group.get(k, "自定义")
                        if g_name not in group_to_fields:
                            group_to_fields[g_name] = set()
                            tmpl.append({"group": g_name, "fields": []})
                            added_new = True
                        if g_name not in normalized_attrs:
                            normalized_attrs[g_name] = {}
                        if k not in group_to_fields[g_name]:
                            for tg in tmpl:
                                if tg.get("group") == g_name:
                                    tg.setdefault("fields", []).append(k)
                                    break
                            group_to_fields[g_name].add(k)
                            added_new = True
                        normalized_attrs[g_name][k] = str(v)

                if added_new:
                    novel.character_template = tmpl
                    flag_modified(novel, "character_template")
                    db.commit()

            # Upsert: check existing by name
            if "name" in data:
                existing = db.query(Character).filter(
                    Character.novel_id == novel_id, Character.name == data["name"]
                ).first()
                if existing and action == "add":
                    action = "update"
                    eid = existing.id

            # faction_name → faction_id lookup + auto-create
            faction_id = data.get("faction_id")
            if not faction_id and data.get("faction_name") and str(data["faction_name"]).strip() not in ("", "散修", "无", "未知"):
                fac = db.query(Faction).filter(
                    Faction.novel_id == novel_id, Faction.name == data["faction_name"]
                ).first()
                if fac:
                    faction_id = fac.id
                else:
                    fac = Faction(novel_id=novel_id, name=str(data["faction_name"]).strip(), description="")
                    db.add(fac)
                    db.flush()
                    faction_id = fac.id

            # Upsert: check existing by name (增量覆盖：已存在则更新并保留快照)
            existing_char = None
            if data.get("name") and str(data.get("name")).strip() not in ("", "未知"):
                existing_char = db.query(Character).filter(
                    Character.novel_id == novel_id,
                    Character.name == data["name"],
                ).first()
                if existing_char:
                    if action == "add":
                        action = "update"
                    eid = existing_char.id

            if action == "add":
                db.add(Character(
                    novel_id=novel_id,
                    name=data.get("name", ""),
                    aliases=data.get("aliases", ""),
                    description=data.get("description", ""),
                    status=data.get("status", "正常"),
                    is_active=data.get("is_active", True),
                    is_always_context=bool(data.get("is_always_context", False)),
                    faction_id=faction_id,
                    faction_role=data.get("faction_role", ""),
                    attributes=normalized_attrs,
                ))
            elif action == "update" and eid:
                char = db.get(Character, eid)
                if char:
                    # 检测是否有实质性数据变动，避免无变化时仍拍快照
                    has_change = False
                    if "name" in data and data["name"] != char.name:
                        has_change = True
                    if "aliases" in data and data["aliases"] != (char.aliases or ""):
                        has_change = True
                    if "description" in data and data["description"] != (char.description or ""):
                        has_change = True
                    if "status" in data and data["status"] != (char.status or ""):
                        has_change = True
                    if "is_active" in data and data["is_active"] != char.is_active:
                        has_change = True
                    if "faction_id" in data and faction_id != char.faction_id:
                        has_change = True
                    if "faction_name" in data and faction_id != char.faction_id:
                        has_change = True
                    if "faction_role" in data and data["faction_role"] != (char.faction_role or ""):
                        has_change = True
                    if "is_always_context" in data and bool(data["is_always_context"]) != bool(char.is_always_context):
                        has_change = True
                    if raw_attrs:
                        current_attrs: dict = dict(char.attributes) if char.attributes else {}
                        new_attrs: dict = dict(current_attrs)
                        for g_name, fields_dict in normalized_attrs.items():
                            if g_name not in new_attrs:
                                new_attrs[g_name] = {}
                            for f_name, f_val in fields_dict.items():
                                new_attrs[g_name][f_name] = f_val
                        if new_attrs != current_attrs:
                            has_change = True

                    if not has_change:
                        # 无任何变动，跳过更新和快照
                        db.commit()
                        return {"status": "skipped", "msg": f"角色「{char.name}」数据无变化，已跳过更新。"}

                    # 有变动：先拍快照，再更新
                    attrs_copy = copy.deepcopy(char.attributes)
                    attrs_copy["_status"] = char.status or ""
                    attrs_copy["_is_active"] = char.is_active
                    snap = CharacterSnapshot(
                        character_id=char.id,
                        version_name=datetime.utcnow().strftime("%Y%m%d_%H%M%S"),
                        attributes=attrs_copy,
                    )
                    db.add(snap)

                    if "name" in data:
                        char.name = data["name"]
                    if "aliases" in data:
                        char.aliases = data["aliases"]
                    if "description" in data:
                        char.description = data["description"]
                    if "status" in data:
                        char.status = data["status"]
                    if "is_active" in data:
                        char.is_active = data["is_active"]
                    if "faction_id" in data or "faction_name" in data:
                        char.faction_id = faction_id
                    if "faction_role" in data:
                        char.faction_role = data["faction_role"]
                    if "is_always_context" in data:
                        char.is_always_context = bool(data["is_always_context"])
                    if raw_attrs:
                        char.attributes = new_attrs
                        flag_modified(char, "attributes")
                    loc_id = data.get("location_id")
                    if loc_id is not None:
                        loc = db.get(Location, loc_id)
                        if loc:
                            db.add(CharacterTrajectory(
                                character_id=char.id,
                                location_id=loc_id,
                                start_tick=novel.current_tick,
                                reason=data.get("reason", "AI 调度"),
                            ))
            elif action == "delete" and eid:
                db.query(CharacterRelation).filter(
                    (CharacterRelation.source_id == eid) | (CharacterRelation.target_id == eid)
                ).delete()
                db.query(Character).filter(Character.id == eid).delete()

        elif etype == "volume":
            if action == "add":
                db.add(Volume(
                    novel_id=novel_id,
                    title=data.get("title", ""),
                    order_index=int(data.get("order_index", 0)),
                ))
            elif action == "update" and eid:
                v = db.get(Volume, eid)
                if v:
                    if "title" in data:
                        v.title = data["title"]
                    if "order_index" in data:
                        v.order_index = int(data["order_index"])
            elif action == "delete" and eid:
                db.query(Volume).filter(Volume.id == eid).delete()

        elif etype == "character_template":
            if "attributes" in data and isinstance(data["attributes"], list):
                existing: list = list(novel.character_template) if isinstance(novel.character_template, list) else []
                incoming: list = data["attributes"]
                existing_groups = {g.get("group",""): g for g in existing if isinstance(g, dict)}
                for g in incoming:
                    if isinstance(g, dict) and g.get("group"):
                        gname = g["group"]
                        if gname in existing_groups:
                            # 合并字段：去重
                            old_fields = set(existing_groups[gname].get("fields", []))
                            new_fields = [f for f in g.get("fields", []) if f not in old_fields]
                            if new_fields:
                                existing_groups[gname]["fields"] = existing_groups[gname].get("fields", []) + new_fields
                        else:
                            existing.append(g)
                novel.character_template = existing
                flag_modified(novel, "character_template")

        db.commit()
        return {"status": "success", "msg": f"{etype} {action} 成功。"}

    if req.tool_name == "manage_chain_todos":
        if not req.chain_id:
            raise HTTPException(400, "缺少 chain_id，无法操作推理链任务清单")
        chain = db.get(ReasoningChain, req.chain_id)
        if not chain:
            raise HTTPException(404, "推理链不存在")
        action = req.arguments.get("action")
        todos: list = list(chain.todos) if isinstance(chain.todos, list) else []

        if action == "add":
            content = req.arguments.get("content", "").strip()
            if not content:
                raise HTTPException(400, "任务内容不能为空")
            now = datetime.utcnow().isoformat()
            new_todo = {
                "id": "td_" + uuid.uuid4().hex[:8],
                "content": content,
                "status": "pending",
                "created_at": now,
                "updated_at": now,
                "notes": [],
            }
            todos.append(new_todo)
            chain.todos = todos
            flag_modified(chain, "todos")
            db.commit()
            return {"status": "success", "msg": f"任务 [{new_todo['id']}] 已创建", "todo": new_todo}

        todo_id = req.arguments.get("todo_id", "").strip()
        if not todo_id:
            raise HTTPException(400, "缺少 todo_id")
        target = next((t for t in todos if t.get("id") == todo_id), None)
        if not target:
            raise HTTPException(404, f"任务 {todo_id} 不存在")

        if action == "update_status":
            new_status = req.arguments.get("status", "").strip()
            if new_status not in ("pending", "in_progress", "done"):
                raise HTTPException(400, f"无效状态: {new_status}，只能为 pending/in_progress/done")
            target["status"] = new_status
            target["updated_at"] = datetime.utcnow().isoformat()
            chain.todos = todos
            flag_modified(chain, "todos")
            db.commit()
            return {"status": "success", "msg": f"任务 [{todo_id}] 状态更新为 {new_status}"}

        if action == "append_note":
            note = req.arguments.get("note", "").strip()
            if not note:
                raise HTTPException(400, "备注内容不能为空")
            notes: list = target.get("notes", [])
            if not isinstance(notes, list):
                notes = []
            notes.append({"text": note, "at": datetime.utcnow().isoformat()})
            target["notes"] = notes
            target["updated_at"] = datetime.utcnow().isoformat()
            chain.todos = todos
            flag_modified(chain, "todos")
            db.commit()
            return {"status": "success", "msg": f"已为任务 [{todo_id}] 追加备注"}

        raise HTTPException(400, f"不支持的操作: {action}（仅支持 add/update_status/append_note）")

    if req.tool_name == "generate_reasoning_chain":
        action = req.arguments.get("action")
        title = req.arguments.get("title", "未命名推理链")
        steps = req.arguments.get("steps", [])
        now_ts = int(datetime.utcnow().timestamp())
        built_nodes = []
        for i, step in enumerate(steps):
            node_id = str(f"node_{now_ts}_{i}")
            next_node_id = str(f"node_{now_ts}_{i + 1}") if i < len(steps) - 1 else ""
            raw_target = step.get("target")
            parsed_target: list[dict] = []
            if isinstance(raw_target, list):
                for blk in raw_target:
                    blk_type = blk.get("type", "text")
                    blk_value = blk.get("value", "")
                    if blk_type == "placeholder" and blk_value and not blk_value.startswith("{"):
                        blk_value = "{" + blk_value + "}"
                    parsed_target.append({
                        "id": "blk_" + uuid.uuid4().hex[:8],
                        "type": blk_type,
                        "value": blk_value,
                    })
            elif isinstance(raw_target, str):
                parsed_target.append({
                    "id": "blk_" + uuid.uuid4().hex[:8],
                    "type": "text",
                    "value": raw_target,
                })
            else:
                parsed_target.append({
                    "id": "blk_" + uuid.uuid4().hex[:8],
                    "type": "text",
                    "value": "正文",
                })
            built_nodes.append({
                "id": node_id,
                "x": 50,
                "y": 50 + (i * 250),
                "premise": step.get("premise", ""),
                "prompt": step.get("prompt", ""),
                "target": parsed_target,
                "next_node_id": next_node_id,
                "branches": [],
                "output": "",
            })

        if action == "create_new":
            new_chain = ReasoningChain(
                novel_id=novel_id,
                title=title,
                nodes=built_nodes,
                context_settings={"use_outlines": False, "use_characters": False, "use_timeline": False},
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
