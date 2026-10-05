"""Media check (team media-check.json format): decode, frames, fps, resolution, audio, checksum."""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path
from typing import Any

from ..hashing import sha256_file
from ..media.ffmpeg import decode_errors, probe


def media_check(video: Path, scene: dict | None = None) -> dict[str, Any]:
    info = probe(video, count_frames=True)
    errors = decode_errors(video)
    report: dict[str, Any] = {
        "passed": False, "frames": info["frames"], "duration_seconds": round(info["frames"] / info["fps"], 4) if info["fps"] else 0,
        "fps": info["fps_str"], "resolution": [info["width"], info["height"]], "full_decode_errors": errors,
        "audio_streams": info["audio_streams"], "sha256": sha256_file(video), "codec": info["codec"],
        "pix_fmt": info["pix_fmt"],
    }
    problems = []
    if errors:
        problems.append(f"{errors} decode error(s)")
    if info["audio_streams"]:
        problems.append("audio stream present (white-model reference must be silent)")
    if scene is not None:
        expected_frames = int(round(scene["duration_s"] * scene["fps"]))
        if info["frames"] != expected_frames:
            problems.append(f"frames {info['frames']} != expected {expected_frames}")
        if Fraction(info["fps_str"]) != Fraction(int(scene["fps"]), 1):
            problems.append(f"fps {info['fps_str']} != {scene['fps']}/1")
        if [info["width"], info["height"]] != list(scene["resolution"]):
            problems.append(f"resolution {info['width']}x{info['height']} != {scene['resolution']}")
        report["expected"] = {"frames": expected_frames, "fps": f"{scene['fps']}/1", "resolution": list(scene["resolution"])}
    report["passed"] = not problems
    report["problems"] = problems
    return report
