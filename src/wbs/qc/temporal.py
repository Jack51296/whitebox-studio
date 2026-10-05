"""Automatic temporal evidence for the experience report (never replaces normal-speed viewing).

Built-in statistics on the decoded video: black frames, luminance flicker outside planned cuts, isolated
flash-like spikes that are not planned cuts, frozen runs. VBench (Apache-2.0) can add model-based scores
(subject consistency, motion smoothness, temporal flicker) when it is installed; otherwise it is reported
as not_run. Used for white-box renders and for V2V results alike.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

from ..reverse.cuts import detect_cuts
from ..reverse.video import decode

FLICKER_P95_MAX = 3.0
VBENCH_DIMENSIONS = ("subject_consistency", "motion_smoothness", "temporal_flicker")


def _runs(mask: np.ndarray, min_len: int) -> list[list[int]]:
    runs, start = [], None
    for i, v in enumerate(list(mask) + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start >= min_len:
                runs.append([start + 1, i])
            start = None
    return runs


def temporal_evidence(video: Path, planned_cuts: list[int] | None = None, fps: float = 24.0) -> dict[str, Any]:
    """``planned_cuts``: 0-based first frames of new shots (excluded from flicker / spike statistics)."""
    grays = decode(video, 320, keep=set()).grays
    luma = grays.reshape(len(grays), -1).astype(np.float32)
    mean, std = luma.mean(1), luma.std(1)
    planned = set(planned_cuts or [])
    near_cut = np.zeros(len(grays), bool)
    for c in planned:
        near_cut[max(0, c - 1):c + 2] = True
    black = np.nonzero((mean < 16) & (std < 4))[0]
    k = 7
    padded = np.pad(mean, k // 2, mode="edge")
    smooth = np.array([np.median(padded[i:i + k]) for i in range(len(mean))])
    residual = np.abs(mean - smooth)[~near_cut]
    p95 = float(np.percentile(residual, 95)) if len(residual) else 0.0
    spikes, diffs = detect_cuts(grays, fps)
    unexpected = [c + 1 for c in spikes if all(abs(c - p) > 1 for p in planned)]
    g = grays.astype(np.int16)
    flashes: list[int] = []
    if len(g) >= 3:
        before = np.abs(g[1:-1] - g[:-2]).mean((1, 2))
        after = np.abs(g[2:] - g[1:-1]).mean((1, 2))
        skip = np.abs(g[2:] - g[:-2]).mean((1, 2))
        low = np.minimum(before, after)
        flashes = [int(i) + 2 for i in np.nonzero((low > 12) & (skip < 0.35 * low))[0]]
    frozen = _runs(np.r_[False, diffs[1:] < 0.05], max(6, int(fps // 2)))
    problems = []
    if len(black):
        problems.append(f"{len(black)} 帧近乎全黑")
    if p95 > FLICKER_P95_MAX:
        problems.append(f"亮度闪烁 p95={p95:.2f} 超过 {FLICKER_P95_MAX}")
    if flashes:
        problems.append(f"单帧闪帧/掉帧 {flashes[:10]}")
    if unexpected:
        problems.append(f"非计划的切点 {unexpected[:10]}")
    return {"schema": "wbs.qc.temporal/1.0", "status": "failed" if problems else "passed", "frames": len(grays),
            "black_frames_1based": [int(i) + 1 for i in black[:50]], "luma_flicker_p95": round(p95, 3),
            "luma_flicker_max": round(float(residual.max()) if len(residual) else 0.0, 3),
            "single_frame_flashes_1based": flashes[:50], "unexpected_spikes_1based": unexpected,
            "frozen_runs_1based": frozen[:20], "problems": problems, "vbench": vbench_evidence(video),
            "scope": "时域统计证据；闪烁/突变阈值为经验值，冻结段仅供参考（静止镜头会被列出）；正常速度观感仍需人工"}


def vbench_evidence(video: Path, dimensions: tuple[str, ...] = VBENCH_DIMENSIONS) -> dict[str, Any]:
    exe = shutil.which("vbench")
    if exe is None:
        return {"status": "not_run", "reason": "未安装 VBench（Apache-2.0，可选；需要 torch 与其评测模型）"}
    scores, errors = {}, {}
    with tempfile.TemporaryDirectory(prefix="wbs_vbench_") as tmp:
        clip = Path(tmp) / "videos" / Path(video).name
        clip.parent.mkdir()
        shutil.copyfile(video, clip)
        for dim in dimensions:
            out = Path(tmp) / dim
            proc = subprocess.run([exe, "evaluate", "--dimension", dim, "--videos_path", str(clip.parent),
                                   "--mode=custom_input", "--output_path", str(out)],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600)
            results = sorted(out.glob("*_eval_results.json")) if out.exists() else []
            if proc.returncode != 0 or not results:
                errors[dim] = (proc.stderr or proc.stdout or "no result file").strip().splitlines()[-1:]
                continue
            data = json.loads(results[-1].read_text(encoding="utf-8"))
            value = data.get(dim)
            scores[dim] = round(float(value[0] if isinstance(value, list) else value), 4)
    return {"status": "run" if scores else "failed", "scores": scores, "errors": errors or None}
