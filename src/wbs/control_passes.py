"""Control-pass videos for video-to-video backends: depth / segmentation (Blender) and edges (Canny on the render)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import cv2

from .config import Settings, VideoEncode
from .errors import WbsError
from .jsonio import read_json, write_json
from .layout import JobPaths
from .media.ffmpeg import encode_frames
from .render import run_blender
from .tools import require_tool

PASSES = ("depth", "seg", "edge")
FOLDER = "控制通道"
CONTROL_ENCODE = VideoEncode(codec="libx264", crf=12, preset="medium", pix_fmt="yuv420p")


def edge_video(src: Path, dst: Path, low: int = 80, high: int = 160) -> dict[str, Any]:
    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        raise WbsError(f"cannot open {src}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    width, height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    dst.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [require_tool("ffmpeg"), "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{width}x{height}",
         "-r", f"{fps}", "-i", "-", "-c:v", CONTROL_ENCODE.codec, "-crf", str(CONTROL_ENCODE.crf),
         "-pix_fmt", CONTROL_ENCODE.pix_fmt, "-an", str(dst)], stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    frames = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            gray = cv2.GaussianBlur(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (3, 3), 0)
            assert proc.stdin is not None
            proc.stdin.write(cv2.Canny(gray, low, high).tobytes())
            frames += 1
    finally:
        cap.release()
        if proc.stdin:
            proc.stdin.close()
        err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        code = proc.wait()
    if code != 0:
        raise WbsError(f"ffmpeg failed writing {dst}: {err[-400:]}")
    return {"video": dst.name, "frames": frames, "encoding": f"Canny({low}, {high}) on the white-box render, white edges"}


def render_passes(job: JobPaths, settings: Settings, names: list[str]) -> dict[str, Any]:
    unknown = [n for n in names if n not in PASSES]
    if unknown:
        raise WbsError(f"unknown control pass {unknown}; choose from {PASSES}")
    scene = read_json(job.scene)
    folder = job.root / FOLDER
    out: dict[str, Any] = {}
    in_blender = [n for n in names if n in ("seg", "depth")]
    if in_blender:
        status = run_blender(job, settings, render=False, audit=False, save_blend=False, passes=in_blender)
        for name in in_blender:
            frames_dir = job.render_dir / "passes" / name
            dest = folder / f"{scene['title']}_{name}.mp4"
            encode_frames(frames_dir, dest, int(scene["fps"]), CONTROL_ENCODE)
            shutil.rmtree(frames_dir, ignore_errors=True)
            info = dict(status.get("passes", {}).get(name, {}))
            legend = info.pop("legend", None)
            if legend is not None:
                write_json(folder / "seg_legend.json", {"schema": "wbs.seg_legend/1.0", "objects": legend})
                info["legend"] = "seg_legend.json"
            out[name] = {"video": dest.name, **info}
    if "edge" in names:
        video = job.video(scene["title"])
        if not video.exists():
            raise WbsError(f"{job.key}: render the white-box video before the edge pass")
        out["edge"] = edge_video(video, folder / f"{scene['title']}_edge.mp4")
    report = {"schema": "wbs.control_passes/1.0", "folder": FOLDER, "passes": out,
              "note": "控制通道只作为可选 V2V 后端（Cosmos-Transfer / Wan VACE）的输入；默认流程仍只导出提交包"}
    write_json(job.report("控制通道.json"), report)
    job.update_meta(control_passes=sorted(out))
    return report
