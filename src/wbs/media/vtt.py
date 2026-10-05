"""WebVTT director notes (optional subtitle track, never burned into the clean video)."""

from __future__ import annotations

from pathlib import Path

from ..jsonio import write_text


def timestamp(t: float) -> str:
    t = max(t, 0.0)
    hours, rem = divmod(t, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{int(hours):02}:{int(minutes):02}:{seconds:06.3f}"


def write_vtt(cues: list[tuple[float, float, str]], path: Path) -> Path:
    blocks = ["WEBVTT", ""]
    for start, end, text in cues:
        blocks += [f"{timestamp(start)} --> {timestamp(end)}", text.strip(), ""]
    return write_text(path, "\n".join(blocks))
