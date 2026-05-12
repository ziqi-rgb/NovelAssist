# PyInstaller 打包规范

## 前置条件

- Python 3.11 + 虚拟环境已激活
- `pip install pyinstaller` 已安装
- 项目结构：`backend/main.py` 为入口，`frontend/` 为静态资源

## 代码层要求

### 1. 相对导入容错

所有 `backend/` 内的跨模块导入必须使用 `try/except ImportError` 降级：

```python
# main.py / database.py / models.py
try:
    from .database import Base, engine, get_db, run_migration
    from .models import (...)
except ImportError:
    from database import Base, engine, get_db, run_migration
    from models import (...)
```

涉及文件：`main.py`、`database.py`、`models.py` 共 5 处相对导入。

### 2. 动态路径映射

**数据库** (`database.py`)：打包时存放到 `.exe` 同级 `.data/` 目录：

```python
if getattr(sys, "frozen", False):
    base_dir = os.path.dirname(sys.executable)
else:
    base_dir = os.path.abspath(".")

DATA_DIR = os.path.join(base_dir, ".data")
os.makedirs(DATA_DIR, exist_ok=True)
DATABASE_URL = f"sqlite:///{os.path.join(DATA_DIR, 'novel.db')}"
```

**前端静态文件** (`main.py`)：打包时从 `sys._MEIPASS` 临时解压目录读取：

```python
if getattr(sys, "frozen", False):
    frontend_path = os.path.join(sys._MEIPASS, "frontend")
else:
    frontend_path = str(Path(__file__).resolve().parent.parent / "frontend")
app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")
```

### 3. 启动入口 (`main.py` 底部)

```python
import multiprocessing
import threading
import webbrowser

def open_browser():
    webbrowser.open("http://127.0.0.1:8000")

if __name__ == "__main__":
    multiprocessing.freeze_support()
    threading.Timer(1.5, open_browser).start()
    uvicorn.run(app, host="127.0.0.1", port=8000)
```

## 编译命令

在 `NovelAssist/` 目录下执行：

```bash
pyinstaller \
  --name "NovelAssist" \
  --onefile \
  --add-data "frontend;frontend" \
  --hidden-import uvicorn.logging \
  --hidden-import uvicorn.loops \
  --hidden-import uvicorn.loops.auto \
  --hidden-import uvicorn.protocols.http.auto \
  --hidden-import uvicorn.protocols.websockets.auto \
  backend/main.py
```

> **注意：** Windows 下 `--add-data` 分隔符为分号 `;`，Linux/macOS 为冒号 `:`。

## 参数说明

| 参数 | 用途 |
|------|------|
| `--name "NovelAssist"` | 输出 `.exe` 文件名（避免中文以避免 PyInstaller 编码问题） |
| `--onefile` | 打包为单一可执行文件 |
| `--add-data "frontend;frontend"` | 将 `frontend/` 嵌入 `sys._MEIPASS/frontend/` |
| `--hidden-import uvicorn.*` | 补全 uvicorn 的隐式子模块依赖 |

## 产物

- `dist/NovelAssist.exe` (约 22 MB)
- `build/` 和 `*.spec` 为构建中间产物，`.gitignore` 应排除

## 排除清单 (`.gitignore`)

```
dist/
build/
*.spec
```

## 常见问题

| 问题 | 原因 | 解决 |
|------|------|------|
| `ImportError: attempted relative import` | PyInstaller 将 `main.py` 作为顶层脚本运行，相对导入失效 | 添加 `try/except ImportError` 降级 |
| 中文文件名乱码 | PyInstaller GBK 编码问题 | 使用 ASCII 命名，用户可手动重命名 |
| 数据库丢失 | `.exe` 临时解压目录不可写 | 数据库路径指向 `sys.executable` 同级 `.data/` |
| Missing `pysqlite2/MySQLdb/psycopg2` 警告 | SQLAlchemy 尝试加载其他 DB 驱动 | 可忽略，仅使用 SQLite |
