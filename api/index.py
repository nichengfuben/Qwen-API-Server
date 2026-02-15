"""
Vercel Serverless Function 入口
核心策略: 在导入项目代码之前，拦截所有文件系统操作，
将 'data/' 路径透明重定向到 '/tmp/data/'
"""

import sys
import os
import pathlib
import traceback

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# ========================================
# 路径重定向: data/ -> /tmp/data/
# 必须在导入任何项目代码之前完成
# ========================================

TMP_DATA = "/tmp/data"

# 创建所有子目录
for subdir in [
    "", "checkpoints", "large_texts", "anthropic_files",
    "tts", "audio_uploads", "generated_images", "generated_videos",
    "img", "batch_results",
]:
    os.makedirs(os.path.join(TMP_DATA, subdir), exist_ok=True)


def _redirect_path(path):
    """将 data/xxx 路径重定向到 /tmp/data/xxx"""
    s = str(path)
    # 匹配 'data/...' 或 './data/...' 或绝对路径中的 data
    if s == "data" or s.startswith("data/") or s.startswith("data\\"):
        return os.path.join(TMP_DATA, s[5:]) if len(s) > 5 else TMP_DATA
    if s == "./data" or s.startswith("./data/") or s.startswith("./data\\"):
        return os.path.join(TMP_DATA, s[7:]) if len(s) > 7 else TMP_DATA
    abs_data = os.path.join(ROOT_DIR, "data")
    if s == abs_data or s.startswith(abs_data + os.sep) or s.startswith(abs_data + "/"):
        rel = s[len(abs_data):]
        return TMP_DATA + rel if rel else TMP_DATA
    return s


# Patch os.makedirs
_original_makedirs = os.makedirs

def _patched_makedirs(name, mode=0o777, exist_ok=False):
    return _original_makedirs(_redirect_path(name), mode=mode, exist_ok=exist_ok)

os.makedirs = _patched_makedirs

# Patch os.mkdir
_original_mkdir = os.mkdir

def _patched_mkdir(path, mode=0o777, *args, **kwargs):
    return _original_mkdir(_redirect_path(path), mode, *args, **kwargs)

os.mkdir = _patched_mkdir

# Patch pathlib.Path.mkdir
_original_path_mkdir = pathlib.Path.mkdir

def _patched_path_mkdir(self, mode=0o777, parents=False, exist_ok=False):
    redirected = _redirect_path(str(self))
    return _original_path_mkdir(
        pathlib.Path(redirected), mode=mode, parents=parents, exist_ok=exist_ok
    )

pathlib.Path.mkdir = _patched_path_mkdir

# Patch open (用于文件读写重定向)
_original_open = open

def _patched_open(file, mode="r", *args, **kwargs):
    if isinstance(file, (str, pathlib.Path)):
        file = _redirect_path(str(file))
    return _original_open(file, mode, *args, **kwargs)

import builtins
builtins.open = _patched_open

# Patch pathlib.Path 的其他文件操作
_original_path_exists = pathlib.Path.exists

def _patched_path_exists(self):
    redirected = _redirect_path(str(self))
    return _original_path_exists(pathlib.Path(redirected))

# 只对 data 路径生效的 exists
_real_exists = pathlib.Path.exists

def _smart_exists(self):
    s = str(self)
    if "data" in s:
        redirected = _redirect_path(s)
        if redirected != s:
            return _real_exists(pathlib.Path(redirected))
    return _real_exists(self)

pathlib.Path.exists = _smart_exists

# 环境变量覆盖
os.environ.setdefault("DATA_DIR", TMP_DATA)

# ========================================
# 导入 FastAPI app
# ========================================

try:
    from qwen_server import app
except Exception as e:
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    app = FastAPI()
    _error_info = {
        "error": str(e),
        "type": type(e).__name__,
        "traceback": traceback.format_exc(),
        "python": sys.version,
        "cwd": os.getcwd(),
        "root_dir": ROOT_DIR,
        "root_files": sorted(os.listdir(ROOT_DIR))[:30],
        "tmp_data_contents": sorted(os.listdir(TMP_DATA)),
    }

    @app.api_route(
        "/{path:path}",
        methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    )
    async def catch_all(path: str = ""):
        return JSONResponse(status_code=500, content=_error_info)
