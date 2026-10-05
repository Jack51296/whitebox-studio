"""Background optical flow per shot ([D2]: 遮蔽主体 + RANSAC → 缩放/位移/滚转曲线, 方向由实测符号定).

Signs (image coordinates, x right, y down): ``dx > 0`` means scene content moved right between frames
(camera yawed left); ``dy > 0`` content moved down (camera tilted up); ``scale > 1`` content grew
(camera moved in / zoomed in); ``roll_deg > 0`` content rotated counter-clockwise on screen.
"""

from __future__ import annotations

import math
from typing import Any

import cv2
import numpy as np


def pair_motion(prev: np.ndarray, cur: np.ndarray, mask: np.ndarray | None = None) -> dict[str, Any]:
    h, w = prev.shape
    pts = cv2.goodFeaturesToTrack(prev, maxCorners=400, qualityLevel=0.01, minDistance=6, mask=mask)
    if pts is None or len(pts) < 12:
        return {"ok": False, "reason": "few_features"}
    nxt, status, _ = cv2.calcOpticalFlowPyrLK(prev, cur, pts, None, winSize=(21, 21), maxLevel=3)
    good = status.ravel() == 1
    p0, p1 = pts[good].reshape(-1, 2), nxt[good].reshape(-1, 2)
    if len(p0) < 12:
        return {"ok": False, "reason": "few_tracks"}
    m, inliers = cv2.estimateAffinePartial2D(p0, p1, method=cv2.RANSAC, ransacReprojThreshold=2.0)
    if m is None:
        return {"ok": False, "reason": "ransac_failed"}
    a, b = float(m[0, 0]), float(m[1, 0])
    center = np.array([w / 2, h / 2])
    moved = m[:, :2] @ center + m[:, 2]
    return {"ok": True, "scale": math.hypot(a, b), "roll_deg": -math.degrees(math.atan2(b, a)),
            "dx": float((moved[0] - center[0]) / w), "dy": float((moved[1] - center[1]) / h),
            "inlier_ratio": float(inliers.sum() / len(inliers)) if inliers is not None else 0.0,
            "matrix": [[round(float(v), 6) for v in row] for row in m]}


def subject_mask(shape: tuple[int, int], bbox: list[float] | None, grow: float = 0.15) -> np.ndarray | None:
    """Mask (255 = usable background) excluding a normalised subject box [x0, y0, x1, y1]."""
    if bbox is None:
        return None
    h, w = shape
    x0, y0, x1, y1 = bbox
    gx, gy = (x1 - x0) * grow, (y1 - y0) * grow
    mask = np.full((h, w), 255, np.uint8)
    mask[max(0, int((y0 - gy) * h)):min(h, int(math.ceil((y1 + gy) * h))),
         max(0, int((x0 - gx) * w)):min(w, int(math.ceil((x1 + gx) * w)))] = 0
    return mask


def mask_from_segmentation(shape: tuple[int, int], segmentation: np.ndarray, grow_px: int = 5) -> np.ndarray:
    """Background mask (255 = usable) from a subject segmentation of any size (non-zero = subject)."""
    seg = cv2.resize((segmentation > 0).astype(np.uint8), (shape[1], shape[0]), interpolation=cv2.INTER_NEAREST)
    seg = cv2.dilate(seg, np.ones((2 * grow_px + 1, 2 * grow_px + 1), np.uint8))
    return np.where(seg > 0, 0, 255).astype(np.uint8)


def shot_flow(grays: np.ndarray, start: int, end: int, boxes: dict[int, list[float]] | None = None,
              segmentations: dict[int, np.ndarray] | None = None) -> dict[str, Any]:
    """Per-pair motion within [start, end] (inclusive) and cumulative totals.

    The subject is excluded from the background estimate by its segmentation when available
    (SAM masks), otherwise by its box grown 15 %.
    """
    per, zoom, pan_x, pan_y, roll, ok = [], 1.0, 0.0, 0.0, 0.0, 0
    boxes, segmentations = boxes or {}, segmentations or {}
    for i in range(start + 1, end + 1):
        seg = segmentations.get(i)
        if seg is None:
            seg = segmentations.get(i - 1)
        if seg is not None:
            mask = mask_from_segmentation(grays[i].shape, seg)
        else:
            mask = subject_mask(grays[i].shape, boxes.get(i) or boxes.get(i - 1))
        motion = pair_motion(grays[i - 1], grays[i], mask)
        motion["frame"] = i
        per.append(motion)
        if motion["ok"]:
            ok += 1
            zoom *= motion["scale"]
            pan_x += motion["dx"]
            pan_y += motion["dy"]
            roll += motion["roll_deg"]
    return {"pairs": len(per), "ok_pairs": ok, "zoom_total": round(zoom, 5), "pan_x_total": round(pan_x, 5),
            "pan_y_total": round(pan_y, 5), "roll_total_deg": round(roll, 4),
            "per_frame": [{k: (round(v, 6) if isinstance(v, float) else v) for k, v in p.items() if k != "matrix"}
                          for p in per]}
