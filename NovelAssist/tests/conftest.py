"""pytest 全局配置 — 在导入 backend 之前把工作目录切到临时目录，隔离测试数据库。"""
import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.abspath(__file__))
_PROJECT = os.path.dirname(_ROOT)  # NovelAssist/
if _PROJECT not in sys.path:
    sys.path.insert(0, _PROJECT)

# database.py 以 os.path.abspath(".") 定位 .data/novel.db；先切目录即可获得全新库
_TMPDIR = tempfile.mkdtemp(prefix="novelassist_test_")
os.chdir(_TMPDIR)
