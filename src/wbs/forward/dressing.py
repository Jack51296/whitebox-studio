"""Scale and speed cues for white-box scenes, written into scene.json as ordinary blocks.

Reference posts line fast paths (near repeated elements give motion parallax), dashed lane marks follow vehicle paths
on straight stretches, container stacks are split into standard 40 ft units, and tall buildings get floor lines.
Posts collide (cameras and actors keep clear of them); marks and floor lines do not. Everything is deterministic.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from ..blender import kinematics as K
from ..config import Dressing

CONTAINER_M = (12.19, 2.44, 2.59)
POST_RADIUS = 0.15
DASH_M, GAP_M = 3.0, 6.0


def _rotate(x: float, y: float, yaw_deg: float) -> tuple[float, float]:
    c, s = math.cos(math.radians(yaw_deg)), math.sin(math.radians(yaw_deg))
    return x * c - y * s, x * s + y * c


def split_containers(blocks: list[dict]) -> tuple[list[dict], int]:
    """Replace ``role: container`` blocks by unit boxes (same footprint, hairline gaps); units keep ``group`` = old id."""
    out, units = [], 0
    for b in blocks:
        if b.get("role") != "container" or b.get("group"):
            out.append(b)
            continue
        sx, sy, sz = b["size"]
        yaw = (b.get("rotation_deg") or [0, 0, 0])[2]
        along_x = sx >= sy
        length, width = (sx, sy) if along_x else (sy, sx)
        n_len = max(1, round(length / CONTAINER_M[0]))
        n_wid = max(1, round(width / CONTAINER_M[1]))
        n_hgt = max(1, round(sz / CONTAINER_M[2]))
        ul, uw, uh = length / n_len, width / n_wid, sz / n_hgt
        cx, cy, cz = b["center"]
        for i in range(n_len):
            for j in range(n_wid):
                for k in range(n_hgt):
                    a = -length / 2 + ul * (i + 0.5)
                    c = -width / 2 + uw * (j + 0.5)
                    lx, ly = (a, c) if along_x else (c, a)
                    dx, dy = _rotate(lx, ly, yaw)
                    size = (ul - 0.06, uw - 0.06, uh - 0.03) if along_x else (uw - 0.06, ul - 0.06, uh - 0.03)
                    out.append({**b, "id": f"{b['id']}__{i}_{j}_{k}", "group": b["id"],
                                "center": [round(cx + dx, 3), round(cy + dy, 3), round(cz - sz / 2 + uh * (k + 0.5), 3)],
                                "size": [round(v, 3) for v in size], "label": b.get("label") or "集装箱"})
                    units += 1
    return out, units


def floor_lines(blocks: list[dict], floor_m: float) -> list[dict]:
    """Thin bands around every ``role: building`` block of 6 m or more, one per floor (non-colliding)."""
    out = []
    for b in blocks:
        if b.get("role") != "building" or b.get("shape", "box") != "box" or b["size"][2] < 6.0:
            continue
        sx, sy, sz = b["size"]
        base = b["center"][2] - sz / 2
        for n in range(1, int((sz - 0.5) // floor_m) + 1):
            out.append({"id": f"{b['id']}__floor{n}", "shape": "box",
                        "center": [b["center"][0], b["center"][1], round(base + n * floor_m, 3)],
                        "size": [round(sx + 0.06, 3), round(sy + 0.06, 3), 0.08],
                        "rotation_deg": list(b.get("rotation_deg") or [0, 0, 0]), "role": "floor_line",
                        "collision": False, "label": "楼层线", "group": b["id"]})
    return out


def _samples(ev: K.SceneEvaluator, scene: dict, step: float = 0.1) -> tuple[dict[str, np.ndarray], np.ndarray]:
    end = ev.time_map.source_end
    src = np.arange(0.0, end + 1e-9, step)
    actors = {a["id"]: np.array([ev.tracks[a["id"]].at(float(t)) for t in src]) for a in scene.get("actors", [])}
    edit = np.arange(0.0, float(scene["duration_s"]), step / 5)  # tracking cameras move metres per 0.1 s
    cams = np.array([ev.camera_state(float(t))["pos"] for t in edit]) if len(edit) else np.zeros((0, 3))
    return actors, cams


def _clear_of_blocks(p: np.ndarray, boxes: list[tuple], margin: float) -> bool:
    return all(K.point_aabb_distance(tuple(p), lo, hi) > margin for lo, hi in boxes)


def reference_posts(scene: dict, blocks: list[dict], cfg: Dressing, actors: dict[str, np.ndarray],
                    cams: np.ndarray, step: float = 0.1) -> list[dict]:
    """Posts every ``post_spacing_m`` along paths faster than ``post_min_speed_mps``, staggered on both sides.

    A post that would stand closer to another actor's path than that path's own post offset is dropped, so parallel
    routes (cars side by side) keep posts on the outer kerbs instead of between the lanes."""
    boxes = [K.block_aabb(b) for b in blocks if b.get("collision", True) and b.get("role") not in ("ground", "floor")]
    by_id = {a["id"]: a for a in scene.get("actors", [])}
    everyone = [(pos, by_id[aid].get("radius_m", 0.35)) for aid, pos in actors.items()]
    offsets = {aid: _post_offset(by_id[aid]) for aid in actors}
    posts: list[np.ndarray] = []
    out = []
    for aid, pos in actors.items():
        actor = by_id[aid]
        seg = np.linalg.norm(np.diff(pos[:, :2], axis=0), axis=1)
        moving = seg > 1e-3
        if not moving.any() or seg[moving].mean() / step < cfg.post_min_speed_mps:
            continue
        vehicle = actor.get("kind") == "vehicle"
        height = 5.0 if vehicle else 3.0
        offset = offsets[aid]
        arc = np.r_[0.0, np.cumsum(seg)]
        for n, s in enumerate(np.arange(cfg.post_spacing_m / 2, arc[-1], cfg.post_spacing_m / 2)):
            i = min(int(np.searchsorted(arc, s)), len(pos) - 2)
            d = pos[i + 1, :2] - pos[i, :2]
            if np.linalg.norm(d) < 1e-6:
                continue
            d /= np.linalg.norm(d)
            side = 1.0 if n % 2 == 0 else -1.0
            p = np.array([pos[i, 0] + d[1] * offset * side, pos[i, 1] - d[0] * offset * side, height / 2])
            if any(np.linalg.norm(q[:2] - p[:2]) < cfg.post_spacing_m * 0.45 for q in posts):
                continue
            if not _clear_of_blocks(p, boxes, 0.4):
                continue
            if any((np.hypot(path[:, 0] - p[0], path[:, 1] - p[1]) < r + POST_RADIUS + 0.6).any() for path, r in everyone):
                continue
            if any((np.hypot(path[:, 0] - p[0], path[:, 1] - p[1]) < offsets[other] + 1.0).any()
                   for other, path in actors.items() if other != aid):
                continue
            if _between_routes(pos[i, :2], d, side, offset, actors, aid, cfg.post_spacing_m / 2):
                continue
            if len(cams):
                near = np.hypot(cams[:, 0] - p[0], cams[:, 1] - p[1]) < POST_RADIUS + 1.0
                if (near & (cams[:, 2] < height + 0.5)).any():
                    continue
            posts.append(p)
            out.append({"id": f"POST_{len(out) + 1:03}", "shape": "cylinder",
                        "center": [round(float(p[0]), 3), round(float(p[1]), 3), round(height / 2, 3)],
                        "size": [2 * POST_RADIUS, 2 * POST_RADIUS, height], "role": "reference_post", "collision": True,
                        "label": "尺度参照柱"})
    return out


def _post_offset(actor: dict) -> float:
    return actor.get("radius_m", 0.35) + (2.2 if actor.get("kind") == "vehicle" else 1.5)


def _between_routes(q: np.ndarray, d: np.ndarray, side: float, offset: float, actors: dict[str, np.ndarray],
                    aid: str, reach: float, road_m: float = 15.0) -> bool:
    """Would a post ``offset`` to this side of the route at ``q`` stand between it and another route nearby?"""
    normal = np.array([d[1], -d[0]]) * side
    for other, path in actors.items():
        if other == aid:
            continue
        rel = path[:, :2] - q
        along, across = rel @ d, rel @ normal
        if ((np.abs(along) < reach) & (across > offset) & (across < offset + road_m)).any():
            return True
    return False


def _along(pos: np.ndarray, arc: np.ndarray, s: float) -> tuple[np.ndarray, np.ndarray]:
    """Point and unit direction at arc length ``s`` of a sampled path."""
    i = min(int(np.searchsorted(arc, s)), len(pos) - 2)
    d = pos[i + 1, :2] - pos[i, :2]
    return pos[i, :2], d / max(np.linalg.norm(d), 1e-9)


def lane_marks(scene: dict, blocks: list[dict], actors: dict[str, np.ndarray]) -> list[dict]:
    """Dashes 1.8 m left of vehicle paths on straight stretches (flat, non-colliding)."""
    boxes = [K.block_aabb(b) for b in blocks if b.get("collision", True) and b.get("role") not in ("ground", "floor")]
    by_id = {a["id"]: a for a in scene.get("actors", [])}
    centres: list[np.ndarray] = []
    out = []
    for aid, pos in actors.items():
        if by_id[aid].get("kind") != "vehicle":
            continue
        seg = np.linalg.norm(np.diff(pos[:, :2], axis=0), axis=1)
        arc = np.r_[0.0, np.cumsum(seg)]
        for s in np.arange(GAP_M, arc[-1] - DASH_M, DASH_M + GAP_M):
            (p0, d0), (p1, d1) = _along(pos, arc, s), _along(pos, arc, s + DASH_M)
            if float(d0 @ d1) < math.cos(math.radians(8)) or np.linalg.norm(p1 - p0) < DASH_M * 0.8:
                continue
            mid, d = (p0 + p1) / 2, (d0 + d1) / np.linalg.norm(d0 + d1)
            c = np.array([mid[0] - d[1] * 1.8, mid[1] + d[0] * 1.8, 0.005])
            if any(np.linalg.norm(q[:2] - c[:2]) < 1.0 for q in centres) or not _clear_of_blocks(c, boxes, 0.3):
                continue
            centres.append(c)
            out.append({"id": f"LANE_{len(out) + 1:03}", "shape": "box",
                        "center": [round(float(c[0]), 3), round(float(c[1]), 3), 0.005], "size": [0.15, DASH_M, 0.01],
                        "rotation_deg": [0.0, 0.0, round(K.heading_deg(float(d[0]), float(d[1])), 2)],
                        "role": "lane_marking", "collision": False, "label": "车道虚线"})
    return out


def dress(scene: dict, cfg: Dressing) -> dict[str, Any]:
    """Add the cues in place; returns counts (also stored as ``meta.dressing``)."""
    if not cfg.enabled:
        return {}
    blocks = list(scene.get("blocks", []))
    counts: dict[str, Any] = {}
    if cfg.containers:
        blocks, counts["container_units"] = split_containers(blocks)
    if cfg.floor_lines:
        lines = floor_lines(blocks, cfg.floor_height_m)
        blocks += lines
        counts["floor_lines"] = len(lines)
    if (cfg.posts or cfg.lane_lines) and scene.get("actors"):
        actors, cams = _samples(K.SceneEvaluator({**scene, "blocks": blocks}), scene)
        if cfg.posts:
            posts = reference_posts(scene, blocks, cfg, actors, cams)
            blocks += posts
            counts["reference_posts"] = len(posts)
        if cfg.lane_lines:
            marks = lane_marks(scene, blocks, actors)
            blocks += marks
            counts["lane_marks"] = len(marks)
    scene["blocks"] = blocks
    scene.setdefault("meta", {})["dressing"] = counts
    return counts
