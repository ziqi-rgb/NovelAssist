"""一键启动脚本 — 启动后端服务并自动打开浏览器"""
import threading
import time
import webbrowser
import uvicorn


def start_server():
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000)


if __name__ == "__main__":
    t = threading.Thread(target=start_server, daemon=True)
    t.start()
    time.sleep(1.5)
    webbrowser.open("http://127.0.0.1:8000")
    t.join()
