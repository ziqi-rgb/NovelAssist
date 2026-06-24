# 三条系统推理链可视化文档

## 节点类型图例

| 图标 | 类型 | 说明 |
|------|------|------|
| 🧠 | reasoning | AI推理，输出内容+ROUTE |
| 📝 | write | 程序写入，无需AI |
| 🔀 | router | 路由判定，匹配prevRoute |
| ❓ | ask | 用户交互提问 |

---

## 🗺️ 一、蓝图规划与逐层搭建流 (18节点)

```
                               ┌─────────────────────────────┐
                               │ 🧠 m_init_plan              │
                               │ 分析输入→任务清单           │
                               │ → DONE                      │
                               └─────────────┬───────────────┘
                                             │
                               ┌─────────────▼───────────────┐
                               │ 📝 m_init_todos             │
                               │ 写入 chain_todos            │
                               └─────────────┬───────────────┘
                                             │
        ╔══════════════════════════════════════════════════════════╗
        ║               层级循环 × 4                               ║
        ║   world(大千世界与宇宙)→planet(星球与大陆)               ║
        ║   →nation(国家)→city(城池与街区)                        ║
        ╚══════════════════════════════════════════════════════════╝
                                             │
                    ┌────────────────────────┼────────────────────────┐
                    │                        │                        │
              ┌─────▼─────┐          ┌───────▼──────┐          ┌──────▼─────┐
              │ 🔀 check   │ ──NO──▶ │  🔀 check     │ ──NO──▶ │  ...       │
              │ 需要此层级？│          │  需要下层级？  │          │            │
              └─────┬─────┘          └───────┬──────┘          └────────────┘
                    │ YES                    │ YES
              ┌─────▼─────┐          ┌───────▼──────┐
              │ 🧠 plan   │          │  🧠 plan      │
              │ 规划地点   │          │  规划地点      │
              │ format:    │          │  format:      │
              │  location  │          │   location    │
              │ → DONE     │          │  → DONE       │
              └─────┬─────┘          └───────┬──────┘
                    │                        │
              ┌─────▼─────┐          ┌───────▼──────┐
              │ 📝 build  │          │  📝 build     │
              │ 写入location│         │  写入location │
              │ entity:    │          │  entity:      │
              │  location  │          │   location    │
              └─────┬─────┘          └───────┬──────┘
                    │                        │
              ┌─────▼─────┐          ┌───────▼──────┐
              │ 🧠 verify │          │  🧠 verify    │
              │ 查DB→      │          │  查DB→        │
              │ CONTINUE/  │          │  CONTINUE/    │
              │ DONE       │          │  DONE         │
              └──┬───┬────┘          └──┬───┬───────┘
                 │   │                  │   │
          CONTINUE│   │DONE      CONTINUE│   │DONE
                 │   └────▶下层级check    │   └───▶下层级check
                 └──▶build               └───▶build
```

### 节点清单

| # | ID | 类型 | 前提 | 格式/write_to | 路由 |
|---|-----|------|------|--------------|------|
| 1 | m_init_plan | 🧠 | 分析输入→任务清单 | — | →m_init_todos |
| 2 | m_init_todos | 📝 | 写入Todo | entity:chain_todos | →m_world_check |
| **world 层级** | | | | | |
| 3 | m_world_check | 🔀 | 需要大千世界/宇宙？ | — | YES→plan / NO→planet_check |
| 4 | m_world_plan | 🧠 | 规划大千世界/宇宙地点 | format:**location** | →m_world_build |
| 5 | m_world_build | 📝 | 写入地点 | entity:**location** | →m_world_verify |
| 6 | m_world_verify | 🧠 | 查DB校验 | — | **CONTINUE→build** / DONE→planet_check |
| **planet 层级** | | | | | |
| 7 | m_planet_check | 🔀 | 需要星球/大陆？ | — | YES→plan / NO→nation_check |
| 8 | m_planet_plan | 🧠 | 规划星球/大陆地点 | format:**location** | →m_planet_build |
| 9 | m_planet_build | 📝 | 写入地点 | entity:**location** | →m_planet_verify |
| 10 | m_planet_verify | 🧠 | 查DB校验 | — | **CONTINUE→build** / DONE→nation_check |
| **nation 层级** | | | | | |
| 11 | m_nation_check | 🔀 | 需要国家？ | — | YES→plan / NO→city_check |
| 12 | m_nation_plan | 🧠 | 规划国家地点 | format:**location** | →m_nation_build |
| 13 | m_nation_build | 📝 | 写入地点 | entity:**location** | →m_nation_verify |
| 14 | m_nation_verify | 🧠 | 查DB校验 | — | **CONTINUE→build** / DONE→city_check |
| **city 层级** | | | | | |
| 15 | m_city_check | 🔀 | 需要城池/街区？ | — | YES→plan / NO→结束 |
| 16 | m_city_plan | 🧠 | 规划城池/街区地点 | format:**location** | →m_city_build |
| 17 | m_city_build | 📝 | 写入地点 | entity:**location** | →m_city_verify |
| 18 | m_city_verify | 🧠 | 查DB校验 | — | **CONTINUE→build** / DONE→结束 |

### 当前问题

- verify 节点的 CONTINUE 回路指向 build 节点，但 build 写完后没有新的 plan 输出，会重复写入同一数据
- 回路应该是 CONTINUE→plan→build，或者 plan 节点一次性输出全部地点、build 逐条写入

---

## 👤 二、严谨人设与模板质检流 (12节点)

```
                               ┌─────────────────────────────┐
                               │ ❓ c_nm1_ask_template       │
                               │ 展示模板→询问用户修改       │
                               └─────────────┬───────────────┘
                                             │
                               ┌─────────────▼───────────────┐
                               │ 🧠 c_n0_init_plan           │
                               │ 分析输入→人物任务清单       │
                               └─────────────┬───────────────┘
                                             │
                               ┌─────────────▼───────────────┐
                               │ 📝 c_n0_init_todos          │
                               │ 写入 chain_todos            │
                               └─────────────┬───────────────┘
                                             │
                               ┌─────────────▼───────────────┐
                               │ 🧠 c_n1_prepare             │
                               │ 初步整理人物设定             │
                               └─────────────┬───────────────┘
                                             │
                               ┌─────────────▼───────────────┐
                               │ 🔀 c_n2_check_faction       │
                               │ 需要新建势力？               │
                               └──┬────────────┬────────────┘
                          YES     │            │  NO
                    ┌─────────────▼──┐   ┌─────▼─────────────┐
                    │ 📝 c_n3_build  │   │ 🧠 c_n4_number    │
                    │ _faction       │   │ 确定人数+编号     │
                    │ 写入势力→返回   │   └─────────┬─────────┘
                    │ check           │             │
                    └────────────────┘   ┌─────────▼─────────┐
                                         │ 🧠 c_n5a_gen_char │
        ╔═══════════════════════════════ │ 生成角色属性      │
        ║         角色循环               │ format:character  │
        ╚═══════════════════════════════ └─────────┬─────────┘
                                                  │
                                        ┌─────────▼─────────┐
                                        │ 📝 c_n5_build_char │
                                        │ 写入 character     │
                                        │ entity:character   │
                                        └─────────┬─────────┘
                                                  │
                                        ┌─────────▼─────────┐
                                        │ 🧠 c_n6_verify    │
                                        │ _template          │
                                        │ 模板对齐质检       │
                                        └──┬──────────┬─────┘
                                   PERFECT │          │ NEEDS_FIX
                                ┌──────────▼──┐  ┌────▼──────────┐
                                │             │  │ 📝 c_n7_fix   │
                                │             │  │ 补齐字段→返回  │
                                │             │  │ verify         │
                                └──────┬──────┘  └───────────────┘
                                       │
                              ┌────────▼──────────┐
                              │ 🧠 c_n8_verify    │
                              │ _quantity          │
                              │ 数量大盘点          │
                              └──┬──────────┬─────┘
                         NEEDS_MORE│          │DONE
                    ┌──────────────▼──┐    ┌──▼──────┐
                    │ 返回 gen_char   │    │  结束    │
                    └─────────────────┘    └─────────┘
```

### 节点清单

| # | ID | 类型 | 前提 | 格式/write_to | 路由 |
|---|-----|------|------|--------------|------|
| 1 | c_nm1_ask_template | ❓ | 展示模板，询问修改 | — | →c_n0_init_plan |
| 2 | c_n0_init_plan | 🧠 | 分析输入→人物任务清单 | — | →c_n0_init_todos |
| 3 | c_n0_init_todos | 📝 | 写入Todo | entity:chain_todos | →c_n1_prepare |
| 4 | c_n1_prepare | 🧠 | 初步整理人物设定 | — | →c_n2_check_faction |
| 5 | c_n2_check_faction | 🔀 | 需要新建势力? | — | YES→c_n3 / NO→c_n4 |
| 6 | c_n3_build_faction | 📝 | 写入势力 | entity:**faction** | →c_n2_check_faction |
| 7 | c_n4_number | 🧠 | 确定人数+编号 | — | →c_n5a_gen_char |
| 8 | c_n5a_gen_char | 🧠 | 生成角色属性 | format:**character** | →c_n5_build_char |
| 9 | c_n5_build_char | 📝 | 写入角色 | entity:**character** | →c_n6_verify_template |
| 10 | c_n6_verify_template | 🧠 | 模板对齐质检 | — | PERFECT→c_n8 / NEEDS_FIX→c_n7 |
| 11 | c_n7_fix | 📝 | 补齐缺失字段 | entity:**character**(update) | →c_n6_verify_template |
| 12 | c_n8_verify_quantity | 🧠 | 数量大盘点 | — | NEEDS_MORE→c_n5a / DONE→结束 |

### 当前问题

1. **c_n3 写入势力**：上游 c_n1_prepare 只有自然语言描述，没有结构化 faction 输出，write 节点无法解析出 name/description
2. **c_n6 verify → c_n7 fix**：fix 节点是 write 类型，需要结构化 fix 指令，但 verify 输出只是 ROUTE 标记
3. **NEEDS_MORE → c_n5a**：gen_char 重新生成全部角色列表，效率低
4. **c_n4_number** 将计划和生成合并：应该是 `ask_node确认名单→gen_char` 两步

---

## 📖 三、全维世界观与大纲流水线 (26节点)

```
                               ┌─────────────────────────────┐
                               │ 🧠 o_init_plan              │
                               │ 分析输入→六大模块任务清单   │
                               └─────────────┬───────────────┘
                                             │
                               ┌─────────────▼───────────────┐
                               │ 📝 o_init_todos             │
                               │ 写入 chain_todos            │
                               └─────────────┬───────────────┘
                                             │
        ╔══════════════════════════════════════════════════════════╗
        ║             模块循环 × 6                                 ║
        ║  world→faction→geo→char→power→story                    ║
        ╚══════════════════════════════════════════════════════════╝
                                             │
                    ┌────────────────────────┼────────────────────────┐
                    │                        │                        │
              ┌─────▼─────┐          ┌───────▼──────┐          ┌──────▼─────┐
              │ 🔀 check   │ ──NO──▶ │  🔀 check     │ ──NO──▶ │  ...       │
              │ 需要此模块？│          │  需要下模块？  │          │            │
              └─────┬─────┘          └───────┬──────┘          └────────────┘
                    │ YES                    │ YES
              ┌─────▼─────┐          ┌───────▼──────┐
              │ 🧠 plan   │          │  🧠 plan      │
              │ 规划条目   │          │  规划条目      │
              │ format:    │          │  format:      │
              │  outline   │          │   outline     │
              │ → DONE     │          │  → DONE       │
              └─────┬─────┘          └───────┬──────┘
                    │                        │
              ┌─────▼─────┐          ┌───────▼──────┐
              │ 📝 build  │          │  📝 build     │
              │ 写入outline│          │  写入outline  │
              │ entity:    │          │  entity:      │
              │  outline   │          │   outline     │
              └─────┬─────┘          └───────┬──────┘
                    │                        │
              ┌─────▼─────┐          ┌───────▼──────┐
              │ 🧠 verify │          │  🧠 verify    │
              │ 查DB→      │          │  查DB→        │
              │ CONTINUE/  │          │  CONTINUE/    │
              │ DONE       │          │  DONE         │
              └──┬───┬────┘          └──┬───┬───────┘
                 │   │                  │   │
          CONTINUE│   │DONE      CONTINUE│   │DONE
                 │   └────▶下模块check    │   └───▶下模块check
                 └──▶build               └───▶build
```

### 节点清单

| # | ID | 类型 | 前提 | 格式/write_to | 路由 |
|---|-----|------|------|--------------|------|
| 1 | o_init_plan | 🧠 | 分析输入→六大模块任务清单 | — | →o_init_todos |
| 2 | o_init_todos | 📝 | 写入Todo | entity:chain_todos | →o_world_check |
| **世界观** | | | | | |
| 3 | o_world_check | 🔀 | 需要世界观设定？ | — | YES→plan / NO→faction_check |
| 4 | o_world_plan | 🧠 | 规划世界观条目 | format:**outline** | →o_world_build |
| 5 | o_world_build | 📝 | 写入大纲 | entity:**outline** | →o_world_verify |
| 6 | o_world_verify | 🧠 | 查DB校验 | — | **CONTINUE→build** / DONE→faction_check |
| **世界势力** | | | | | |
| 7 | o_faction_check | 🔀 | 需要世界势力？ | — | YES→plan / NO→geo_check |
| 8 | o_faction_plan | 🧠 | 规划势力条目 | format:**outline** | →o_faction_build |
| 9 | o_faction_build | 📝 | 写入大纲 | entity:**outline** | →o_faction_verify |
| 10 | o_faction_verify | 🧠 | 查DB校验 | — | **CONTINUE→build** / DONE→geo_check |
| **地理** | | | | | |
| 11 | o_geo_check | 🔀 | 需要地理设定？ | — | YES→plan / NO→char_check |
| 12 | o_geo_plan | 🧠 | 规划地理条目 | format:**outline** | →o_geo_build |
| 13 | o_geo_build | 📝 | 写入大纲 | entity:**outline** | →o_geo_verify |
| 14 | o_geo_verify | 🧠 | 查DB校验 | — | **CONTINUE→build** / DONE→char_check |
| **人物设定** | | | | | |
| 15 | o_char_check | 🔀 | 需要人物设定？ | — | YES→plan / NO→power_check |
| 16 | o_char_plan | 🧠 | 规划人物条目 | format:**outline** | →o_char_build |
| 17 | o_char_build | 📝 | 写入大纲 | entity:**outline** | →o_char_verify |
| 18 | o_char_verify | 🧠 | 查DB校验 | — | **CONTINUE→build** / DONE→power_check |
| **能力体系** | | | | | |
| 19 | o_power_check | 🔀 | 需要能力体系？ | — | YES→plan / NO→story_check |
| 20 | o_power_plan | 🧠 | 规划能力条目 | format:**outline** | →o_power_build |
| 21 | o_power_build | 📝 | 写入大纲 | entity:**outline** | →o_power_verify |
| 22 | o_power_verify | 🧠 | 查DB校验 | — | **CONTINUE→build** / DONE→story_check |
| **剧情大纲** | | | | | |
| 23 | o_story_check | 🔀 | 需要剧情大纲？ | — | YES→plan / NO→结束 |
| 24 | o_story_plan | 🧠 | 规划剧情条目 | format:**outline** | →o_story_build |
| 25 | o_story_build | 📝 | 写入大纲 | entity:**outline** | →o_story_verify |
| 26 | o_story_verify | 🧠 | 查DB校验 | — | **CONTINUE→build** / DONE→结束 |

### 当前问题

1. **verify→build回路**：同地图链，CONTINUE 回 build 但 build 没有新数据
2. **人物设定模块** 的 plan 不做地图全景对齐（`m_id != "char"` 跳过），与其他模块不一致
3. **check 节点是纯 router**：用户说"需要此模块"应该是 ask 节点询问，而非 AI 判定

---

## 三链通用问题汇总

| 问题 | 影响链 | 描述 |
|------|--------|------|
| verify→build回路无新数据 | 🗺️ 📖 | CONTINUE 回 build 但 build 收到的是同一批 plan 输出 |
| 回路应指向 plan | 🗺️ 📖 | 正确的循环: plan→build→verify→CONTINUE→plan |
| check 节点应为 ask | 📖 | "是否需要创建此模块"应由用户决定，非AI |
| 势力写入无结构化上游 | 👤 | c_n3_build_faction 上游是自然语言 |
| fix 写入无结构化上游 | 👤 | c_n7_fix 上游 verify 仅输出 ROUTE |
| 人数判定与生成合并 | 👤 | c_n4_number 可改为 ask |
| verify 依赖 AI 查DB | 🗺️ 👤 📖 | verify 用 AI 查 {locations_full_detail} 判断 CONTINUE/DONE，多一次 AI 调用 |
