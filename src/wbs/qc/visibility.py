"""Screen-space visibility from scene.json before rendering: pinhole projection plus occlusion by collision
blocks (segment vs axis-aligned box). Shared by the framing precheck and the setup readability check.

The camera model matches framing_precheck: position/aim/lens from the evaluator, scene aspect, roll and shake
ignored (small). Occluders are collision blocks; non-colliding dressing never hides anything.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from ..blender import kinematics as K
from ..config import QC

SENSOR_HALF_WIDTH_MM = 18.0


def camera_basis(cam: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None:
    pos, aim = np.array(cam["pos"], dtype=float), np.array(cam["aim"], dtype=float)
    fwd = aim - pos
    if np.linalg.norm(fwd) < 1e-6:
        return None
    fwd /= np.linalg.norm(fwd)
    right = np.cross(fwd, [0.0, 0.0, 1.0])
    right = right / np.linalg.norm(right) if np.linalg.norm(right) > 1e-6 else np.array([1.0, 0.0, 0.0])
    return pos, fwd, right, np.cross(right, fwd)


def project(points: np.ndarray, cam: dict, aspect: float) -> tuple[np.ndarray, np.ndarray]:
    """NDC coordinates (x right, y up, frame edges at ±1) and depth of points in front of the camera."""
    basis = camera_basis(cam)
    if basis is None:
        return np.zeros((0, 2)), np.zeros(0)
    pos, fwd, right, up = basis
    tx = SENSOR_HALF_WIDTH_MM / float(cam["lens_mm"])
    ty = tx / aspect
    d = points - pos
    z = d @ fwd
    front = z > 0.05
    ndc = np.stack([(d[front] @ right) / (z[front] * tx), (d[front] @ up) / (z[front] * ty)], axis=1)
    return ndc, z[front]


def screen_extent(points: np.ndarray, cam: dict, aspect: float) -> dict[str, Any]:
    """Share of the frame width/height covered by the points' bounding box (clipped to the frame)."""
    ndc, _ = project(points, cam, aspect)
    if not len(ndc):
        return {"in_frame": False, "width": 0.0, "height": 0.0, "size": 0.0, "full_width": 0.0, "full_height": 0.0,
                "center": None}
    x0, x1 = float(ndc[:, 0].min()), float(ndc[:, 0].max())
    y0, y1 = float(ndc[:, 1].min()), float(ndc[:, 1].max())
    inside = x1 > -1 and x0 < 1 and y1 > -1 and y0 < 1
    w = max(0.0, min(x1, 1.0) - max(x0, -1.0)) / 2
    h = max(0.0, min(y1, 1.0) - max(y0, -1.0)) / 2
    return {"in_frame": inside, "width": w, "height": h, "size": max(w, h),
            "full_width": (x1 - x0) / 2, "full_height": (y1 - y0) / 2,
            "center": [(max(x0, -1) + min(x1, 1)) / 4 + 0.5, 0.5 - (max(y0, -1) + min(y1, 1)) / 4] if inside else None}


def occluders(scene: dict, exclude: set[str] = frozenset()) -> tuple[np.ndarray, np.ndarray]:
    lo, hi = [], []
    for b in scene.get("blocks", []):
        if not b.get("collision", True) or b["id"] in exclude or b.get("group") in exclude:
            continue
        a, c = K.block_aabb(b)
        lo.append(a)
        hi.append(c)
    return np.array(lo, dtype=float).reshape(-1, 3), np.array(hi, dtype=float).reshape(-1, 3)


def occluded_fraction(points: np.ndarray, cam_pos: np.ndarray, lo: np.ndarray, hi: np.ndarray,
                      margin: float = 0.05) -> float:
    """Share of points whose line of sight to the camera passes through a box (boxes holding the point are skipped)."""
    if not len(points):
        return 1.0
    if not len(lo):
        return 0.0
    d = cam_pos[None, :] - points
    length = np.linalg.norm(d, axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        inv = 1.0 / np.where(np.abs(d) < 1e-12, 1e-12, d)
        t0 = (lo[None] - points[:, None]) * inv[:, None]
        t1 = (hi[None] - points[:, None]) * inv[:, None]
    enter = np.minimum(t0, t1).max(-1)
    leave = np.maximum(t0, t1).min(-1)
    eps = (margin / np.maximum(length, 1e-9))[:, None]
    holds_point = np.all((points[:, None] >= lo[None] - margin) & (points[:, None] <= hi[None] + margin), axis=-1)
    hit = (leave >= np.maximum(enter, 0.0)) & (enter > eps) & (enter < 1.0 - eps) & ~holds_point
    return float(hit.any(axis=1).mean())


def cylinder_points(base: np.ndarray, radius: float, height: float) -> np.ndarray:
    ring = [(np.cos(a), np.sin(a)) for a in np.linspace(0, 2 * np.pi, 4, endpoint=False)]
    pts = [base + [0, 0, 0.05], base + [0, 0, height * 0.95]]
    for level in (0.25, 0.6, 0.9):
        pts += [base + [radius * 0.9 * c, radius * 0.9 * s, height * level] for c, s in ring]
    return np.array(pts, dtype=float)


def box_points(lo: np.ndarray, hi: np.ndarray, shrink: float = 0.05) -> np.ndarray:
    c = (lo + hi) / 2
    half = (hi - lo) / 2 * (1 - shrink)
    signs = np.array([[sx, sy, sz] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)], dtype=float)
    faces = np.array([[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]], dtype=float)
    return np.vstack([c + signs * half, c + faces * half, c[None]])


def subject_visibility(ev: K.SceneEvaluator, scene: dict, actor: dict, t: float, shot_index: int,
                       lo: np.ndarray, hi: np.ndarray) -> dict[str, Any]:
    cam = ev.camera_state(t, index=shot_index)
    aspect = scene["resolution"][0] / scene["resolution"][1]
    pts = cylinder_points(np.array(ev.actor_state(actor["id"], t)[0]), actor.get("radius_m", 0.35),
                          actor.get("height_m", 1.7))
    extent = screen_extent(pts, cam, aspect)
    in_view = pts[_in_front_and_frame(pts, cam, aspect)]
    occ = occluded_fraction(in_view, np.array(cam["pos"], dtype=float), lo, hi) if len(in_view) else 1.0
    return {**extent, "occluded": occ}


def _in_front_and_frame(points: np.ndarray, cam: dict, aspect: float) -> np.ndarray:
    basis = camera_basis(cam)
    if basis is None:
        return np.zeros(len(points), bool)
    pos, fwd, right, up = basis
    tx = SENSOR_HALF_WIDTH_MM / float(cam["lens_mm"])
    ty = tx / aspect
    d = points - pos
    z = d @ fwd
    with np.errstate(divide="ignore", invalid="ignore"):
        x, y = (d @ right) / (z * tx), (d @ up) / (z * ty)
    return (z > 0.05) & (np.abs(x) <= 1.0) & (np.abs(y) <= 1.0)


def _target_boxes(scene: dict, target: str, ev: K.SceneEvaluator, t: float) -> list[tuple[np.ndarray, np.ndarray]]:
    boxes = []
    for b in scene.get("blocks", []):
        if b["id"] == target or b.get("group") == target:
            a, c = K.block_aabb(b)
            boxes.append((np.array(a, dtype=float), np.array(c, dtype=float)))
    if not boxes and target in ev.actors:
        actor = ev.actors[target]
        base = np.array(ev.actor_state(target, t)[0])
        r, h = actor.get("radius_m", 0.35), actor.get("height_m", 1.7)
        boxes.append((base + [-r, -r, 0.0], base + [r, r, h]))
    return boxes


def setup_readability(scene: dict, qc: QC, hz: float = 12.0) -> list[dict[str, Any]]:
    """For every setup event: is what it sets up (targets / focus_region) readable in the shot where it happens?"""
    cfg = qc.readability
    ev = K.SceneEvaluator(scene)
    aspect = scene["resolution"][0] / scene["resolution"][1]
    out = []
    for event in scene.get("events", []):
        if event.get("role") != "setup":
            continue
        targets = list(event.get("targets") or [])
        regions = []
        if event.get("focus_region"):
            cx, cy, cz, sx, sy, sz = event["focus_region"]
            regions.append(("focus_region", np.array([cx - sx / 2, cy - sy / 2, cz - sz / 2]),
                            np.array([cx + sx / 2, cy + sy / 2, cz + sz / 2])))
        if not targets and not regions:
            out.append({"event": event["id"], "status": "warning", "detail": "铺垫没写 targets 或 focus_region，无法核对画面里是否看得清"})
            continue
        index = ev.shot_index(float(event["at_s"]))
        first, last = ev.shot_frames(index)
        step = max(1, int(round(ev.fps / hz)))
        results = []
        for name in targets + [r[0] for r in regions]:
            exclude = {name} if name != "focus_region" else set()
            blo, bhi = occluders(scene, exclude)
            frames = []
            for f in range(first, last + 1, step):
                t = ev.frame_time(f)
                cam = ev.camera_state(t, index=index)
                boxes = ([(lo_, hi_) for n, lo_, hi_ in regions if n == name] if name == "focus_region"
                         else _target_boxes(scene, name, ev, t))
                if not boxes:
                    break
                pts = np.vstack([box_points(lo_, hi_) for lo_, hi_ in boxes])
                width = screen_extent(pts, cam, aspect)["width"]
                visible = pts[_in_front_and_frame(pts, cam, aspect)]
                occ = occluded_fraction(visible, np.array(cam["pos"], dtype=float), blo, bhi) if len(visible) else 1.0
                score = width if occ <= 1 - cfg.setup_min_unoccluded else 0.0
                frames.append((score, width, occ, f))
            if not frames:
                results.append({"target": name, "missing": True, "score": 0.0, "width": 0.0, "occluded": 1.0})
                continue
            score, width, occ, f = max(frames)
            results.append({"target": name, "score": round(score, 4), "width": round(width, 4), "occluded": round(occ, 3),
                            "frame": f})
        unreadable = [r for r in results if r.get("score", 0.0) < cfg.setup_min_width_fraction]
        status = "warning" if unreadable else "ok"
        detail = "；".join(
            f"{r['target']} 最清楚时只占画幅宽度 {100 * r.get('width', 0):.1f}%（遮挡 {100 * r.get('occluded', 1):.0f}%），"
            f"需要至少 {100 * cfg.setup_min_width_fraction:.0f}% 且遮挡不超过 {100 * (1 - cfg.setup_min_unoccluded):.0f}%"
            if not r.get("missing") else f"{r['target']} 在场景里找不到" for r in unreadable)
        out.append({"event": event["id"], "shot": scene["shots"][index]["id"], "status": status, "targets": results,
                    "detail": detail})
    return out
