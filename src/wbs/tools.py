"""Locate external executables: config / WBS_* env → tools/tools.json → PATH."""

from __future__ import annotations

import json
import shutil
import subprocess
from functools import cache
from pathlib import Path

from .config import REPO_ROOT, load_settings
from .errors import ToolMissing

TOOLS_JSON = REPO_ROOT / "tools" / "tools.json"
HINTS = {
    "blender": "运行 scripts/fetch_tools.ps1 下载便携版，或设置 WBS_BLENDER / configs 中 tools.blender",
    "ffmpeg": "运行 scripts/fetch_tools.ps1 下载，或设置 WBS_FFMPEG / 加入 PATH",
    "ffprobe": "运行 scripts/fetch_tools.ps1 下载，或设置 WBS_FFPROBE / 加入 PATH",
    "face_model": "运行 scripts/fetch_tools.ps1 下载 YuNet 人脸检测模型，或设置 WBS_FACE_MODEL",
}
DATA_FILES = {"face_model"}


def _resolve(candidate: str) -> str | None:
    path = Path(candidate)
    if not path.is_absolute():
        repo_relative = REPO_ROOT / path
        if repo_relative.exists():
            return str(repo_relative)
    if path.exists():
        return str(path)
    return shutil.which(candidate)


def _from_tools_json(name: str) -> str | None:
    if not TOOLS_JSON.exists():
        return None
    data = json.loads(TOOLS_JSON.read_text(encoding="utf-8-sig"))
    value = data.get(name)
    return value if value and Path(value).exists() else None


def find_tool(name: str) -> str | None:
    configured = getattr(load_settings().tools, name, None)
    if configured:
        return _resolve(configured)
    if name in DATA_FILES:
        return _from_tools_json(name)
    return _from_tools_json(name) or shutil.which(name)


def require_tool(name: str) -> str:
    path = find_tool(name)
    if not path:
        raise ToolMissing(f"找不到 {name}：{HINTS.get(name, '')}")
    return path


@cache
def tool_version(name: str) -> str | None:
    path = find_tool(name)
    if not path:
        return None
    flag = "--version" if name == "blender" else "-version"
    try:
        out = subprocess.run([path, flag], capture_output=True, text=True, timeout=120,
                             encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return None
    first = (out.stdout or out.stderr).strip().splitlines()
    return first[0] if first else None
