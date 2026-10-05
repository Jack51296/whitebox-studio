"""Subject occupancy backends: motion residual (built-in) or text-prompted detection in the vision worker.

All backends return, per shot, ``{frame: {"bbox", "area", "center", "components", ...}}`` sampled on the
same frames, so the solver and the static gate do not care which one ran.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from . import occupancy as O

PROMPTS = {"person": "person.", "animal": "animal.", "bird": "bird.", "fish": "fish.", "vehicle": "car. vehicle.",
           "robot": "robot.", "product": "object."}
WORKER_COMMAND = {"grounding_dino": "detect_subject", "sam3": "sam3_subject"}


def sample_frames(start: int, end: int, step: int) -> list[int]:
    gap = max(1, min(3, end - start))
    frames = list(range(start + gap, end + 1, step))
    if end not in frames and end - gap >= start:
        frames.append(end)
    return frames


def shot_samples(video: Path | None, grays: np.ndarray, ranges: list[tuple[int, int]], step: int, *,
                 backend: str | None = None, subject: str = "person", prompt: str | None = None,
                 masks: bool = True, mask_dir: Path | None = None) -> tuple[list[dict[int, dict[str, Any]]], dict[str, Any]]:
    from ..vision import resolve
    from ..vision.worker import VisionUnavailable, run

    choice = resolve("subject", backend)
    info: dict[str, Any] = {"backend": choice.used, "requested": choice.requested}
    if choice.skipped:
        info["fallback_reasons"] = choice.skipped
    if choice.used != "motion" and video is not None:
        wanted = {n: sample_frames(a, b, step) for n, (a, b) in enumerate(ranges)}
        request = {"video": str(video), "frames": sorted({f for fs in wanted.values() for f in fs}),
                   "prompt": prompt or PROMPTS.get(subject, "person."), "masks": masks, "width": 640,
                   "mask_dir": str(mask_dir) if mask_dir else None}
        try:
            result = run(WORKER_COMMAND[choice.used], request)
        except VisionUnavailable as exc:
            info.update(backend="motion", error=str(exc))
        else:
            found = {int(k): v for k, v in result["frames"].items()}
            info.update(model=result.get("backend"), device=result.get("device"), seconds=result.get("seconds"),
                        prompt=request["prompt"], detected_frames=len(found), sampled_frames=len(request["frames"]))
            return [{f: found[f] for f in fs if f in found} for fs in wanted.values()], info
    return [O.shot_occupancy(grays, a, b, step) for a, b in ranges], info
