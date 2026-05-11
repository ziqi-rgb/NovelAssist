---
description: Commit with proper task format [#T{id}] {type}: {description}
---
Create a git commit for the current changes.

The commit message format MUST be: `[#T{id}] {type}: {description}`

Where:
- `{id}` = task number (ask me if not obvious from context)
- `{type}` = feat | fix | refactor | test | docs | chore
- `{description}` = Chinese short description of the change

Run: `git status`, `git diff`, then `git add` the relevant files and `git commit` with the properly formatted message.
