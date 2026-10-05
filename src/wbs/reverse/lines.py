"""Dominant structure lines ([D2] ★结构线标注: 峡谷/地平线等主导线, 屏幕端点 + 倾角)."""

from __future__ import annotations

import math
from typing import Any

import cv2
import numpy as np


def structure_lines(gray: np.ndarray, max_lines: int = 4, min_len_frac: float = 0.2,
                    cluster_deg: float = 6.0) -> list[dict[str, Any]]:
    """Longest line per angle cluster. Angle in degrees, 0 = horizontal, positive = rising to the right."""
    h, w = gray.shape
    edges = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 50, 150)
    found = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=max(30, int(0.08 * w)),
                            minLineLength=int(min_len_frac * w), maxLineGap=max(4, int(0.02 * w)))
    if found is None:
        return []
    lines = []
    for x0, y0, x1, y1 in np.asarray(found).reshape(-1, 4):
        if x1 < x0:
            x0, y0, x1, y1 = x1, y1, x0, y0
        angle = math.degrees(math.atan2(-(y1 - y0), (x1 - x0)))
        if angle <= -90:
            angle += 180
        if angle > 90:
            angle -= 180
        lines.append({"p0": [round(x0 / w, 4), round(y0 / h, 4)], "p1": [round(x1 / w, 4), round(y1 / h, 4)],
                      "angle_deg": round(angle, 2), "length": round(math.hypot((x1 - x0) / w, (y1 - y0) / h), 4)})
    lines.sort(key=lambda item: -item["length"])
    kept: list[dict[str, Any]] = []
    for line in lines:
        if all(angle_diff(line["angle_deg"], k["angle_deg"]) > cluster_deg for k in kept):
            kept.append(line)
        if len(kept) >= max_lines:
            break
    return kept


def angle_diff(a: float, b: float) -> float:
    """Smallest difference between two undirected line angles (degrees, 0..90)."""
    d = abs(a - b) % 180
    return min(d, 180 - d)


def match_lines(source: list[dict[str, Any]], render: list[dict[str, Any]], top: int = 3) -> dict[str, Any]:
    if not source:
        return {"status": "not_applicable", "reason": "no dominant lines in source frame", "max_diff_deg": None}
    if not render:
        return {"status": "failed", "reason": "no dominant lines in rendered frame", "max_diff_deg": None}
    diffs = [min(angle_diff(s["angle_deg"], r["angle_deg"]) for r in render) for s in source[:top]]
    return {"max_diff_deg": round(max(diffs), 2), "diffs_deg": [round(d, 2) for d in diffs]}
