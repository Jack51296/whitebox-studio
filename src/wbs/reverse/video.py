"""Single-pass decoding: small grey frames for analysis, full-resolution copies of selected frames."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from ..errors import WbsError


@dataclass
class Decoded:
    grays: np.ndarray            # (T, h, w) uint8 at analysis width
    fps: float
    size: tuple[int, int]        # original (width, height)
    kept: dict[int, np.ndarray]  # frame index (0-based) → full-resolution BGR


def uniform_indices(total: int, count: int = 13) -> list[int]:
    if total <= 0:
        return []
    if total <= count:
        return list(range(total))
    return sorted({round(i * (total - 1) / (count - 1)) for i in range(count)})


def burst_indices(total: int, length: int = 6) -> dict[str, list[int]]:
    mid = total // 2
    clamp = lambda xs: [x for x in xs if 0 <= x < total]  # noqa: E731
    return {"head": clamp(list(range(length))), "middle": clamp(list(range(mid - length // 2, mid - length // 2 + length))),
            "tail": clamp(list(range(total - length, total)))}


def decode(video: Path, analysis_width: int = 320, keep: set[int] | None = None) -> Decoded:
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise WbsError(f"cannot open video {video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    grays, kept = [], {}
    size = (0, 0)
    index = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            h, w = frame.shape[:2]
            size = (w, h)
            if keep is None or index in keep:
                kept[index] = frame
            small = cv2.resize(frame, (analysis_width, max(2, round(h * analysis_width / w))), interpolation=cv2.INTER_AREA)
            grays.append(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
            index += 1
    finally:
        cap.release()
    if not grays:
        raise WbsError(f"{video}: no decodable frames")
    return Decoded(grays=np.stack(grays), fps=float(fps), size=size, kept=kept)


def save_png(frame: np.ndarray, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(".png", frame)
    if not ok:
        raise WbsError(f"PNG encode failed for {path}")
    path.write_bytes(buf.tobytes())
    return path


def load_gray(path: Path, width: int = 320) -> np.ndarray:
    data = np.frombuffer(Path(path).read_bytes(), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise WbsError(f"cannot read image {path}")
    h, w = img.shape
    return cv2.resize(img, (width, max(2, round(h * width / w))), interpolation=cv2.INTER_AREA)
