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
        DEFAULT_PROMPT_TEMPLATES, TimelineEventType, Faction, CharacterRelation,
    )
    from .world_calendar import CalendarEngine  # noqa: E402
except ImportError:
    from database import Base, engine, get_db, run_migration  # noqa: E402
    from models import (  # noqa: E402
        Chapter, Character, Outline, Location,
        WorldEvent, Novel, Volume, TimelineEvent, CharacterSnapshot, ReasoningChain, GlobalSettings,
        CharacterRoutine, CharacterTrajectory,
        DEFAULT_PROMPT_TEMPLATES, TimelineEventType, Faction, CharacterRelation,
    )
    from world_calendar import CalendarEngine  # noqa: E402

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
                        "description": "⚠️【强制格式】：必须是标准的嵌套 JSON 对象(Object)，绝对不能是转义的字符串！新增/更新数据。\noutline: title/description/category/parent_name(父大纲标题)/order_index。\nlocation: name/description/scale(必须从大千世界,宇宙,星球,大陆,国家,城池,街区中选)/parent_id/parent_name(父地点名)/grid_x/grid_y(0-24整数的相对坐标)。\ncharacter: name/description/status/is_active(布尔)/faction_name(势力名)/faction_role/attributes(必须以嵌套字典结构传入,外层key为分组名如'基础信息',内层为具体属性键值对,可直接自创新分组)。\nvolume: title/order_index。\nfaction: name/description/base_location_name(驻地地点名)。\nrelation: source_name/target_name(人物名)/label/weight(-100~100)。",
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
                                        "{factions_list} - 势力与宗门目录(含成员)\n"
                                        "{relations_graph} - 人物关系网(有向连线)\n"
                                        "{timeline} - 正文时间线历史\n"
                                        "{outlines_list} - 世界大纲列表\n"
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
]

SYSTEM_CHAIN_NAMES = [
    "🗺️ 蓝图规划与逐层搭建流",
    "👤 严谨人设与模板质检流",
    "📖 全维世界观与大纲流水线",
]


def _get_system_chain_nodes(title: str) -> list[dict]:
    if title == "🗺️ 蓝图规划与逐层搭建流":
        stages = [
            ("world", "大千世界与宇宙", "大千世界→宇宙", "大千世界", "宇宙"),
            ("planet", "星球与大陆", "星球→大陆", "星球", "大陆"),
            ("nation", "国家", "国家", "国家", "国家"),
            ("city", "城池与街区", "城池→街区", "城池", "街区"),
        ]
        CX = [30, 460, 890, 1320, 1750]  # 5列: create/validate/write/todos/verify (320px卡+110px距)
        RX = 2180                         # router列
        RY = [-450] + [i * 500 for i in range(4)]  # y: -450, 0, 500, 1000, 1500
        nodes: list[dict] = []
        # Row -450: init
        nodes.append({"id":"m_plan","x":CX[0],"y":RY[0],"premise":"明确需要建立的所有地点（含所有层级），结构化输出地点清单。","prompt":"【全域地点规划】\n请根据用户输入和已有设定，规划所有需要创建的地点。覆盖四个层级：大千世界→星球→大陆→国家→城池→街区。\n注意：可以有下级没有上级，但不能有上级没有下级！每个地点必须指明 parent_name 和 scale_level。\n【全景对齐】：100% 参照已有地理数据：{locations_full_detail}。","format":"location","target":[],"branches":[],"next_node_id":"m_todos","output":""})
        nodes.append({"id":"m_todos","type":"write","x":CX[2],"y":RY[0],"premise":"将所有地点写入推理链待办清单。","prompt":"","target":[],"branches":[],"next_node_id":"m_world_create","output":"","write_to":{"entity":"chain_todos","field":"content","action":"create"}})
        for i, (s_id, s_label, s_scales, s_top, s_bottom) in enumerate(stages):
            y = RY[i+1]
            next_s_id = stages[i + 1][0] if i + 1 < len(stages) else "end"
            next_router = f"m_{next_s_id}_router" if next_s_id != "end" else ""
            nodes.append({"id":f"m_{s_id}_create","x":CX[0],"y":y,"premise":f"从待办中找出【{s_label}】层级第一个未完成的地点，生成完整属性。","prompt":f"【{s_label} 地点创建】\n层级标尺: {s_scales}。最顶层从 {s_top} 开始，最底层到 {s_bottom}。\n推理链待办: {{chain_todos}}\n已有地点: {{locations_full_detail}}\n\n请从待办中找出【{s_label}】层级第一个未完成的地点，为其生成完整的属性字典。如果待办中没有该层级的未完成项，输出 <ROUTE>ALL_DONE</ROUTE>。","format":"location","target":[],"branches":[],"next_node_id":f"m_{s_id}_validate","output":""})
            nodes.append({"id":f"m_{s_id}_validate","type":"validate","x":CX[1],"y":y,"premise":f"校验【{s_label}】生成的地点数据是否合法。","prompt":"","format":"location","target":[],"branches":[{"condition":"VALID","next_node_id":f"m_{s_id}_write"},{"condition":"INVALID","next_node_id":f"m_{s_id}_create"}],"next_node_id":"","output":""})
            nodes.append({"id":f"m_{s_id}_write","type":"write","x":CX[2],"y":y,"premise":f"将生成的【{s_label}】地点写入数据库。","prompt":"","target":[],"branches":[],"next_node_id":f"m_{s_id}_todos","output":"","write_to":{"entity":"location","field":"description","action":"create"}})
            nodes.append({"id":f"m_{s_id}_todos","type":"write","x":CX[3],"y":y,"premise":"将待办中对应项标记为已完成。","prompt":"","target":[],"branches":[],"next_node_id":f"m_{s_id}_verify","output":"","write_to":{"entity":"chain_todos","field":"status","action":"update"}})
            nodes.append({"id":f"m_{s_id}_verify","x":CX[4],"y":y,"premise":f"校验：【{s_label}】层级是否全部完成？","prompt":f"【{s_label} 层级校验】\n推理链待办: {{chain_todos}}\n已有地点: {{locations_full_detail}}\n\n请检查【{s_label}】层级的待办项是否已全部标记为完成？\n- 还有未完成的 → <ROUTE>CONTINUE</ROUTE>\n- 全部完成 → <ROUTE>DONE</ROUTE>","format":"","target":[],"branches":[{"condition":"CONTINUE","next_node_id":f"m_{s_id}_create"},{"condition":"DONE","next_node_id":next_router if next_router else ""}],"next_node_id":"","output":""})
            if next_router:
                nodes.append({"id":next_router,"type":"router","x":RX,"y":y,"premise":f"判定：待办中是否还有【{stages[i+1][1]}】层级的地点？","prompt":"","target":[],"branches":[{"condition":"YES","next_node_id":f"m_{next_s_id}_create"},{"condition":"NO","next_node_id":""}],"next_node_id":"","output":""})
        return nodes
    if title == "👤 严谨人设与模板质检流":
        X1, X2 = 400, 890  # 两列布局
        return [
            {
                "id": "c_plan", "x": X1, "y": 60,
                "premise": "确认需要创建哪些角色，列出完整名单。",
                "prompt": "【角色名单确认】\n根据用户输入，列出需要创建的所有角色姓名。每个角色注明所属势力（无则写'散修'）。格式：\n- [ ] 角色名 (所属势力)\n末尾输出 <ROUTE>DONE</ROUTE>。",
                "target": [], "branches": [], "next_node_id": "c_todos", "output": ""
            },
            {
                "id": "c_todos", "type": "write", "x": X2, "y": 60,
                "premise": "将角色名单写入推理链待办清单。",
                "prompt": "", "target": [], "branches": [], "next_node_id": "c_ask_template", "output": "",
                "write_to": {"entity": "chain_todos", "field": "content", "action": "create"}
            },
            {
                "id": "c_ask_template", "type": "ask", "x": X1, "y": 500,
                "premise": "展示当前人物属性模板，询问用户是否需要新增或修改属性分组/字段（一次性，不循环）。",
                "prompt": "【模板校准提问】\n当前小说的人物属性模板为：\n{character_template}\n\n请生成一个问题，询问用户：\n1. 是否需要新增属性分组（如「功法体系」「血脉天赋」「法宝装备」等）？\n2. 是否有现有分组需要增加或删减字段？\n\n用户回复后，将这些修改要求作为后续创建角色时的额外指引。",
                "target": [], "branches": [], "next_node_id": "c_create", "output": ""
            },
            {
                "id": "c_create", "x": X1, "y": 950,
                "premise": "从待办清单中找出第一个未完成的角色，按模板生成完整属性。",
                "prompt": "【角色属性生成】\n系统模板：{character_template}\n已有角色：{characters_full_detail}\n推理链待办：{chain_todos}\n\n请从待办中找出第一个未完成的角色，为其生成完整饱满的属性字典，严格对齐模板的每个分组和每个字段。势力不存在时自动创建（写入 faction_name）。",
                "format": "character", "target": [], "branches": [], "next_node_id": "c_validate", "output": ""
            },
            {
                "id": "c_validate", "type": "validate", "x": X2, "y": 950,
                "premise": "校验生成的角角色数据——检查模板字段是否全部填满。",
                "prompt": "", "format": "character", "target": [],
                "branches": [
                    {"condition": "VALID", "next_node_id": "c_write"},
                    {"condition": "INVALID", "next_node_id": "c_create"}
                ],
                "next_node_id": "", "output": ""
            },
            {
                "id": "c_write", "type": "write", "x": X1, "y": 1400,
                "premise": "将生成的完整角色属性写入数据库（势力自动关联）。",
                "prompt": "", "target": [], "branches": [], "next_node_id": "c_todos_update", "output": "",
                "write_to": {"entity": "character", "field": "attributes", "action": "create"}
            },
            {
                "id": "c_todos_update", "type": "write", "x": X2, "y": 1400,
                "premise": "将待办中对应角色标记为已完成。",
                "prompt": "", "target": [], "branches": [], "next_node_id": "c_verify", "output": "",
                "write_to": {"entity": "chain_todos", "field": "status", "action": "update"}
            },
            {
                "id": "c_verify", "x": X1, "y": 1850,
                "premise": "校验：所有角色是否已全部创建完成？",
                "prompt": "【角色创建校验】\n推理链待办：{chain_todos}\n已有角色：{characters_full_detail}\n\n请检查待办中的所有角色是否已全部标记为完成？\n- 还有未完成的 → <ROUTE>CONTINUE</ROUTE>\n- 全部完成 → <ROUTE>DONE</ROUTE>",
                "format": "", "target": [],
                "branches": [
                    {"condition": "CONTINUE", "next_node_id": "c_create"},
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
            "能力体系设定": "系统化拆解修行体系、超凡境界、等级晋升要求。",
        }
        CX = [30, 460, 890, 1320, 1750]  # 5列: create/validate/write/todos/verify
        RX = 2180                         # router列
        RY = [-450] + [i * 500 for i in range(5)]  # y: -450, 0, 500, 1000, 1500, 2000
        nodes: list[dict] = []
        # Row -450: init
        nodes.append({"id":"o_plan","x":CX[0],"y":RY[0],"premise":"规划所有模块的大纲条目，结构化输出。","prompt":"【全域大纲规划】\n请根据用户输入和已有设定，对所有模块进行大纲条目规划。模块包括：世界观设定、世界势力、地理知识、人物设定（简略）、能力体系设定。\n【全景对齐】：必须 100% 参照已有地理和角色数据：{locations_full_detail} {characters_list}。","format":"outline","target":[],"branches":[],"next_node_id":"o_todos","output":""})
        nodes.append({"id":"o_todos","type":"write","x":CX[2],"y":RY[0],"premise":"将所有大纲条目写入推理链待办清单。","prompt":"","target":[],"branches":[],"next_node_id":"o_world_create","output":"","write_to":{"entity":"chain_todos","field":"content","action":"create"}})
        for i, (m_id, m_name, cat, use_format) in enumerate(modules):
            y = RY[i+1]
            next_m_id = modules[i + 1][0] if i + 1 < len(modules) else "end"
            next_router = f"o_{next_m_id}_router" if next_m_id != "end" else ""
            fmt = "outline" if use_format else ""
            contract = contracts.get(cat, "")
            nodes.append({"id":f"o_{m_id}_create","x":CX[0],"y":y,"premise":f"从待办中找出【{m_name}】分类第一条未完成条目，生成完整内容。","prompt":f"【{m_name} 条目创建】\n硬性契约：{contract}\n推理链待办：{{chain_todos}}\n已有大纲：{{outlines_full_detail}}\n已有地理：{{locations_full_detail}}\n\n请从待办中找出分类为 [{cat}] 的第一个未完成条目，为其生成完整的 title、category、description（Markdown 格式）。条目可以存在父子嵌套关系——通过 parent_name 字段指定父级（如'修真体系'的子条目'筑基期'写 parent_name: 修真体系）。顶级条目 parent_name 写'无'。","format":fmt,"target":[],"branches":[],"next_node_id":f"o_{m_id}_validate","output":""})
            nodes.append({"id":f"o_{m_id}_validate","type":"validate","x":CX[1],"y":y,"premise":f"校验【{m_name}】生成的大纲数据是否合法。","prompt":"","format":fmt,"target":[],"branches":[{"condition":"VALID","next_node_id":f"o_{m_id}_write"},{"condition":"INVALID","next_node_id":f"o_{m_id}_create"}],"next_node_id":"","output":""})
            nodes.append({"id":f"o_{m_id}_write","type":"write","x":CX[2],"y":y,"premise":f"将生成的【{m_name}】条目写入 {cat} 分类。","prompt":"","target":[],"branches":[],"next_node_id":f"o_{m_id}_todos","output":"","write_to":{"entity":"outline","field":"category","sub_field":cat,"action":"create"}})
            nodes.append({"id":f"o_{m_id}_todos","type":"write","x":CX[3],"y":y,"premise":"将待办中对应项标记为已完成。","prompt":"","target":[],"branches":[],"next_node_id":f"o_{m_id}_verify","output":"","write_to":{"entity":"chain_todos","field":"status","action":"update"}})
            nodes.append({"id":f"o_{m_id}_verify","x":CX[4],"y":y,"premise":f"校验：【{m_name}】是否全部完成？","prompt":f"【{m_name} 校验】\n推理链待办：{{chain_todos}}\n已有大纲：{{outlines_full_detail}}\n\n请检查分类为 [{cat}] 的待办项是否已全部标记为完成？\n- 还有未完成的 → <ROUTE>CONTINUE</ROUTE>\n- 全部完成 → <ROUTE>DONE</ROUTE>","format":"","target":[],"branches":[{"condition":"CONTINUE","next_node_id":f"o_{m_id}_create"},{"condition":"DONE","next_node_id":next_router if next_router else ""}],"next_node_id":"","output":""})
            if next_router:
                nodes.append({"id":next_router,"type":"router","x":RX,"y":y,"premise":f"判定：待办中是否还有【{modules[i+1][1]}】的大纲条目？","prompt":"","target":[],"branches":[{"condition":"YES","next_node_id":f"o_{next_m_id}_create"},{"condition":"NO","next_node_id":""}],"next_node_id":"","output":""})
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
    # Data healing: normalize timeline_events event_type to uppercase
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
            if "archive_timeline" not in tpl or "elapsed_years" not in tpl["archive_timeline"]:
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
                t_chain = db_hf.query(ReasoningChain).filter(ReasoningChain.title == chain_title).first()
                if t_chain and isinstance(t_chain.nodes, list):
                    cs = t_chain.context_settings if isinstance(t_chain.context_settings, dict) else {}
                    ver = cs.get("_version", 0)
                    if ver is None or ver < 6:
                        print(f"[Hotfix Migration] 链「{chain_title}」版本 {ver}→6，执行迁移...")
                        t_chain.nodes = _get_system_chain_nodes(chain_title)
                        cs = dict(t_chain.context_settings) if isinstance(t_chain.context_settings, dict) else {}
                        cs["_version"] = 6
                        t_chain.context_settings = cs
                        flag_modified(t_chain, "nodes")
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
                    context_settings={"use_outlines": False, "use_characters": False, "use_timeline": False},
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
    todos: list = []
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
    elapsed_years = 0
    elapsed_months = 0
    elapsed_days = 0
    elapsed_hours = 0
    timeline_added = False
    timeline_events_added: list[str] = []
    timeline_error: Optional[str] = None
    novel = db.get(Novel, ch.novel_id)
    try:
        tl_resp = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": templates.get("archive_timeline", DEFAULT_PROMPT_TEMPLATES["archive_timeline"]).replace("{text}", text)}],
            temperature=0.5,
        )
        raw_tl = (tl_resp.choices[0].message.content or "").strip()
        # Robust JSON extraction — handle markdown fences and bare JSON
        mj = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw_tl, re.DOTALL)
        if mj:
            raw_clean = mj.group(1).strip()
        else:
            raw_clean = raw_tl
        parsed_tl = json.loads(raw_clean)
        if isinstance(parsed_tl, dict):
            elapsed_years = int(parsed_tl.get("elapsed_years", 0))
            elapsed_months = int(parsed_tl.get("elapsed_months", 0))
            elapsed_days = int(parsed_tl.get("elapsed_days", 0))
            elapsed_hours = int(parsed_tl.get("elapsed_hours", 0))
            # Multi-dimensional tick conversion: 1 year=8640h, 1 month=720h, 1 day=24h
            tick_increment = (elapsed_years * 8640) + (elapsed_months * 720) + (elapsed_days * 24) + elapsed_hours
            if tick_increment > 0 and novel:
                novel.current_tick += tick_increment
            cal_time = format_ticks_to_calendar(novel.current_tick) if novel else ""
            events_list = parsed_tl.get("events", [])
            for evt in events_list:
                max_idx = db.scalar(
                    select(func.max(TimelineEvent.timeline_index)).where(
                        TimelineEvent.novel_id == ch.novel_id,
                        TimelineEvent.event_type == TimelineEventType.MAIN_STORY.name,
                    )
                )
                new_idx = (max_idx or 0) + 1
                evt_desc = f"📍 [{evt.get('location', '未知')}] ({evt.get('characters', '群像')}) {evt.get('event', '')}"
                te = TimelineEvent(
                    novel_id=ch.novel_id,
                    event_type=TimelineEventType.MAIN_STORY.name,
                    timeline_index=new_idx,
                    time_label=cal_time,
                    content=evt_desc,
                    absolute_tick=novel.current_tick if novel else 0,
                )
                db.add(te)
                timeline_added = True
                timeline_events_added.append(f"[{cal_time}] {evt_desc}")
    except Exception as e:
        timeline_error = str(e)

    # 3. Character updates
    updated_names: list[str] = []
    character_error: Optional[str] = None
    try:
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
                        db.commit()
                        db.refresh(new_char)
                        name = new_char.name
                        updated_names.append(f"✨ [新登场] {name} ({new_char.status})")
                        target = new_char
                    else:
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
                                    db.commit()
                                    db.refresh(new_fac)
                                    target.faction_id = new_fac.id

                        if not is_new:
                            updated_names.append(name)
    except Exception as e:
        character_error = str(e)

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
            if ver < 6:
                print(f"[Runtime Migration] 链「{name}」版本 {ver}→6")
                chain.nodes = _get_system_chain_nodes(name)
                if isinstance(chain.context_settings, dict):
                    chain.context_settings["_version"] = 6
                else:
                    chain.context_settings = {"_version": 6}
                flag_modified(chain, "nodes")
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
                context_settings={"use_outlines": False, "use_characters": False, "use_timeline": False},
                nodes=nodes,
            ))
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
    flag_modified(chain, "nodes")
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
    write_to: dict | None = None  # {entity, field, sub_field, action}
    chain_id: int | None = None   # 推理链ID（解析 {chain_todos} 时使用）


async def get_llm_intent(instruction: str, api_key: str, base_url: str, model: str, system_prompt: str) -> str:
    if not instruction.strip():
        return "chat"
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"判断以下用户指令的意图类别：\n{instruction}"},
        ],
        "max_tokens": 10, "temperature": 0.0,
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            res = await client.post(url, headers=headers, json=payload)
            if res.status_code == 200:
                ans = res.json()["choices"][0]["message"]["content"].strip().lower()
                for valid in ["chat", "map_assist", "outline_assist", "character_assist", "import_data"]:
                    if valid in ans:
                        return valid
    except Exception:
        pass
    return "chat"


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

        context_header = "==== 当前世界状态参考 ====\n" + "\n\n".join(blocks) + "\n==========================\n\n"
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
            "3. 如果[输出要求]中指定了区块格式，必须严格按照区块编号顺序输出，不得跳过或打乱。\n"
            "4. 如果[输出要求]中要求输出 <ROUTE> 标签，必须在输出末尾精确包含该标签，不得遗漏。\n"
            "5. 如果[输出要求]中有布尔/选项判断，必须从给定选项中选一个，不得自创。\n"
            "6. 当用户指令明确要求调用工具操作某个实体时，必须调用对应工具，参数中的名称、描述等必须与指令完全一致。\n"
            "{outlines_list}\n{characters_list}",
            req.novel_id, db,
        )},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]
    return await async_llm_stream(messages, req.api_key or LLM_API_KEY, req.base_url or LLM_BASE_URL, req.model or LLM_MODEL, tools=AGENT_TOOLS)


def resolve_placeholders(text: str, novel_id: int, db: Session) -> str:
    def find_outline_title(outlines: list[Outline], oid: int | None) -> str:
        if oid is None:
            return "?"
        for o in outlines:
            if o.id == oid:
                return o.title
        return "?"
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
                faction_info = ""
                if c.faction_id:
                    faction = db.get(Faction, c.faction_id)
                    if faction:
                        faction_info = f" | 🏛️ {faction.name}"
                        if c.faction_role:
                            faction_info += f" - {c.faction_role}"
                lines.append(f"- **{name}** [{c.status}] {active_tag} | 📍 {status_info['location_name']} ({status_info['desc']}){faction_info}")
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

        if ph == "characters_full_detail":
            chars = db.scalars(select(Character).where(Character.novel_id == novel_id)).all()
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
            locs = db.scalars(select(Location).where(Location.novel_id == novel_id)).all()
            if not locs:
                return "(无地点)"
            lines = ["## 地点列表 (含ID与层级)"]
            for lc in locs:
                sc = lc.scale_level or (lc.scale.value if hasattr(lc.scale, "value") else "")
                parent_info = f" (父ID:{lc.parent_id})" if lc.parent_id else ""
                lines.append(f"- ID:{lc.id} **{lc.name}** [{sc}]{parent_info}")
            return "\n".join(lines)

        if ph == "locations_full_detail":
            locs = db.scalars(select(Location).where(Location.novel_id == novel_id)).all()
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

        if ph == "timeline":
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
            factions = db.query(Faction).filter(Faction.novel_id == novel_id).all()
            if not factions:
                return "(无势力数据)"
            res = []
            for f in factions:
                members = db.query(Character).filter(Character.faction_id == f.id).all()
                mem_strs = [f"{m.name}({m.faction_role})" for m in members if m.faction_role]
                mem_info = f"成员: {', '.join(mem_strs)}" if mem_strs else "暂无成员"
                base_info = f"驻地ID:{f.base_location_id}" if f.base_location_id else "无固定驻地"
                res.append(f"【{f.name}】 {f.description} | {base_info} | {mem_info}")
            return "\n".join(res)

        if ph == "relations_graph":
            relations = db.query(CharacterRelation).filter(CharacterRelation.novel_id == novel_id).all()
            if not relations:
                return "(无关系连线)"
            res = []
            for r in relations:
                src = db.get(Character, r.source_id)
                tgt = db.get(Character, r.target_id)
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


def format_ticks_to_calendar(ticks: int) -> str:
    total_hours = max(0, ticks)
    hour = total_hours % 24
    total_days = total_hours // 24
    day = (total_days % 30) + 1
    total_months = total_days // 30
    month = (total_months % 12) + 1
    year = (total_months // 12) + 1
    return f"{year}年 {month}月 {day}日 {hour}时"


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
    chain_data: str = ""
    history: list = []


@app.post("/api/writing/copilot")
async def writing_copilot(req: CopilotRequest, db: Session = Depends(get_db)):
    gs = db.get(GlobalSettings, 1)
    templates = gs.prompt_templates if gs else DEFAULT_PROMPT_TEMPLATES

    tmpl = templates.get(req.scene, templates.get("chat", "{instruction}"))

    prompt = tmpl.replace("{content}", req.current_chapter_content).replace("{selection}", req.selected_text).replace("{instruction}", req.instruction).replace("{chain_data}", req.chain_data)
    prompt = resolve_placeholders(prompt, req.novel_id, db)

    messages = [
        {"role": "system", "content": resolve_placeholders(
            templates.get("system_agent", DEFAULT_PROMPT_TEMPLATES["system_agent"]),
            req.novel_id, db)},
    ]

    if req.history:
        for msg in req.history[-10:]:
            if isinstance(msg, dict) and "role" in msg and "content" in msg:
                messages.append({"role": msg["role"], "content": msg["content"]})

    messages.append({"role": "user", "content": prompt})
    return await async_llm_stream(messages, req.api_key or LLM_API_KEY, req.base_url or LLM_BASE_URL, req.model or LLM_MODEL, tools=AGENT_TOOLS)


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

    if req.tool_name == "manage_world_element":
        etype = req.arguments.get("element_type")
        action = req.arguments.get("action")
        eid = req.arguments.get("element_id")
        data = req.arguments.get("data", {})

        # 🛡️ 写入目标硬校验
        wt = req.write_to
        if wt and wt.get("entity"):
            entity_map = {"character": "character", "location": "location", "outline": "outline",
                          "faction": "faction", "timeline": "timeline"}
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
            # Scale validation and downgrade
            valid_scales = ["大千世界", "宇宙", "星球", "大陆", "国家", "城池", "街区"]
            input_scale = data.get("scale", "街区")
            if input_scale not in valid_scales:
                s_lower = str(input_scale).lower()
                if "大千世界" in s_lower:
                    input_scale = "大千世界"
                elif "世界" in s_lower or "宇宙" in s_lower or "universe" in s_lower:
                    input_scale = "宇宙"
                elif "星球" in s_lower or "planet" in s_lower:
                    input_scale = "星球"
                elif "大陆" in s_lower or "continent" in s_lower:
                    input_scale = "大陆"
                elif "国" in s_lower or "empire" in s_lower:
                    input_scale = "国家"
                elif "城" in s_lower or "镇" in s_lower or "city" in s_lower or "town" in s_lower:
                    input_scale = "城池"
                elif "村" in s_lower or "village" in s_lower or "街" in s_lower or "建筑" in s_lower or "building" in s_lower:
                    input_scale = "街区"
                else:
                    input_scale = "街区"
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
                        scale_level="大陆", description="AI 自动溯源创建",
                    )
                    db.add(parent_loc)
                    db.commit()
                parent_id = parent_loc.id

            if action == "add":
                final_x = data.get("grid_x") if data.get("grid_x") is not None else data.get("map_x")
                final_y = data.get("grid_y") if data.get("grid_y") is not None else data.get("map_y")
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
                    if "attributes" in data and isinstance(data["attributes"], dict):
                        item.attributes = {**item.attributes, **data["attributes"]}
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
                    import random
                    data["name"] = f"隐世散人_{random.randint(100, 999)}"

            raw_attrs = data.get("attributes", {})
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
                        current_attrs: dict = dict(char.attributes) if char.attributes else {}
                        for g_name, fields_dict in normalized_attrs.items():
                            if g_name not in current_attrs:
                                current_attrs[g_name] = {}
                            for f_name, f_val in fields_dict.items():
                                current_attrs[g_name][f_name] = f_val
                        char.attributes = current_attrs
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
            if action == "update" and "attributes" in data:
                novel.character_template = data["attributes"]
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
