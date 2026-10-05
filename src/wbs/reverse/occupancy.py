"""Subject occupancy by motion saliency: residual after compensating background motion ([D2] 主体占幅).

Generic (no detector, no identity): the moving foreground left after warping the previous frame by
the estimated background motion. Returns normalised boxes [x0, y0, x1, y1] and area fractions.
"""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from .flow import pair_motion


def foreground_box(prev: np.ndarray, cur: np.ndarray, threshold: int = 18,
                   min_area: float = 0.002) -> dict[str, Any] | None:
    h, w = cur.shape
    motion = pair_motion(prev, cur)
    if motion["ok"]:
        m = np.array(motion["matrix"], dtype=np.float32)
        warped = cv2.warpAffine(prev, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    else:
        warped = prev
    diff = cv2.absdiff(cur, warped)
    diff = cv2.GaussianBlur(diff, (5, 5), 0)
    _, fg = cv2.threshold(diff, threshold, 255, cv2.THRESH_BINARY)
    border = max(2, int(0.02 * w))
    fg[:border, :] = fg[-border:, :] = 0
    fg[:, :border] = fg[:, -border:] = 0
    fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(fg)
    if count <= 1:
        return None

    def weight(s) -> float:
        touches = (s[cv2.CC_STAT_LEFT] <= border + 1) + (s[cv2.CC_STAT_TOP] <= border + 1) + \
            (s[cv2.CC_STAT_LEFT] + s[cv2.CC_STAT_WIDTH] >= w - border - 1) + \
            (s[cv2.CC_STAT_TOP] + s[cv2.CC_STAT_HEIGHT] >= h - border - 1)
        return s[cv2.CC_STAT_AREA] * (0.35 ** touches)

    comps = sorted((stats[i] for i in range(1, count)), key=lambda s: -weight(s))
    largest = weight(comps[0])
    if comps[0][cv2.CC_STAT_AREA] < min_area * w * h:
        return None
    keep = [s for s in comps if weight(s) >= 0.3 * largest]
    x0 = min(s[cv2.CC_STAT_LEFT] for s in keep)
    y0 = min(s[cv2.CC_STAT_TOP] for s in keep)
    x1 = max(s[cv2.CC_STAT_LEFT] + s[cv2.CC_STAT_WIDTH] for s in keep)
    y1 = max(s[cv2.CC_STAT_TOP] + s[cv2.CC_STAT_HEIGHT] for s in keep)
    box = [x0 / w, y0 / h, x1 / w, y1 / h]
    return {"bbox": [round(v, 4) for v in box], "area": round((box[2] - box[0]) * (box[3] - box[1]), 5),
            "center": [round((box[0] + box[2]) / 2, 4), round((box[1] + box[3]) / 2, 4)],
            "components": len(keep)}


def shot_occupancy(grays: np.ndarray, start: int, end: int, step: int) -> dict[int, dict[str, Any]]:
    """Foreground boxes sampled every ``step`` frames inside [start, end] (needs a previous frame in-shot)."""
    out: dict[int, dict[str, Any]] = {}
    gap = max(1, min(3, end - start))
    frames = list(range(start + gap, end + 1, step))
    if end not in frames and end - gap >= start:
        frames.append(end)
    for i in frames:
        box = foreground_box(grays[i - gap], grays[i])
        if box is not None:
            out[i] = box
    return out


def first_mid_last(samples: dict[int, dict[str, Any]], start: int, end: int) -> dict[str, dict[str, Any] | None]:
    if not samples:
        return {"first": None, "mid": None, "last": None}
    keys = sorted(samples)
    mid = (start + end) / 2

    def nearest(target: float) -> dict[str, Any]:
        k = min(keys, key=lambda x: abs(x - target))
        return {"frame": k, **samples[k]}

    return {"first": nearest(start), "mid": nearest(mid), "last": nearest(end)}
