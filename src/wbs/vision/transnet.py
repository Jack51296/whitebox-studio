"""TransNetV2 shot-boundary detection (MIT, soCzech/TransNetV2) through onnxruntime.

Same windowing as the reference implementation: 48×27 RGB frames, windows of 100 frames advanced by 50,
25 frames of padding on each side, the middle 50 predictions of every window are kept.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from ..tools import require_tool
from .fetch import model_path


def onnx_path() -> Path:
    return model_path("transnetv2") / "transnetv2.onnx"


def small_frames(video: Path) -> np.ndarray:
    proc = subprocess.run([require_tool("ffmpeg"), "-v", "error", "-i", str(video), "-vf", "scale=48:27", "-pix_fmt", "rgb24",
                           "-f", "rawvideo", "pipe:"], capture_output=True, check=True)
    return np.frombuffer(proc.stdout, np.uint8).reshape(-1, 27, 48, 3)


def predict(frames: np.ndarray) -> np.ndarray:
    import onnxruntime as ort

    session = ort.InferenceSession(str(onnx_path()), providers=["CPUExecutionProvider"])
    n = len(frames)
    tail = 25 + 50 - (n % 50 if n % 50 else 50)
    padded = np.concatenate([np.repeat(frames[:1], 25, 0), frames, np.repeat(frames[-1:], tail, 0)])
    out = []
    for ptr in range(0, len(padded) - 99, 50):
        window = padded[ptr:ptr + 100][None]
        out.append(session.run(None, {"frames": window})[0][0, 25:75, 0])
    return np.concatenate(out)[:n]


def cuts_from_predictions(pred: np.ndarray, threshold: float = 0.5) -> list[int]:
    """First frame of every new shot: the frame after each run of transition frames."""
    hits = pred > threshold
    cuts = []
    for i in range(1, len(hits)):
        if hits[i - 1] and not hits[i]:
            cuts.append(i)
    return cuts


def detect(video: Path, threshold: float = 0.5) -> tuple[list[int], np.ndarray]:
    pred = predict(small_frames(video))
    return cuts_from_predictions(pred, threshold), pred
