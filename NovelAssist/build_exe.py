"""PyInstaller 打包编译脚本"""
import os
import PyInstaller.__main__

sep = os.pathsep
frontend_arg = f"frontend{sep}frontend"

PyInstaller.__main__.run([
    "run.py",
    "--name=NovelAssist",
    "--onefile",
    "--console",
    f"--add-data={frontend_arg}",
    "--hidden-import=uvicorn",
    "--hidden-import=uvicorn.logging",
    "--hidden-import=uvicorn.loops",
    "--hidden-import=uvicorn.loops.auto",
    "--hidden-import=uvicorn.protocols.http.auto",
    "--hidden-import=uvicorn.protocols.websockets.auto",
    "--hidden-import=uvicorn.lifespan.on",
    "--hidden-import=fastapi",
    "--hidden-import=sqlalchemy",
    "--hidden-import=openai",
    "--hidden-import=httpx",
    "--hidden-import=pydantic",
    "--hidden-import=dotenv",
    "--hidden-import=multiprocessing",
    "--clean",
    "--noconfirm",
])
