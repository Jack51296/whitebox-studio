"""Thin ffmpeg / ffprobe wrappers (no shell, explicit arguments, errors surfaced)."""

from __future__ import annotations

import json
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any

from ..config import VideoEncode
from ..errors import WbsError
from ..tools import require_tool


def _run(args: list[str], timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if proc.returncode != 0:
        raise WbsError(f"{Path(args[0]).name} failed ({proc.returncode}): {proc.stderr.strip()[-800:]}")
    return proc


def probe(video: Path, count_frames: bool = False) -> dict[str, Any]:
    args = [require_tool("ffprobe"), "-v", "error", "-print_format", "json", "-show_streams", "-show_format"]
    if count_frames:
        args.append("-count_frames")
    data = json.loads(_run(args + [str(video)]).stdout)
    streams = data.get("streams", [])
    vstreams = [s for s in streams if s.get("codec_type") == "video"]
    if not vstreams:
        raise WbsError(f"{video}: no video stream")
    v = vstreams[0]
    rate = Fraction(v.get("avg_frame_rate") or v.get("r_frame_rate") or "0/1")
    if rate == 0:
        rate = Fraction(v.get("r_frame_rate") or "24/1")
    duration = float(v.get("duration") or data.get("format", {}).get("duration") or 0)
    frames = v.get("nb_read_frames") if count_frames else v.get("nb_frames")
    frames = int(frames) if frames not in (None, "N/A") else round(duration * float(rate))
    return {
        "width": int(v["width"]), "height": int(v["height"]), "fps": float(rate),
        "fps_str": f"{rate.numerator}/{rate.denominator}", "frames": frames, "duration_s": round(duration, 4),
        "codec": v.get("codec_name"), "pix_fmt": v.get("pix_fmt"),
        "audio_streams": sum(1 for s in streams if s.get("codec_type") == "audio"),
    }


def decode_errors(video: Path) -> int:
    proc = subprocess.run([require_tool("ffmpeg"), "-v", "error", "-i", str(video), "-f", "null", "-"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    lines = [line for line in proc.stderr.splitlines() if line.strip()]
    return len(lines) + (1 if proc.returncode != 0 and not lines else 0)


def encode_frames(frames_dir: Path, out: Path, fps: int, encode: VideoEncode, pattern: str = "f%04d.png",
                  start_number: int = 1) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    _run([require_tool("ffmpeg"), "-y", "-v", "error", "-framerate", str(fps), "-start_number", str(start_number),
          "-i", str(frames_dir / pattern), "-c:v", encode.codec, "-crf", str(encode.crf), "-preset", encode.preset,
          "-pix_fmt", encode.pix_fmt, "-an", "-movflags", "+faststart", str(out)])
    return out


def extract_frame(video: Path, out_png: Path, *, frame_index: int | None = None, time_s: float | None = None) -> Path:
    out_png.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = require_tool("ffmpeg")
    if frame_index is not None:
        args = [ffmpeg, "-y", "-v", "error", "-i", str(video), "-vf", f"select=eq(n\\,{int(frame_index)})",
                "-fps_mode", "passthrough", "-frames:v", "1", str(out_png)]
    elif time_s is not None:
        args = [ffmpeg, "-y", "-v", "error", "-ss", f"{max(time_s, 0):.4f}", "-i", str(video), "-frames:v", "1",
                str(out_png)]
    else:
        raise ValueError("frame_index or time_s required")
    _run(args)
    if not out_png.exists():
        raise WbsError(f"no frame extracted from {video} ({frame_index=}, {time_s=})")
    return out_png


def side_by_side(left: Path, right: Path, out: Path, encode: VideoEncode, height: int = 720) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    graph = (f"[0:v]scale=-2:{height},setsar=1[a];[1:v]scale=-2:{height},setsar=1[b];"
             "[a][b]hstack=inputs=2[v]")
    _run([require_tool("ffmpeg"), "-y", "-v", "error", "-i", str(left), "-i", str(right), "-filter_complex", graph,
          "-map", "[v]", "-c:v", encode.codec, "-crf", str(encode.crf), "-preset", encode.preset,
          "-pix_fmt", encode.pix_fmt, "-an", "-shortest", str(out)])
    return out


def transcode(src: Path, out: Path, encode: VideoEncode, extra: list[str] | None = None) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    _run([require_tool("ffmpeg"), "-y", "-v", "error", "-i", str(src), *(extra or []), "-c:v", encode.codec,
          "-crf", str(encode.crf), "-preset", encode.preset, "-pix_fmt", encode.pix_fmt, "-an", str(out)])
    return out
