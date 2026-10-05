"""Cut detection. Built-in: per-frame grey difference with an isolated-spike criterion ([D2]: 防固定阈值漏切).

Optional backends (``vision.cuts``): PySceneDetect AdaptiveDetector (BSD-3) and TransNetV2 ONNX (MIT);
when they are missing ``find_cuts`` falls back along transnetv2 → pyscenedetect → builtin.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

METHODS = {
    "builtin": "grey difference isolated spike (3 × local median, 2 × each neighbour, floor 6)",
    "pyscenedetect": "PySceneDetect AdaptiveDetector (BSD-3)",
    "transnetv2": "TransNetV2 ONNX (MIT), threshold 0.5",
}


def frame_diffs(grays: np.ndarray) -> np.ndarray:
    d = np.zeros(len(grays), dtype=float)
    if len(grays) > 1:
        d[1:] = np.abs(grays[1:].astype(np.int16) - grays[:-1].astype(np.int16)).mean(axis=(1, 2))
    return d


def detect_cuts(grays: np.ndarray, fps: float, ratio: float = 3.0, isolation: float = 2.0, floor: float = 6.0,
                min_gap_s: float = 0.3) -> tuple[list[int], np.ndarray]:
    """Return 0-based indices of the first frame of each new shot, and the difference signal.

    A frame is a cut when its difference is an isolated spike: ``ratio`` × the local median on both
    sides and ``isolation`` × each neighbour. Fast continuous motion raises a plateau, not a spike,
    so it is not cut; quiet shots with small absolute changes still are.
    """
    d = frame_diffs(grays)
    w = max(3, int(round(fps / 2)))
    candidates = []
    for i in range(1, len(d)):
        window = np.concatenate([d[max(1, i - w):i], d[i + 1:i + 1 + w]])
        base = float(np.median(window)) if len(window) else 0.0
        neighbours = [d[j] for j in (i - 1, i + 1) if 0 < j < len(d)]
        if d[i] > floor and d[i] > ratio * (base + 0.5) and all(d[i] >= isolation * n for n in neighbours):
            candidates.append((i, d[i]))
    min_gap = max(1, int(round(min_gap_s * fps)))
    cuts: list[int] = []
    for i, _ in sorted(candidates, key=lambda c: -c[1]):
        if all(abs(i - c) >= min_gap for c in cuts):
            cuts.append(i)
    return sorted(cuts), d


def pyscenedetect_cuts(video: Path, fps: float, min_gap_s: float = 0.3) -> list[int]:
    from scenedetect import AdaptiveDetector, SceneManager, open_video

    stream = open_video(str(video), backend="opencv")
    manager = SceneManager()
    manager.add_detector(AdaptiveDetector(min_scene_len=max(1, int(round(min_gap_s * fps))), luma_only=_greyish(video)))
    manager.detect_scenes(stream, show_progress=False)
    return [int(getattr(start, "frame_num", None) or start.get_frames()) for start, _ in manager.get_scene_list()[1:]]


def _greyish(video: Path, samples: int = 8) -> bool:
    """Grey white-box renders carry the cut in luma only; hue/saturation deltas would dilute it."""
    import cv2

    cap = cv2.VideoCapture(str(video))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    sats = []
    for k in range(samples):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(k * total / samples))
        ok, frame = cap.read()
        if ok:
            sats.append(float(cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)[..., 1].mean()))
    cap.release()
    return bool(sats) and float(np.median(sats)) < 25.0


def find_cuts(video: Path | None, grays: np.ndarray, fps: float, backend: str | None = None) -> tuple[list[int], np.ndarray, dict[str, Any]]:
    """Cuts with the configured backend (automatic fallback); the grey-difference signal is always returned."""
    from ..vision import resolve

    choice = resolve("cuts", backend)
    info: dict[str, Any] = {"backend": choice.used, "requested": choice.requested, "method": METHODS[choice.used]}
    if choice.skipped:
        info["fallback_reasons"] = choice.skipped
    builtin, diffs = detect_cuts(grays, fps)
    if choice.used == "builtin" or video is None:
        return builtin, diffs, info
    try:
        if choice.used == "transnetv2":
            from ..vision import transnet

            found, pred = transnet.detect(Path(video))
            info["transnetv2_peak"] = [round(float(pred[c - 1]), 3) for c in found if c > 0]
        else:
            found = pyscenedetect_cuts(Path(video), fps)
    except Exception as exc:  # noqa: BLE001 - optional backend, fall back to the built-in detector
        info.update(backend="builtin", method=METHODS["builtin"], error=f"{choice.used}: {type(exc).__name__}: {exc}")
        return builtin, diffs, info
    found = sorted(c for c in set(found) if 0 < c < len(grays))
    info["builtin_agrees"] = sorted(found) == sorted(builtin)
    return found, diffs, info


def shot_ranges(cuts: list[int], total: int) -> list[tuple[int, int]]:
    """Inclusive 0-based frame ranges per shot."""
    bounds = [0, *cuts, total]
    return [(a, b - 1) for a, b in zip(bounds, bounds[1:]) if b > a]
