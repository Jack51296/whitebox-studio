"""Measurement reports read from scene.json kinematics and the Blender per-frame audit."""

from __future__ import annotations

import math
import statistics
from pathlib import Path
from typing import Any

import numpy as np

from ..blender import kinematics as K
from ..config import DynamicGate, Framing
from ..reverse.cuts import find_cuts
from ..reverse.video import decode
from .dynamic import motion_issues, shot_subject


def editing_check(scene: dict, video: Path | None, samples: dict | None = None) -> dict[str, Any]:
    """Descriptive timing (team 前置检查.editing) + where the cuts actually happen.

    Authority: the camera Blender actually rendered each frame with (audit ``active_camera``).
    Picture-based cut detection is recorded as supporting evidence; its misses/extras are warnings,
    because similar neighbouring shots can hide a cut and fast motion can look like one.
    """
    lengths = [s["end_s"] - s["start_s"] for s in scene["shots"]]
    fps = int(scene["fps"])
    planned = [int(round(s["start_s"] * fps)) + 1 for s in scene["shots"][1:]]
    report: dict[str, Any] = {
        "physical_shots": len(lengths), "cut_boundaries": len(lengths) - 1,
        "minimum_seconds": round(min(lengths), 4), "maximum_seconds": round(max(lengths), 4),
        "mean_seconds": round(statistics.mean(lengths), 4), "median_seconds": round(statistics.median(lengths), 4),
        "shots_below_one_second": sum(v < 1.0 for v in lengths),
        "planned_cut_frames_1based": planned,
        "scope": "Descriptive timing + actual camera switches; no readability or tension score",
    }
    rows = (samples or {}).get("samples") or []
    if rows and all("active_camera" in r for r in rows):
        switches = [b["frame"] for a, b in zip(rows, rows[1:]) if a["active_camera"] != b["active_camera"]]
        expected = {f"CAMERA_{s['id']}" for s in scene["shots"]}
        wrong_camera = [r["frame"] for r in rows if r["active_camera"] != f"CAMERA_{r['shot']}"]
        report["rendered_camera_switch_frames_1based"] = switches
        report["frames_with_unexpected_camera"] = wrong_camera[:30]
        ok = switches == planned and not wrong_camera and {r["active_camera"] for r in rows} <= expected
        report.update(status="passed" if ok else "failed", basis="blender_active_camera")
    if video is not None and Path(video).exists():
        grays = decode(video, 320, keep=set()).grays
        found, _, method = find_cuts(Path(video), grays, fps)
        detected = [c + 1 for c in found]
        report["visual_detection"] = {
            "detected_cut_frames_1based": detected, "detector": method,
            "missed_planned_cuts": [p for p in planned if all(abs(p - c) > 1 for c in detected)],
            "unplanned_visual_cuts": [c for c in detected if all(abs(p - c) > 1 for p in planned)],
            "note": "画面检测仅作辅助：相邻两镜很像时会漏报，剧烈运动可能误报；unplanned_visual_cuts 需人工看前后帧（可能是闪跳）"}
        if "status" not in report:
            vd = report["visual_detection"]
            report.update(status="passed" if not vd["missed_planned_cuts"] and not vd["unplanned_visual_cuts"] else "failed",
                          basis="visual_detection_only")
    if "status" not in report:
        report.update(status="not_run", reason="no audit samples and no video")
    return report


def framing_report(scene: dict, samples: dict, cfg: Framing | None = None) -> dict[str, Any]:
    """景别对照 (wide/close/ratio) + per-shot subject visibility from the Blender audit (in frame and not hidden)."""
    cfg = cfg or Framing()
    rows = samples["samples"]
    actors = scene.get("actors", [])
    if not actors:
        return {"status": "not_applicable", "reason": "no subject in scene"}
    main = actors[0]["id"]

    def seen(row: dict, subject: str = main) -> bool:
        a = row["actors"].get(subject, {})
        return bool(a.get("visible")) and a.get("occluded_fraction", 0.0) <= 1 - cfg.min_unoccluded

    visible = [r for r in rows if seen(r)]
    pov = {s["id"] for s in scene["shots"] if s.get("move") == "pov"}

    def pick(row: dict | None) -> dict[str, Any] | None:
        if row is None:
            return None
        a = row["actors"][main]
        return {"edit": row["time"], "frame": row["frame"], "shot": row["shot"], "subject": main,
                "height_fraction": round(a["bbox"][3] - a["bbox"][1], 5), "in_frame": True, "center": a["center"],
                "camera_distance": a["distance_m"], "partial": a.get("partial", False)}

    measured = [r for r in visible if r["shot"] not in pov]
    wide = min(measured, key=lambda r: r["actors"][main]["bbox"][3] - r["actors"][main]["bbox"][1], default=None)
    close = max(measured, key=lambda r: r["actors"][main]["bbox"][3] - r["actors"][main]["bbox"][1], default=None)
    w, c = pick(wide), pick(close)
    ratio = (c["height_fraction"] / w["height_fraction"]) if w and c and w["height_fraction"] > 0 else None
    shots = []
    worst = 1.0
    for shot in scene["shots"]:
        srows = [r for r in rows if r["shot"] == shot["id"]]
        if not srows:
            continue
        subject = shot_subject(scene, shot)["id"]
        rate = sum(seen(r, subject) for r in srows) / len(srows)
        longest, run = 0, 0
        for r in srows:
            run = 0 if seen(r, subject) else run + 1
            longest = max(longest, run)
        in_frame = [r["actors"][subject] for r in srows if r["actors"].get(subject, {}).get("visible")]
        heights = [a["bbox"][3] - a["bbox"][1] for a in in_frame]
        sizes = [max(a["bbox"][3] - a["bbox"][1], a["bbox"][2] - a["bbox"][0]) for a in in_frame]
        entry = {"shot": shot["id"], "subject": subject, "frames": len(srows), "main_visible_rate": round(rate, 4),
                 "longest_invisible_run_frames": longest, "pov": shot["id"] in pov,
                 "occluded_frames": sum(1 for a in in_frame if a.get("occluded_fraction", 0.0) > 1 - cfg.min_unoccluded),
                 "height_fraction_min": round(min(heights), 4) if heights else None,
                 "height_fraction_max": round(max(heights), 4) if heights else None,
                 "size_median": round(statistics.median(sizes), 4) if sizes else None}
        shots.append(entry)
        if shot["id"] not in pov:
            worst = min(worst, rate)
    status = "passed" if worst >= cfg.min_visible_rate else "failed"
    return {"wide": w, "close": c, "ratio": round(ratio, 4) if ratio else None, "status": status,
            "rule": f"main subject in frame and ≥{cfg.min_unoccluded:.0%} unoccluded in ≥{cfg.min_visible_rate:.0%} "
                    "of frames of every non-POV shot", "shots": shots}


def speed_report(scene: dict, hz: int = 96, gate: DynamicGate | None = None) -> dict[str, Any]:
    ev = K.SceneEvaluator(scene)
    duration = float(scene["duration_s"])
    n = max(2, int(duration * hz))
    edit_t = np.linspace(0.0, duration, n)
    values: dict[str, dict[str, Any]] = {"source": {}, "edit": {}}
    for actor in scene.get("actors", []):
        track = ev.tracks[actor["id"]]
        src_end = ev.time_map.source_end
        src_t = np.linspace(0.0, src_end, n)
        src_pos = np.array([track.at(float(t)) for t in src_t])
        edit_pos = np.array([ev.actor_state(actor["id"], float(t))[0] for t in edit_t])
        for basis, pos, times in (("source", src_pos, src_t), ("edit", edit_pos, edit_t)):
            step = np.linalg.norm(np.diff(pos, axis=0), axis=1)
            dt = float(times[1] - times[0])
            speed = step / dt if dt > 0 else step * 0
            values[basis][actor["id"]] = {"peak_mps": round(float(speed.max(initial=0)), 4),
                                         "mean_mps": round(float(step.sum() / max(times[-1] - times[0], 1e-9)), 4),
                                         "distance_m": round(float(step.sum()), 4)}
    _, peaks = motion_issues(scene, gate or DynamicGate(), ev)
    return {"time_basis_separated": scene.get("time_map") is not None, "values": values,
            "motion_peaks_source": {aid: {k: v for k, v in p.items() if k.startswith("peak_")} | {"limits": p["limits"]}
                                    for aid, p in peaks.items()},
            "scope": "数值测量，不替代主观速度感或打斗强度验收"}


def orientation_check(scene: dict, hz: int = 96, gate: DynamicGate | None = None,
                      max_lag_deg: float = 30.0) -> dict[str, Any]:
    """Heading rate per actor kind; vehicles must also point where they move (a lagging heading reads as sliding)."""
    gate = gate or DynamicGate()
    ev = K.SceneEvaluator(scene, yaw_rate_hz=hz)
    worst: dict[str, float] = {}
    limits: dict[str, float] = {}
    lag: dict[str, float] = {}
    bad: dict[str, str] = {}
    for actor in scene.get("actors", []):
        aid = actor["id"]
        grid, yaws = ev._yaw[aid]
        rates = [abs(b - a) / max(t1 - t0, 1e-9) for a, b, t0, t1 in zip(yaws, yaws[1:], grid, grid[1:])]
        worst[aid] = round(max(rates, default=0.0), 3)
        kind = actor.get("kind", "pawn")
        limits[aid] = float(actor.get("max_yaw_rate_dps") or gate.limits(kind).yaw_rate)
        if worst[aid] > limits[aid] + 1e-6:
            bad[aid] = f"yaw rate {worst[aid]:.0f} > {limits[aid]:.0f} °/s"
        if kind == "vehicle" and not actor.get("yaw_keys"):
            track, largest = ev.tracks[aid], 0.0
            for t, yaw in zip(grid, yaws):
                vx, vy, _ = track.velocity(t, dt=0.05)
                if math.hypot(vx, vy) > 2.0:
                    largest = max(largest, abs((K.heading_deg(vx, vy) - yaw + 180.0) % 360.0 - 180.0))
            lag[aid] = round(largest, 2)
            if largest > max_lag_deg:
                bad.setdefault(aid, f"heading lags motion by {largest:.0f}° (reads as sliding)")
    return {"status": "failed" if bad else ("passed" if worst else "not_applicable"), "sample_hz": hz,
            "max_yaw_rate_dps": worst, "limit_dps": limits, "vehicle_heading_lag_deg": lag, "problems": bad,
            "rule": "heading follows motion, holds while stationary, root yaw rate limited per actor kind"}


def consistency_report(scene: dict, samples: dict) -> dict[str, Any]:
    """Blender-built transforms vs scene.json evaluation (proves the build matches the data)."""
    ev = K.SceneEvaluator(scene)
    actor_dev = cam_dev = 0.0
    shake = 0.0
    for shot in scene["shots"]:
        for layer in shot["camera"].get("responses") or []:
            shake = max(shake, math.sqrt(sum(v * v for v in layer.get("translation_m", [0, 0, 0]))))
    for row in samples["samples"]:
        t = ev.frame_time(row["frame"])
        for aid, a in row["actors"].items():
            actor_dev = max(actor_dev, math.dist(a["pos"], ev.actor_state(aid, t)[0]))
        cam_dev = max(cam_dev, math.dist(row["camera"]["pos"], ev.camera_state(t)["pos"]))
    cam_tol = 0.01 + shake
    ok = actor_dev <= 0.01 and cam_dev <= cam_tol
    return {"status": "passed" if ok else "failed", "frames": len(samples["samples"]),
            "max_actor_deviation_m": round(actor_dev, 6), "max_camera_deviation_m": round(cam_dev, 6),
            "tolerance_m": {"actor": 0.01, "camera": round(cam_tol, 4)},
            "blender_version": samples.get("blender_version")}
