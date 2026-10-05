"""Interchange models for rendered jobs: FBX / GLB from the job's .blend and a camera-cut list from scene.json."""

from __future__ import annotations

import csv
import subprocess
import time
from pathlib import Path
from typing import Any

from .blender import kinematics as K
from .camera_language import label
from .config import Settings
from .errors import WbsError
from .jsonio import read_json
from .layout import JobPaths, safe_filename
from .tools import require_tool

EXPORTER = Path(__file__).resolve().parent / "blender" / "model_export.py"
EXPORT_DIR = "模型导出"
CUT_LIST = "镜头切换.csv"
CUT_HEADER = ["镜头", "相机", "起始帧（文件帧号）", "结束帧（文件帧号）", "成片起始秒", "成片结束秒", "焦距mm", "景别", "角度", "运镜"]


def camera_cuts(scene: dict) -> list[list[Any]]:
    """Which camera is live over which frames (timeline markers do not survive FBX/GLB)."""
    ev = K.SceneEvaluator(scene)
    rows = []
    for i, shot in enumerate(scene["shots"]):
        first, last = ev.shot_frames(i)
        rows.append([shot["id"], f"CAMERA_{shot['id']}", first, last, round(shot["start_s"], 3), round(shot["end_s"], 3),
                     round(float(shot.get("lens_mm", 32.0)), 1), label("framing", shot.get("framing", "")),
                     label("angle", shot.get("angle", "")), label("move", shot.get("move", ""))])
    return rows


def write_cut_list(scene: dict, path: Path) -> Path:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(CUT_HEADER)
        writer.writerows(camera_cuts(scene))
    return path


def _free_folder(root: Path) -> Path:
    """模型导出/ unless it already holds files; earlier exports are never overwritten."""
    out = root / EXPORT_DIR
    if not out.exists() or not any(out.iterdir()):
        return out
    stamp, n = time.strftime("%Y%m%d-%H%M%S"), 1
    out = root / f"{EXPORT_DIR}_{stamp}"
    while out.exists():
        n += 1
        out = root / f"{EXPORT_DIR}_{stamp}_{n}"
    return out


def export_models(job: JobPaths, settings: Settings, formats: tuple[str, ...] = ("fbx", "glb"),
                  timeout: int | None = None) -> dict[str, Any]:
    scene = read_json(job.scene)
    blend = job.blend(scene["title"])
    if not blend.exists():
        raise WbsError(f"{job.key} 没有 .blend：先渲染（wbs render 或 wbs process）再导出模型")
    out = _free_folder(job.root)
    out.mkdir(parents=True, exist_ok=True)
    status = out / "model_export.json"
    cmd = [require_tool("blender"), "-b", str(blend), "--factory-startup", "-noaudio", "--python-exit-code", "1",
           "--python", str(EXPORTER), "--", "--out", str(out), "--stem", safe_filename(scene["title"], job.job_id),
           "--formats", ",".join(formats), "--status", str(status)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout or settings.render.timeout_s)
    if proc.returncode != 0 or not status.exists():
        raise WbsError(f"Blender 模型导出失败（exit {proc.returncode}）：{(proc.stdout + proc.stderr)[-600:]}")
    write_cut_list(scene, out / CUT_LIST)
    result = {**read_json(status), "folder": out.relative_to(job.root).as_posix(), "cut_list": CUT_LIST}
    job.update_meta(model_export={"folder": result["folder"], "files": result["files"],
                                  "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    return result
