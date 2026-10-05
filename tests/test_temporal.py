from __future__ import annotations

import cv2
import numpy as np
from conftest import requires_ffmpeg

from wbs.config import VideoEncode
from wbs.media.ffmpeg import encode_frames
from wbs.qc.temporal import temporal_evidence


def _clip(tmp_path, name, black=None, flash=None, cut=24, n=48):
    frames = tmp_path / name
    frames.mkdir()
    for i in range(n):
        img = np.full((90, 160, 3), 90 if i < cut else 170, np.uint8)
        cv2.circle(img, (20 + i * 2, 45), 10, (40, 40, 200), -1)
        if i == black:
            img[:] = 0
        if i == flash:
            img[:] = 255
        cv2.imwrite(str(frames / f"f{i + 1:04d}.png"), img)
    return encode_frames(frames, tmp_path / f"{name}.mp4", 24, VideoEncode(crf=12))


@requires_ffmpeg
def test_temporal_evidence_flags_black_and_flash_frames_not_planned_cuts(tmp_path):
    clean = temporal_evidence(_clip(tmp_path, "clean"), planned_cuts=[24])
    assert clean["status"] == "passed" and not clean["unexpected_spikes_1based"] and not clean["black_frames_1based"]
    assert clean["vbench"]["status"] in ("not_run", "run", "failed")
    bad = temporal_evidence(_clip(tmp_path, "bad", black=10, flash=36), planned_cuts=[24])
    assert bad["status"] == "failed"
    assert bad["black_frames_1based"] == [11]
    assert bad["single_frame_flashes_1based"] == [11, 37] and not bad["unexpected_spikes_1based"]
