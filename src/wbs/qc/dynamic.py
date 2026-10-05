"""Dynamic (clipping) gate: dense sampling of scene.json kinematics ([D2] 动态闸门 "穿模", team 制作数据检查).

Checks, at ``sample_hz`` over the whole edit timeline:
  camera ↔ block clearance, camera ↔ actor distance (a POV carrier is exempt for its own shot),
  actor ↔ block penetration (standing on top is support), actor ↔ actor clearance,
  per-frame jumps (teleports) of actors and cameras within a shot, and physical plausibility per actor
  kind on the source clock: speed, acceleration, braking, cornering (lateral) and heading rate.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any, Literal

import numpy as np

from .. import camera_language as CL
from ..blender import kinematics as K
from ..config import DynamicGate, Framing
from .visibility import occluders, subject_visibility

ACTOR_BLOCK_TOL_M = 0.05
CAMERA_JUMP_M_PER_FRAME = 3.0
MOTION_TYPES = ("actor_accel", "actor_brake", "actor_lateral", "actor_yaw_rate")
G = 9.81


def _moving_average(x: np.ndarray, width: int) -> np.ndarray:
    if width <= 1 or len(x) < 3:
        return x
    pad = width // 2
    padded = np.pad(x, [(pad, pad)] + [(0, 0)] * (x.ndim - 1), mode="edge")
    kernel = np.ones(width) / width
    return np.apply_along_axis(lambda c: np.convolve(c, kernel, mode="valid"), 0, padded)


def motion_metrics(pos: np.ndarray, dt: float, smooth_s: float = 0.1) -> dict[str, np.ndarray]:
    """Speed, tangential acceleration, lateral acceleration (m/s²) and heading rate (deg/s) of a sampled path."""
    width = max(1, int(round(smooth_s / dt))) | 1
    vel = _moving_average(np.gradient(pos, dt, axis=0), width)
    acc = _moving_average(np.gradient(vel, dt, axis=0), width)
    speed = np.linalg.norm(vel, axis=1)
    safe = np.maximum(speed, 1e-9)
    hspeed = np.hypot(vel[:, 0], vel[:, 1])
    heading = np.unwrap(np.arctan2(vel[:, 1], vel[:, 0]))
    return {"speed": speed, "hspeed": hspeed, "tangential": (vel * acc).sum(1) / safe,
            "lateral": np.linalg.norm(np.cross(vel, acc), axis=1) / safe,
            "yaw_rate": np.degrees(np.abs(np.gradient(heading, dt)))}


def _runs(mask: np.ndarray, min_len: int) -> list[tuple[int, int]]:
    """Index ranges [a, b) where ``mask`` holds for at least ``min_len`` consecutive samples."""
    out, start = [], None
    for i, flag in enumerate(list(mask) + [False]):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            if i - start >= min_len:
                out.append((start, i))
            start = None
    return out


def motion_issues(scene: dict, cfg: DynamicGate, ev: K.SceneEvaluator | None = None) -> tuple[list[dict], dict]:
    """Per-kind plausibility on the source clock; returns (issues, per-actor peaks)."""
    ev = ev or K.SceneEvaluator(scene)
    hz = cfg.sample_hz
    end = ev.time_map.source_end
    n = max(3, int(end * hz) + 1)
    src = np.linspace(0.0, end, n)
    dt = float(src[1] - src[0])
    hold = max(1, int(round(cfg.motion_hold_s / dt)))
    issues, peaks = [], {}
    for actor in scene.get("actors", []):
        actor_kind = actor.get("kind", "pawn")
        lim = cfg.limits(actor_kind)
        pos = np.array([ev.tracks[actor["id"]].at(float(t)) for t in src], dtype=float)
        m = motion_metrics(pos, dt, cfg.motion_smooth_s)
        moving = m["hspeed"] > cfg.motion_min_speed_mps
        steady = moving & np.roll(moving, 1) & np.roll(moving, -1)
        steady[[0, -1]] = False
        yaw_limit = float(actor.get("max_yaw_rate_dps") or lim.yaw_rate)
        checks = (("actor_accel", m["tangential"], lim.accel, np.ones(n, bool), "m/s²"),
                  ("actor_brake", -m["tangential"], lim.brake, np.ones(n, bool), "m/s²"),
                  ("actor_lateral", m["lateral"], lim.lateral, moving, "m/s²"),
                  ("actor_yaw_rate", m["yaw_rate"], yaw_limit, steady, "°/s"))
        peaks[actor["id"]] = {"kind": actor_kind, "limits": lim.model_dump() | {"yaw_rate": yaw_limit}}
        raw_speed = np.r_[np.linalg.norm(np.diff(pos, axis=0), axis=1) / dt, 0.0]
        peaks[actor["id"]]["peak_speed"] = round(float(raw_speed.max(initial=0.0)), 3)
        for a, b in _runs(raw_speed > lim.speed, 1):
            k = a + int(np.argmax(raw_speed[a:b]))
            t_edit = ev.time_map.edit(float(src[k])) if scene.get("time_map") else float(src[k])
            issues.append({"type": "actor_speed", "time_s": round(t_edit, 4), "frame": int(t_edit * ev.fps) + 1,
                           "shot": scene["shots"][ev.shot_index(t_edit)]["id"], "value": round(float(raw_speed[k]), 3),
                           "detail": f"actor {actor['id']} {raw_speed[k]:.1f} m/s > {lim.speed:g}"})
        for kind, values, limit, mask, unit in checks:
            valid = np.where(mask, values, 0.0)
            valid[[0, -1]] = 0.0
            peaks[actor["id"]][f"peak_{kind.removeprefix('actor_')}"] = round(float(valid.max(initial=0.0)), 3)
            for a, b in _runs(valid > limit, hold):
                k = a + int(np.argmax(valid[a:b]))
                t_edit = ev.time_map.edit(float(src[k])) if scene.get("time_map") else float(src[k])
                g = f"（{valid[k] / G:.1f} g）" if unit == "m/s²" else ""
                issues.append({"type": kind, "time_s": round(t_edit, 4), "frame": int(t_edit * ev.fps) + 1,
                               "shot": scene["shots"][ev.shot_index(t_edit)]["id"], "value": round(float(valid[k]), 3),
                               "detail": f"actor {actor['id']} ({actor_kind}) {valid[k]:.1f} {unit}{g} > {limit:g} "
                                         f"for {(b - a) * dt:.2f} s"})
    return issues, peaks


def _boxes(scene: dict) -> tuple[list[str], np.ndarray, np.ndarray]:
    ids, lo, hi = [], [], []
    for b in scene.get("blocks", []):
        if not b.get("collision", True):
            continue
        a, c = K.block_aabb(b)
        ids.append(b["id"])
        lo.append(a)
        hi.append(c)
    return ids, np.array(lo, dtype=float).reshape(-1, 3), np.array(hi, dtype=float).reshape(-1, 3)


def _point_box_dist(p: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    """Signed distance from points (T,3) to boxes (N,3): (T,N); negative inside."""
    d_out = np.maximum(np.maximum(lo[None] - p[:, None], 0.0), p[:, None] - hi[None])
    outside = np.sqrt((d_out ** 2).sum(-1))
    inside = np.minimum(p[:, None] - lo[None], hi[None] - p[:, None]).min(-1)
    return np.where(outside > 0, outside, -np.maximum(inside, 0.0))


def _cyl_box_pen(c: np.ndarray, r: float, h: float, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    """Horizontal penetration (T,N) of an upright cylinder into boxes; top support is not penetration."""
    bottom, top = c[:, 2:3], c[:, 2:3] + h
    vertical = (top > lo[None, :, 2]) & (bottom < hi[None, :, 2] - 0.05)
    nx = np.clip(c[:, None, 0], lo[None, :, 0], hi[None, :, 0])
    ny = np.clip(c[:, None, 1], lo[None, :, 1], hi[None, :, 1])
    dist = np.hypot(c[:, None, 0] - nx, c[:, None, 1] - ny)
    inner = np.minimum.reduce([c[:, None, 0] - lo[None, :, 0], hi[None, :, 0] - c[:, None, 0],
                               c[:, None, 1] - lo[None, :, 1], hi[None, :, 1] - c[:, None, 1]])
    pen = np.where(dist > 0, np.maximum(r - dist, 0.0), r + np.maximum(inner, 0.0))
    return np.where(vertical, pen, 0.0)


def _cyl_point_dist(c: np.ndarray, r: float, h: float, p: np.ndarray) -> np.ndarray:
    dh = np.maximum(np.hypot(p[:, 0] - c[:, 0], p[:, 1] - c[:, 1]) - r, 0.0)
    dz = np.maximum(np.maximum(c[:, 2] - p[:, 2], 0.0), p[:, 2] - (c[:, 2] + h))
    return np.hypot(dh, dz)


def framing_precheck(scene: dict, hz: float = 12.0, min_rate: float | None = None,
                     cfg: Framing | None = None) -> dict[str, Any]:
    """Pre-render check that each shot's subject (its aim actor, else the first actor) is in frame and not hidden
    behind blocks (same rule as post-render).

    Pinhole model from pos/aim/lens with the scene aspect; roll and shake are ignored (small). Also records the
    subject's screen size per shot (max of width/height share), used to check declared framing.
    """
    actors = scene.get("actors", [])
    if not actors:
        return {"status": "not_applicable", "reason": "no subject"}
    cfg = cfg or Framing()
    min_rate = cfg.min_visible_rate if min_rate is None else min_rate
    ev = K.SceneEvaluator(scene)
    lo, hi = occluders(scene)
    worst, per_shot = 1.0, []
    for i, shot in enumerate(scene["shots"]):
        if shot.get("move") == "pov":
            per_shot.append({"shot": shot["id"], "pov": True})
            continue
        main = shot_subject(scene, shot)
        first, last = ev.shot_frames(i)
        step = max(1, int(round(ev.fps / hz)))
        frames = list(range(first, last + 1, step))
        visible, hidden, sizes, full, pitches, rolls = 0, 0, [], [], [], []
        figure = CL.is_figure(main.get("kind", "pawn"))
        for f in frames:
            t = ev.frame_time(f)
            vis = subject_visibility(ev, scene, main, t, i, lo, hi)
            cam = ev.camera_state(t, index=i)
            d = np.array(cam["aim"], dtype=float) - np.array(cam["pos"], dtype=float)
            if np.linalg.norm(d) > 1e-6:
                pitches.append(math.degrees(math.asin(max(-1.0, min(1.0, d[2] / np.linalg.norm(d))))))
            rolls.append(abs(cam["roll_deg"]))
            if vis["in_frame"]:
                sizes.append(vis["size"])
                full.append(vis["full_height"] if figure else max(vis["full_width"], vis["full_height"]))
                if vis["occluded"] <= 1 - cfg.min_unoccluded:
                    visible += 1
                else:
                    hidden += 1
        rate = visible / max(1, len(frames))
        worst = min(worst, rate)
        size = float(np.median(full)) if full else 0.0
        pitch = float(np.median(pitches)) if pitches else 0.0
        per_shot.append({"shot": shot["id"], "subject": main["id"], "visible_rate": round(rate, 4), "occluded_frames": hidden,
                         "size_median": round(float(np.median(sizes)), 4) if sizes else 0.0,
                         "subject_size": round(size, 4),
                         "measured_framing": CL.framing_of(size, main.get("kind", "pawn")) if full else "",
                         "pitch_deg": round(pitch, 2), "roll_max_deg": round(max(rolls, default=0.0), 2),
                         "measured_angle": CL.angle_of(pitch, max(rolls, default=0.0))})
    return {"status": "passed" if worst >= min_rate else "failed",
            "rule": f"shot subject (aim actor, else the first actor) in frame and ≥{cfg.min_unoccluded:.0%} unoccluded "
                    f"in ≥ {min_rate:.0%} of frames",
            "shots": per_shot}


def shot_subject(scene: dict, shot: dict) -> dict:
    """The actor a shot is about: its camera's aim actor, else the scene's first actor."""
    aim = (shot.get("camera") or {}).get("aim_actor")
    return next((a for a in scene["actors"] if a["id"] == aim), scene["actors"][0])


def dynamic_gate(scene: dict, cfg: DynamicGate, max_issues: int = 60,
                 motion: Literal["enforce", "warn"] = "enforce") -> dict[str, Any]:
    """``motion="warn"`` (reverse-solved scenes) reports acceleration/cornering/heading-rate as warnings only."""
    ev = K.SceneEvaluator(scene)
    hz = cfg.sample_hz
    duration = float(scene["duration_s"])
    n = max(2, int(duration * hz) + 1)
    times = np.linspace(0.0, duration - 1e-6, n)
    shot_idx = np.array([ev.shot_index(t) for t in times])
    cams = np.array([ev.camera_state(t, index=int(i))["pos"] for t, i in zip(times, shot_idx)], dtype=float)
    actors = scene.get("actors", [])
    apos = {a["id"]: np.array([ev.actor_state(a["id"], t)[0] for t in times], dtype=float) for a in actors}
    ids, lo, hi = _boxes(scene)
    issues: list[dict[str, Any]] = []

    def issue(kind: str, k: int, detail: str, value: float) -> None:
        issues.append({"type": kind, "time_s": round(float(times[k]), 4), "frame": int(times[k] * ev.fps) + 1,
                       "shot": scene["shots"][int(shot_idx[k])]["id"], "value": round(float(value), 4),
                       "detail": detail})

    cam_clear = math.inf
    if len(ids):
        d = _point_box_dist(cams, lo, hi)
        per_t = d.min(1)
        cam_clear = float(per_t.min())
        for k in np.nonzero(per_t < cfg.camera_min_clearance_m)[0]:
            issue("camera_block_clearance", int(k), f"block {ids[int(d[k].argmin())]}", per_t[k])

    cam_actor = math.inf
    pov_shots = {i for i, s in enumerate(scene["shots"]) if s.get("move") == "pov"}
    for a in actors:
        dist = _cyl_point_dist(apos[a["id"]], a["radius_m"], a["height_m"], cams)
        if a["id"] == (actors[0]["id"] if actors else None):
            dist = np.where(np.isin(shot_idx, list(pov_shots)), np.inf, dist)
        if np.isfinite(dist).any():
            cam_actor = min(cam_actor, float(dist[np.isfinite(dist)].min()))
        for k in np.nonzero(dist < cfg.camera_min_clearance_m)[0]:
            issue("camera_actor_clearance", int(k), f"actor {a['id']}", dist[k])

    max_pen = 0.0
    if len(ids):
        for a in actors:
            pen = _cyl_box_pen(apos[a["id"]], a["radius_m"], a["height_m"], lo, hi)
            per_t = pen.max(1)
            max_pen = max(max_pen, float(per_t.max()))
            for k in np.nonzero(per_t > ACTOR_BLOCK_TOL_M)[0]:
                issue("actor_block_penetration", int(k), f"actor {a['id']} into {ids[int(pen[k].argmax())]}", per_t[k])

    pair_clear = math.inf
    for i, a in enumerate(actors):
        for b in actors[i + 1:]:
            pa, pb = apos[a["id"]], apos[b["id"]]
            horiz = np.hypot(pa[:, 0] - pb[:, 0], pa[:, 1] - pb[:, 1]) - a["radius_m"] - b["radius_m"]
            overlap_z = (pa[:, 2] < pb[:, 2] + b["height_m"]) & (pb[:, 2] < pa[:, 2] + a["height_m"])
            gap = np.where(overlap_z, horiz, np.inf)
            pair_clear = min(pair_clear, float(gap.min()))
            for k in np.nonzero(gap < cfg.actor_min_pair_clearance_m)[0]:
                issue("actor_pair_clearance", int(k), f"{a['id']}–{b['id']}", gap[k])

    moving_issues, motion_peaks = motion_issues(scene, cfg, ev)
    warnings = [i for i in moving_issues if motion == "warn" and i["type"] in MOTION_TYPES]
    issues.extend(i for i in moving_issues if i not in warnings)
    step = np.linalg.norm(np.diff(cams, axis=0), axis=1) * (hz / ev.fps)
    same_shot = shot_idx[1:] == shot_idx[:-1]
    for k in np.nonzero(same_shot & (step > CAMERA_JUMP_M_PER_FRAME))[0]:
        issue("camera_jump", int(k), f"{step[k]:.2f} m per frame", step[k])

    counts = Counter(i["type"] for i in issues)
    return {
        "schema": "wbs.qc.dynamic/1.1",
        "status": "passed" if not issues else "failed",
        "sample_hz": hz, "samples": int(n),
        "motion_policy": motion,
        "thresholds": {"camera_min_clearance_m": cfg.camera_min_clearance_m,
                       "actor_min_pair_clearance_m": cfg.actor_min_pair_clearance_m,
                       "actor_block_tolerance_m": ACTOR_BLOCK_TOL_M,
                       "camera_jump_m_per_frame": CAMERA_JUMP_M_PER_FRAME,
                       "motion_hold_s": cfg.motion_hold_s, "motion_min_speed_mps": cfg.motion_min_speed_mps},
        "motion": motion_peaks,
        "warnings": warnings[:max_issues],
        "warning_counts": dict(Counter(i["type"] for i in warnings)),
        "minimum_camera_clearance_m": None if math.isinf(cam_clear) else round(cam_clear, 4),
        "minimum_camera_actor_distance_m": None if math.isinf(cam_actor) else round(cam_actor, 4),
        "maximum_actor_block_penetration_m": round(max_pen, 4),
        "minimum_pair_clearance_m": None if math.isinf(pair_clear) else round(pair_clear, 4),
        "physical_cameras": len(scene["shots"]),
        "issue_counts": dict(counts),
        "issues": issues[:max_issues],
        "issues_truncated": max(0, len(issues) - max_issues),
    }
