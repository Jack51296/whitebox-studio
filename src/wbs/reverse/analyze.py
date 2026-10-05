"""Stage 0–1 of the reverse pipeline: probe, frame sampling, cuts, background flow, occupancy, lines."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import cv2

from ..jsonio import write_json
from ..media.ffmpeg import probe
from . import cuts as C
from . import lines as L
from . import occupancy as O
from .flow import shot_flow
from .subject import shot_samples
from .video import burst_indices, decode, save_png, uniform_indices


def analyze(video: Path, out_dir: Path, analysis_width: int = 320, occupancy_hz: float = 4.0, *,
            cuts: str | None = None, subject_backend: str | None = None, subject: str = "person",
            prompt: str | None = None) -> dict[str, Any]:
    """``cuts`` / ``subject_backend`` override ``vision.cuts`` / ``vision.subject`` (fallback is automatic)."""
    started = time.time()
    info = probe(video, count_frames=True)
    total = int(info["frames"])
    uniform = uniform_indices(total, 13)
    bursts = burst_indices(total, 6)
    wanted = set(uniform) | {i for idx in bursts.values() for i in idx}
    decoded = decode(video, analysis_width, keep=set())
    grays = decoded.grays
    total = len(grays)
    fps = info["fps"] or decoded.fps

    cut_list, diffs, cut_info = C.find_cuts(video, grays, fps, cuts)
    ranges = C.shot_ranges(cut_list, total)
    step = max(1, int(round(fps / occupancy_hz)))
    occupancies, subject_info = shot_samples(video, grays, ranges, step, backend=subject_backend, subject=subject,
                                             prompt=prompt, mask_dir=out_dir / "masks")
    shots = []
    for n, (start, end) in enumerate(ranges, 1):
        occ = occupancies[n - 1]
        boxes = {i: s["bbox"] for i, s in occ.items()}
        masks = {i: s["mask_file"] for i, s in occ.items() if s.get("mask_file")}
        dense, segs = {}, {}
        for i in range(start, end + 1):
            near = min(boxes, key=lambda k: abs(k - i)) if boxes else None
            if near is not None and abs(near - i) <= step:
                dense[i] = boxes[near]
                if near in masks:
                    segs[i] = near
        loaded = {k: cv2.imread(masks[k], cv2.IMREAD_GRAYSCALE) for k in set(segs.values())}
        flow = shot_flow(grays, start, end, dense, {i: loaded[k] for i, k in segs.items() if loaded[k] is not None})
        mid = (start + end) // 2
        shots.append({
            "id": f"S{n:02}", "start_frame": start, "end_frame": end,
            "start_s": round(start / fps, 4), "end_s": round((end + 1) / fps, 4),
            "first_mid_last_frames": [start, mid, end],
            "flow": flow, "occupancy_samples": {str(k): v for k, v in sorted(occ.items())},
            "occupancy": O.first_mid_last(occ, start, end),
            "lines": {"first": L.structure_lines(grays[start]), "mid": L.structure_lines(grays[mid]),
                      "last": L.structure_lines(grays[end])},
        })
    wanted |= {i for s in shots for i in s["first_mid_last_frames"]}
    kept = decode(video, analysis_width, keep=wanted).kept
    frames_dir = out_dir / "frames"
    saved: dict[str, list[dict[str, Any]]] = {"uniform": [], "bursts": [], "shots": []}
    for k, i in enumerate(uniform):
        if i in kept:
            path = save_png(kept[i], frames_dir / f"uniform_{k + 1:02}_f{i + 1:04}.png")
            saved["uniform"].append({"frame": i + 1, "time_s": round(i / fps, 4), "file": path.relative_to(out_dir).as_posix()})
    for name, idx in bursts.items():
        for i in idx:
            if i in kept:
                path = save_png(kept[i], frames_dir / f"burst_{name}_f{i + 1:04}.png")
                saved["bursts"].append({"group": name, "frame": i + 1, "file": path.relative_to(out_dir).as_posix()})
    for s in shots:
        files = {}
        for role, i in zip(("first", "mid", "last"), s["first_mid_last_frames"]):
            if i in kept:
                path = save_png(kept[i], frames_dir / f"shot_{s['id']}_{role}.png")
                files[role] = path.relative_to(out_dir).as_posix()
        s["frame_files"] = files
        saved["shots"].append({"shot": s["id"], **files})

    doc = {
        "schema": "wbs.reverse.analysis/1.1",
        "video": {"file": Path(video).name, "width": info["width"], "height": info["height"], "fps": fps,
                  "fps_str": info["fps_str"], "frames": total, "duration_s": round(total / fps, 4),
                  "audio_streams": info["audio_streams"]},
        "analysis_width": analysis_width,
        "cuts": {"frames_1based": [c + 1 for c in cut_list], "times_s": [round(c / fps, 4) for c in cut_list],
                 "method": cut_info["method"], "detector": cut_info,
                 "diff_signal": [round(float(v), 3) for v in diffs]},
        "subject_detection": subject_info,
        "shots": shots, "frames": saved,
        "not_run": {"action_event_table": "需要看图理解动作（agent/人工）；离线规则分析不做动作计数"},
        "seconds": round(time.time() - started, 2),
    }
    write_json(out_dir / "analysis.json", doc)
    return doc
