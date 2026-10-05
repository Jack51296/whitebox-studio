"""CenterFace face detector (MIT; the ONNX model shipped with deface) through OpenCV DNN.

Decoding follows deface/centerface.py (ORB-HD/deface @09b670d, MIT). OpenCV DNN accepts the static-shape
model at any input size divisible by 32, so no ``onnx`` rewrite and no onnxruntime are needed.
"""

from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np

from .fetch import model_path

OUTPUTS = ["537", "538", "539", "540"]


def onnx_path() -> Path:
    return model_path("centerface") / "centerface.onnx"


class CenterFace:
    def __init__(self, path: Path | None = None, max_side: int = 1280) -> None:
        self.net = cv2.dnn.readNetFromONNX(str(path or onnx_path()))
        self.max_side = max_side

    def detect(self, bgr: np.ndarray, threshold: float = 0.5) -> list[tuple[int, int, int, int, float]]:
        """Face boxes (x, y, w, h, score) in the frame's pixel coordinates."""
        h, w = bgr.shape[:2]
        shrink = min(1.0, self.max_side / max(h, w))
        w_new, h_new = int(math.ceil(w * shrink / 32) * 32), int(math.ceil(h * shrink / 32) * 32)
        blob = cv2.dnn.blobFromImage(bgr, 1.0, (w_new, h_new), (0, 0, 0), swapRB=True, crop=False)
        self.net.setInput(blob)
        heatmap, scale, offset, _ = self.net.forward(OUTPUTS)
        heat = heatmap[0, 0]
        ys, xs = np.nonzero(heat > threshold)
        if len(ys) == 0:
            return []
        s0 = np.exp(scale[0, 0, ys, xs]) * 4
        s1 = np.exp(scale[0, 1, ys, xs]) * 4
        x1 = np.clip((xs + offset[0, 1, ys, xs] + 0.5) * 4 - s1 / 2, 0, w_new)
        y1 = np.clip((ys + offset[0, 0, ys, xs] + 0.5) * 4 - s0 / 2, 0, h_new)
        x2, y2 = np.minimum(x1 + s1, w_new), np.minimum(y1 + s0, h_new)
        scores = heat[ys, xs]
        rects = [[float(a), float(b), float(c - a), float(d - b)] for a, b, c, d in zip(x1, y1, x2, y2)]
        keep = cv2.dnn.NMSBoxes(rects, scores.astype(float).tolist(), threshold, 0.3)
        sx, sy = w / w_new, h / h_new
        out = []
        for i in np.array(keep).reshape(-1):
            x, y, bw, bh = rects[int(i)]
            out.append((int(x * sx), int(y * sy), int(bw * sx), int(bh * sy), float(scores[int(i)])))
        return out
