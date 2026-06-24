---
name: workflow
description: 任务执行工作流：解析Task Package、编码实现、运行lint/test、提交代码、输出Execution Report，含阻塞升级机制和提交规范
---

# Workflow — 任务执行工作流

本 skill 涵盖本项目完整的 **Architect → Execution Agent** 工作流协议。当收到任务包指令或需要回报执行结果时调用。

## 角色定义

| 角色 | 职责 |
|------|------|
| **架构师** | 需求分析、架构设计、任务拆分、技术选型，以 Task Package 格式下达任务 |
| **执行 Agent** | 按指令编码实现、运行测试、提交代码，以 Execution Report 格式回报 |

## Task Package 解析

收到的任务包必须包含:
- **任务编号:** #T{序号}
- **优先级:** P0-Critical / P1-High / P2-Medium / P3-Low
- **执行步骤 (Action Items):** 具体文件+函数+测试要求
- **交付物要求:** 需修改/创建的文件路径列表
- **验收标准:** 测试通过、Lint/Type Check 无报错、功能满足需求

## 执行流程

1. 阅读相关现有代码，理解上下文
2. 按步骤逐一实现，每步完成后验证
3. 编写对应的单元测试
4. 运行 `ruff check .` + `mypy .` + `pytest -v`
5. **MANDATORY:** `git add -A && git commit -m "[#T{编号}] {type}: {描述}"`
6. 确认 `git status` 干净
7. 输出 Execution Report

## Execution Report 格式 (MUST FOLLOW)

```
# 📤 [EXECUTION_REPORT] 执行结果报告
**回应任务编号:** #T{编号}

## 1. 执行状态 (Status)
- [x] ✅ 成功 / [ ] ⚠️ 部分 / [ ] ❌ 失败

## 2. 工作详情 (Work Details)
- **新增/修改的文件:** (路径+改动摘要)
- **运行命令与结果:** (lint/test 输出)

## 3. 遇到的问题与报错 (Issues & Errors)
- 无则填"无"

## 4. 需要架构师决策的事项 (Request for Decision)
- 无则填"无"
```

## 阻塞升级机制

Agent **不得**自行做架构级决策（引入新库、改 schema、改架构）。遇到以下情况必须在 Report 第 4 节提请决策:
- 需要新增第三方依赖
- 当前架构设计冲突
- 技术选型无法确定

## 提交规范

- Format: `[#T{编号}] {type}: {描述}`
- Type: `feat` / `fix` / `refactor` / `test` / `docs` / `chore`
- 未经批准不得 push 到远程

## 测试要求

- 每个任务包的功能代码必须有对应单元测试
- 核心逻辑覆盖率 ≥ 80%
- 提交前必须通过: `ruff check` + `mypy` + `pytest`

## Agent 能力参考

| 能力 | 工具 |
|------|------|
| 文件读写 | read_file, write_file, edit_file |
| 代码搜索 | grep, glob, ls |
| 终端执行 | bash (pip, ruff, mypy, pytest, git) |
| 网络查询 | web_search, web_fetch (查官方文档) |
| 代码分析 | Ruff Lint, MyPy Type Check |

## 安全约束

- 不得将 API Key/密码写入代码或提交仓库
- 不得执行未经授权的网络请求
- 所有文件操作限定项目工作区内
