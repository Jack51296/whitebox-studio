"""Client for the isolated vision worker (.venv-vision): torch models run out of process, results come back as files."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from ..config import REPO_ROOT, load_settings
from ..errors import WbsError
from ..jsonio import read_json, write_json
from .fetch import models_dir

WORKER_SCRIPT = Path(__file__).with_name("worker_main.py")


class VisionUnavailable(WbsError):
    """The requested vision backend cannot run here (missing environment, package, weights or GPU)."""


def vision_python() -> Path | None:
    configured = load_settings().vision.python
    candidates = [Path(configured)] if configured else []
    candidates += [REPO_ROOT / ".venv-vision" / "Scripts" / "python.exe", REPO_ROOT / ".venv-vision" / "bin" / "python"]
    for path in candidates:
        path = path if path.is_absolute() else REPO_ROOT / path
        if path.exists():
            return path
    return None


def run(command: str, request: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
    python = vision_python()
    if python is None:
        raise VisionUnavailable("未安装视觉工作环境（运行 scripts/setup_vision.ps1 或 setup_vision.sh）")
    timeout = timeout or load_settings().vision.worker_timeout_s
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "WBS_MODELS_DIR": str(models_dir()),
           "PYTHONPATH": "", "PYTHONNOUSERSITE": "1"}
    with tempfile.TemporaryDirectory(prefix="wbs_vision_") as tmp:
        req, resp = Path(tmp) / "request.json", Path(tmp) / "response.json"
        write_json(req, request)
        started = time.time()
        try:
            proc = subprocess.run([str(python), str(WORKER_SCRIPT), command, str(req), str(resp)], env=env,
                                  capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise VisionUnavailable(f"视觉工作进程超时（{timeout}s）：{command}") from exc
        if not resp.exists():
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-8:]
            raise VisionUnavailable(f"视觉工作进程失败（exit {proc.returncode}）：{' | '.join(tail)}")
        out = read_json(resp)
    if out.get("error"):
        raise VisionUnavailable(f"{command}: {out['error']}")
    out.setdefault("seconds", round(time.time() - started, 2))
    return out


def selftest(refresh: bool = False) -> dict[str, Any]:
    """Worker packages / GPU, cached next to the models (re-probed when the interpreter changes)."""
    python = vision_python()
    if python is None:
        return {"installed": False, "reason": "未安装视觉工作环境（.venv-vision）"}
    cache = models_dir() / "worker_selftest.json"
    stamp = [str(python), round(python.stat().st_mtime, 1)]
    if cache.exists() and not refresh:
        cached = read_json(cache)
        if cached.get("stamp") == stamp:
            return cached
    try:
        result = {"installed": True, **run("selftest", {}, timeout=600)}
    except VisionUnavailable as exc:
        result = {"installed": True, "ok": False, "reason": str(exc)}
    result["stamp"] = stamp
    cache.parent.mkdir(parents=True, exist_ok=True)
    write_json(cache, result)
    return result


def main_python() -> str:
    return json.dumps({"python": sys.executable})
