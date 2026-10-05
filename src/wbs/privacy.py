"""Face anonymisation for real-video inputs before they go to any external model ([D7] 面部范围高斯模糊).

Detectors: OpenCV YuNet (tools/models, fetched by scripts/fetch_tools.ps1), optionally joined by CenterFace
(the MIT model shipped with deface; ``vision.faces: [yunet, centerface]``) — boxes from all detectors are
blurred (union) to reduce misses. OpenCV Haar cascades are used only when YuNet is unavailable.
Automated detection can miss small, occluded or strongly turned faces, so the result must be spot-checked
by a person before external upload. The report says so.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .config import VideoEncode
from .errors import WbsError
from .tools import HINTS, find_tool, require_tool


class FaceDetector:
    def __init__(self, width: int, height: int, min_size: int = 24, score: float = 0.6,
                 extra: list[str] | None = None) -> None:
        """``extra`` detectors (``centerface``) are added when available; missing ones are listed in ``skipped``."""
        self.min_size = min_size
        self.extra, self.skipped = [], {}
        for name in extra or []:
            if name == "centerface":
                from .vision import unavailable_reason
                from .vision.centerface import CenterFace

                reason = unavailable_reason("faces", "centerface")
                if reason is None:
                    self.extra.append(("centerface", CenterFace()))
                else:
                    self.skipped[name] = reason
        model = find_tool("face_model")
        if model and hasattr(cv2, "FaceDetectorYN"):
            self.kind = "yunet"
            self._yunet = cv2.FaceDetectorYN.create(model, "", (width, height), score, 0.3, 5000)
            return
        base = getattr(getattr(cv2, "data", None), "haarcascades", None)
        names = ["haarcascade_frontalface_default.xml", "haarcascade_profileface.xml"]
        paths = [Path(base) / n for n in names] if base else []
        if hasattr(cv2, "CascadeClassifier") and paths and all(p.exists() for p in paths):
            self.kind = "haar"
            self._haar = [cv2.CascadeClassifier(str(p)) for p in paths]
            return
        if self.extra:
            self.kind = "none"
            return
        raise WbsError(f"没有可用的人脸检测模型：{HINTS['face_model']}")

    @property
    def method(self) -> str:
        return "+".join([f"opencv-{self.kind}"] * (self.kind != "none") + [name for name, _ in self.extra])

    def detect(self, frame: np.ndarray) -> list[tuple[int, int, int, int]]:
        boxes = self._primary(frame)
        for _, detector in self.extra:
            boxes += [(x, y, w, h) for x, y, w, h, _ in detector.detect(frame) if w >= self.min_size and h >= self.min_size]
        return boxes

    def _primary(self, frame: np.ndarray) -> list[tuple[int, int, int, int]]:
        if self.kind == "none":
            return []
        if self.kind == "yunet":
            _, faces = self._yunet.detect(frame)
            if faces is None:
                return []
            return [(int(f[0]), int(f[1]), int(f[2]), int(f[3])) for f in faces
                    if f[2] >= self.min_size and f[3] >= self.min_size]
        gray = cv2.equalizeHist(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
        width = gray.shape[1]
        boxes: list[tuple[int, int, int, int]] = []
        for i, clf in enumerate(self._haar):
            variants = ((gray, False), (cv2.flip(gray, 1), True)) if i == 1 else ((gray, False),)
            for img, mirrored in variants:
                for (x, y, w, h) in clf.detectMultiScale(img, 1.1, 4, minSize=(self.min_size, self.min_size)):
                    boxes.append((width - x - w, y, w, h) if mirrored else (x, y, w, h))
        return boxes


def blur_faces(src: Path, dst: Path, encode: VideoEncode, *, expand: float = 0.3, min_size: int = 24,
               mode: str = "gaussian", detectors: list[str] | None = None) -> dict[str, Any]:
    """``detectors`` defaults to ``vision.faces``; YuNet is always the base, others are joined (union)."""
    if detectors is None:
        from .config import load_settings

        detectors = load_settings().vision.faces
    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        raise WbsError(f"cannot open {src}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    width, height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    dst.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [require_tool("ffmpeg"), "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}",
         "-r", f"{fps}", "-i", "-", "-c:v", encode.codec, "-crf", str(encode.crf), "-preset", encode.preset,
         "-pix_fmt", encode.pix_fmt, "-an", str(dst)], stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    detector = FaceDetector(width, height, min_size=min_size, extra=[d for d in detectors if d != "yunet"])
    frames = frames_with_faces = detections = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frames += 1
            boxes = detector.detect(frame)
            if boxes:
                frames_with_faces += 1
                detections += len(boxes)
            for (x, y, w, h) in boxes:
                dx, dy = int(w * expand), int(h * expand)
                x0, y0 = max(x - dx, 0), max(y - dy, 0)
                x1, y1 = min(x + w + dx, width), min(y + h + dy, height)
                roi = frame[y0:y1, x0:x1]
                if roi.size == 0:
                    continue
                if mode == "pixelate":
                    small = cv2.resize(roi, (max(1, (x1 - x0) // 16), max(1, (y1 - y0) // 16)))
                    frame[y0:y1, x0:x1] = cv2.resize(small, (x1 - x0, y1 - y0), interpolation=cv2.INTER_NEAREST)
                else:
                    k = max(31, ((x1 - x0) // 2) | 1)
                    frame[y0:y1, x0:x1] = cv2.GaussianBlur(roi, (k, k), 0)
            assert proc.stdin is not None
            proc.stdin.write(frame.tobytes())
    finally:
        cap.release()
        if proc.stdin:
            proc.stdin.close()
        stderr = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        code = proc.wait()
    if code != 0:
        raise WbsError(f"ffmpeg failed while writing {dst}: {stderr[-500:]}")
    return {"source": src.name, "output": dst.name, "frames": frames, "frames_with_faces": frames_with_faces,
            "detections": detections, "method": f"{detector.method}, {mode}", "detectors_skipped": detector.skipped,
            "manual_check_required": True,
            "note": "自动检测可能漏掉小脸、遮挡或大角度侧脸；外发前必须人工抽查"}
