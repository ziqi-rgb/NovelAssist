"""一键启动脚本 — 启动后端服务并自动打开浏览器"""
import sys
import os

# [核心补丁]: 修复 PyInstaller --noconsole 模式下标准流缺失导致的崩溃
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

import threading
import time
import webbrowser

import uvicorn
from backend.main import app


def start_server():
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    if sys.platform.startswith("win"):
        import multiprocessing

        multiprocessing.freeze_support()

    print(">>> AI小说推演引擎 V2.0 后端已启动，请勿关闭此窗口... <<<")

    t = threading.Thread(target=start_server, daemon=True)
    t.start()
    time.sleep(1.5)
    webbrowser.open("http://127.0.0.1:8000")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
