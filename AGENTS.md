# Writer Project

A Python 3.11 + FastAPI project.

## Project Structure

```
writer/
├── docs/                    # 项目规范文档
│   ├── WORKFLOW.md          # 工作流程规范（任务包→执行报告）
│   └── SKILLS.md            # 执行 Agent 能力需求
├── .opencode/               # OpenCode 配置
│   └── commands/            # 自定义命令
└── .venv/                   # Python 虚拟环境
```

## Tech Stack

| Layer | Tech | Notes |
|-------|------|-------|
| Language | Python 3.11 | Type hints required |
| Web | FastAPI | async/await |
| Server | Uvicorn | |
| AI | OpenAI SDK | |
| Test | pytest | |
| Lint | Ruff | PEP 8 |
| Type Check | MyPy | strict mode |
| Git Hooks | pre-commit | |

## Code Conventions

- Follow PEP 8, enforced by Ruff
- All functions must have type annotations
- Use `async/await` for I/O operations
- No redundant comments

## Build, Lint, Test Commands

```bash
ruff check .           # Lint
mypy .                 # Type check
pytest -v              # Run tests
pytest --cov           # Tests + coverage
```

## Workflow Protocol (CRITICAL)

This project uses a strict **Architect → Execution Agent** workflow. Read the protocol immediately:

@docs/WORKFLOW.md
@docs/SKILLS.md

### Summary of Protocol

1. Architect issues **Task Package** in format: `# 📥 [TASK_PACKAGE] #T{id}`
2. Execution Agent implements, then returns **Execution Report**: `# 📤 [EXECUTION_REPORT]`
3. Agent must NOT make architecture-level decisions — escalate via "Request for Decision"
4. Commit format: `[#T{id}] {type}: {description}`
5. Every task must have tests and pass lint + type check before reporting

## Git Commit Format

```
[#T001] feat: 简短描述
[#T001] fix: 简短描述
[#T001] refactor: 简短描述
[#T001] test: 简短描述
```

## When Receiving a Task Package

1. Read `@docs/WORKFLOW.md` and `@docs/SKILLS.md` if not yet loaded
2. Parse the task instructions (task number, steps, deliverables)
3. Read relevant existing code before editing
4. Implement step by step
5. Run lint + type check + tests
6. **Run `git add -A && git commit -m "[#T{id}] {type}: {描述}"`** (MANDATORY)
7. Verify `git status` shows clean working tree
8. Write execution report in the exact format specified
9. List any issues or decisions needed

## When Blocked

- Do NOT guess architecture decisions
- Report in "Request for Decision" section of execution report
- Wait for Architect's response before proceeding
