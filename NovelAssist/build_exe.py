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
    "--clean",
    "--noconfirm",
])
