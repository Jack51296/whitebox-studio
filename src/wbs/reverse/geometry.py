"""Geometry solve: real per-frame cameras + depth → draft scene.json (replaces the monocular heuristic).

Input is a camera track in OpenCV convention (x right, y down, z forward; camera-to-world), produced by
the vision worker (DA3 / MapAnything) or imported from MegaSaM / COLMAP as NPZ. Per shot:

1. gravity: RANSAC ground plane in the back-projected depth (normal near the mean camera up), else the
   mean camera up vector;
2. scale: metric track as is; otherwise camera height above the ground plane = assumed camera height,
   else the subject's pixel height × depth = assumed subject height;
3. local frame: ground point below the first camera is the origin, its heading is +Y, Z is up;
4. subject: foot ray of the occupancy box intersected with the ground (depth fallback);
5. shots are chained so the subject path stays continuous (same rule as the heuristic solver);
6. blocks: depth points above the ground rasterised on a 0.5 m grid, connected cells → boxes, with a
   keep-out corridor around the camera and subject paths.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ..models.scene import SceneSpec
from .solve import (
    MAX_SOLVED_SPEED,
    SUBJECT_KINDS,
    _describe_flow,
    _interp_boxes,
    _limit_speed,
    _median,
    _smooth,
)

GEOMETRY_SOLVER = "reverse/geometry 1.0 (6-DoF cameras + depth: {source}; gravity {gravity}; scale {scale})"
MAX_CAMERA_SPEED_MPS = 25.0


class GeometryError(ValueError):
    """The track cannot support a geometry solve (the caller falls back to the heuristic solver)."""


def load_track(path: Path, analysis: dict | None = None, stride: int = 1) -> dict[str, Any]:
    """Read a wbs camera track NPZ, or a MegaSaM ``sgd_cvd_hr.npz`` (cam_c2w / intrinsic / depths)."""
    with np.load(path, allow_pickle=False) as z:
        files = set(z.files)
        if "c2w" in files:
            track = {k: np.array(z[k]) for k in ("frames", "shot", "c2w", "K", "depth", "conf") if k in files}
            meta = json.loads(str(z["meta"])) if "meta" in files else {}
        elif "cam_c2w" in files:
            c2w = np.array(z["cam_c2w"], dtype=np.float64)
            n = len(c2w)
            depth = np.array(z["depths"], dtype=np.float32) if "depths" in files else None
            track = {"frames": np.arange(n, dtype=np.int32) * stride, "c2w": c2w,
                     "K": np.repeat(np.array(z["intrinsic"], dtype=np.float64)[None], n, 0)}
            if depth is not None:
                track["depth"] = depth
            size = [int(depth.shape[2]), int(depth.shape[1])] if depth is not None else None
            meta = {"source": "megasam", "metric": False, "convention": "opencv_c2w", "image_size": size}
        else:
            raise GeometryError(f"{Path(path).name}: 既不是 wbs 相机轨迹（c2w/K/frames）也不是 MegaSaM 结果（cam_c2w/intrinsic）")
    if "shot" not in track:
        if analysis is None:
            raise GeometryError("轨迹缺少 shot 字段，需要 analysis 按切点分配")
        shot = np.full(len(track["frames"]), -1, dtype=np.int32)
        for n, s in enumerate(analysis["shots"]):
            shot[(track["frames"] >= s["start_frame"]) & (track["frames"] <= s["end_frame"])] = n
        track["shot"] = shot
    track["meta"] = meta
    return track


def _unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 else v


def _backproject(depth: np.ndarray, K: np.ndarray, c2w: np.ndarray, image_size: tuple[int, int],
                 rows: slice | None = None, conf: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """World points and their normalised pixel coords for a (downsampled) depth map."""
    h, w = depth.shape
    W, H = image_size
    v, u = np.mgrid[0:h, 0:w]
    px = (u + 0.5) * W / w
    py = (v + 0.5) * H / h
    keep = depth > 1e-6
    if conf is not None:
        keep &= conf >= np.median(conf)
    if rows is not None:
        band = np.zeros_like(keep)
        band[rows] = True
        keep &= band
    z = depth[keep].astype(np.float64)
    x = (px[keep] - K[0, 2]) / K[0, 0] * z
    y = (py[keep] - K[1, 2]) / K[1, 1] * z
    cam = np.stack([x, y, z], 1)
    world = cam @ c2w[:3, :3].T + c2w[:3, 3]
    return world, np.stack([px[keep] / W, py[keep] / H], 1)


def ground_plane(points: np.ndarray, up_hint: np.ndarray, threshold: float, iterations: int = 300,
                 max_angle_deg: float = 35.0, seed: int = 0) -> tuple[np.ndarray, np.ndarray, float] | None:
    """RANSAC plane whose normal is within ``max_angle_deg`` of ``up_hint``: (normal, point, inlier ratio)."""
    if len(points) < 50:
        return None
    rng = np.random.default_rng(seed)
    cos_max = math.cos(math.radians(max_angle_deg))
    best, best_score, best_count = None, 0.0, 0
    for _ in range(iterations):
        a, b, c = points[rng.choice(len(points), 3, replace=False)]
        n = np.cross(b - a, c - a)
        if np.linalg.norm(n) < 1e-9:
            continue
        n = _unit(n)
        if abs(float(n @ up_hint)) < cos_max:
            continue
        d = np.abs((points - a) @ n) / threshold
        near = d < 1.0
        score = float((1.0 - d[near] ** 2).sum())
        if score > best_score:
            best, best_score, best_count = (n, a), score, int(near.sum())
    if best is None:
        return None
    n, a = best
    tol = threshold
    for _ in range(5):
        inliers = points[np.abs((points - a) @ n) < tol]
        if len(inliers) < 3:
            break
        a = inliers.mean(0)
        _, _, vt = np.linalg.svd(inliers - a, full_matrices=False)
        n = _unit(vt[-1])
        tol = max(3.0 * float(np.median(np.abs((inliers - a) @ n))), threshold / 20)
    if n @ up_hint < 0:
        n = -n
    return n, a, best_count / len(points)


def _nlerp(q0: np.ndarray, q1: np.ndarray, u: float) -> np.ndarray:
    if q0 @ q1 < 0:
        q1 = -q1
    return _unit((1 - u) * q0 + u * q1)


def _quat(r: np.ndarray) -> np.ndarray:
    t = np.trace(r)
    if t > 0:
        s = math.sqrt(t + 1.0) * 2
        return np.array([0.25 * s, (r[2, 1] - r[1, 2]) / s, (r[0, 2] - r[2, 0]) / s, (r[1, 0] - r[0, 1]) / s])
    i = int(np.argmax(np.diag(r)))
    j, k = (i + 1) % 3, (i + 2) % 3
    s = math.sqrt(1.0 + r[i, i] - r[j, j] - r[k, k]) * 2
    q = np.zeros(4)
    q[0] = (r[k, j] - r[j, k]) / s
    q[1 + i] = 0.25 * s
    q[1 + j] = (r[j, i] + r[i, j]) / s
    q[1 + k] = (r[k, i] + r[i, k]) / s
    return q


def _rot(q: np.ndarray) -> np.ndarray:
    w, x, y, z = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def interpolate_cameras(frames: np.ndarray, c2w: np.ndarray, wanted: list[int]) -> dict[int, np.ndarray]:
    """Camera-to-world for every wanted frame (position lerp, rotation nlerp, clamped at the ends)."""
    quats = [_quat(m[:3, :3]) for m in c2w]
    out = {}
    for f in wanted:
        j = int(np.searchsorted(frames, f))
        if j <= 0 or j >= len(frames):
            k = 0 if j <= 0 else len(frames) - 1
            out[f] = c2w[k]
            continue
        a, b = frames[j - 1], frames[j]
        u = (f - a) / max(b - a, 1)
        m = np.eye(4)
        m[:3, :3] = _rot(_nlerp(quats[j - 1], quats[j], u))
        m[:3, 3] = (1 - u) * c2w[j - 1, :3, 3] + u * c2w[j, :3, 3]
        out[f] = m
    return out


def _frame_of_shot(c2w: np.ndarray, K: np.ndarray, depth: np.ndarray | None, conf: np.ndarray | None,
                   image_size: tuple[int, int], metric: bool, camera_height: float, subject_h: float,
                   boxes: dict[int, list[float]], frames: np.ndarray) -> dict[str, Any]:
    """Gravity, scale and origin of one shot's local frame."""
    up_cam = _unit(-np.mean(c2w[:, :3, 1], axis=0))
    info: dict[str, Any] = {"gravity": "camera_up", "scale_from": "assumed"}
    plane = None
    if depth is not None:
        pick = np.linspace(0, len(c2w) - 1, min(8, len(c2w))).round().astype(int)
        h = depth.shape[1]
        pts = [_backproject(depth[i], K[i], c2w[i], image_size, rows=slice(int(h * 0.55), h),
                            conf=None if conf is None else conf[i])[0] for i in pick]
        pts = np.concatenate(pts) if pts else np.zeros((0, 3))
        if len(pts) > 4000:
            pts = pts[np.random.default_rng(1).choice(len(pts), 4000, replace=False)]
        scale_ref = float(np.median(depth[depth > 0])) if np.any(depth > 0) else 1.0
        plane = ground_plane(pts, up_cam, threshold=0.02 * scale_ref)
    if plane is not None and plane[2] >= 0.15:
        up, p0, ratio = plane
        info.update(gravity="ground_plane", ground_inliers=round(ratio, 3))
    else:
        up, p0 = up_cam, None
    heights = [float(up @ (c[:3, 3] - p0)) for c in c2w] if p0 is not None else []
    cam_h = float(np.median(heights)) if heights else None
    if metric:
        scale = 1.0
        info["scale_from"] = "metric_track"
    elif cam_h and cam_h > 1e-6:
        scale = camera_height / cam_h
        info["scale_from"] = f"camera_height {camera_height:g} m"
    else:
        scale = 1.0
        if depth is not None and boxes:
            ests = []
            for i, f in enumerate(frames):
                box = boxes.get(int(f))
                if box is None:
                    continue
                d = depth[i]
                cy, cx = int((box[1] + box[3]) / 2 * d.shape[0]), int((box[0] + box[2]) / 2 * d.shape[1])
                z = float(d[min(cy, d.shape[0] - 1), min(cx, d.shape[1] - 1)])
                if z > 0:
                    ests.append((box[3] - box[1]) * image_size[1] * z / K[i, 1, 1])
            if ests:
                scale = subject_h / float(np.median(ests))
                info["scale_from"] = f"subject_height {subject_h:g} m"
    c0 = c2w[0, :3, 3]
    ground0 = c0 - up * (float(up @ (c0 - p0)) if p0 is not None else camera_height / scale)
    fwd = c2w[0, :3, 2] - up * float(c2w[0, :3, 2] @ up)
    if np.linalg.norm(fwd) < 1e-6:
        fwd = -c2w[0, :3, 1] - up * float(-c2w[0, :3, 1] @ up)
    fwd = _unit(fwd)
    right = np.cross(fwd, up)
    info.update(scale=round(scale, 6), camera_height_units=None if cam_h is None else round(cam_h, 6))
    return {"A": np.stack([right, fwd, up]), "origin": ground0, "scale": scale, "info": info}


def _intrudes(center: tuple[float, float], size: tuple[float, float], angle_deg: float, height: float,
              keepout: np.ndarray, clearance: float) -> bool:
    """Any keep-out point (camera / subject path) inside the box footprint grown by ``clearance`` and below its top."""
    if len(keepout) == 0:
        return False
    a = math.radians(angle_deg)
    d = keepout[:, :2] - np.array(center)
    lx = d[:, 0] * math.cos(a) + d[:, 1] * math.sin(a)
    ly = -d[:, 0] * math.sin(a) + d[:, 1] * math.cos(a)
    hit = (np.abs(lx) < size[0] / 2 + clearance) & (np.abs(ly) < size[1] / 2 + clearance) & (keepout[:, 2] < height + clearance)
    return bool(hit.any())


def _occupancy_blocks(points: np.ndarray, keepout: np.ndarray, cell: float = 0.5, min_points: int = 6,
                      clearance: float = 1.0, max_blocks: int = 80, tile_m: float = 4.0) -> list[dict[str, Any]]:
    """Connected occupied cells → oriented boxes; a box that would intrude on the camera / subject corridor is
    split into ``tile_m`` tiles (axis-aligned), and tiles that still intrude are dropped."""
    if len(points) == 0:
        return []
    pts = points[(points[:, 2] > 0.3) & (np.abs(points[:, :2]) < 120).all(1)]
    if len(pts) == 0:
        return []
    lo = pts[:, :2].min(0) - cell
    shape = np.ceil((pts[:, :2].max(0) + cell - lo) / cell).astype(int)[::-1]
    ij = ((pts[:, :2] - lo) / cell).astype(int)
    count = np.zeros(shape, np.int32)
    np.add.at(count, (ij[:, 1], ij[:, 0]), 1)
    grid = (count >= min_points).astype(np.uint8)
    if len(keepout):
        ko = ((keepout[:, :2] - lo) / cell).astype(int)
        r = int(math.ceil(clearance / cell))
        for x, y in ko:
            grid[max(0, y - r):y + r + 1, max(0, x - r):x + r + 1] = 0
    flat = ij[:, 1] * shape[1] + ij[:, 0]

    def height_of(ys: np.ndarray, xs: np.ndarray) -> float:
        member = np.isin(flat, ys * shape[1] + xs)
        return max(0.5, min(float(np.percentile(pts[member, 2], 95)) if member.any() else 1.0, 40.0))

    def to_world(cx: float, cy: float) -> tuple[float, float]:
        return float(lo[0] + cx * cell), float(lo[1] + cy * cell)

    n, labels, stats, _ = cv2.connectedComponentsWithStats(grid, connectivity=8)
    rects: list[tuple[tuple[float, float], tuple[float, float], float, float, int]] = []
    for k in sorted(range(1, n), key=lambda k: -stats[k, cv2.CC_STAT_AREA]):
        ys, xs = np.nonzero(labels == k)
        cells = np.stack([xs, ys], 1).astype(np.float32) + 0.5
        (cx, cy), (w, h), angle = cv2.minAreaRect(cells) if len(cells) >= 3 else ((float(cells[0][0]), float(cells[0][1])), (1.0, 1.0), 0.0)
        height = height_of(ys, xs)
        center, size = to_world(cx, cy), (max(w, 1.0) * cell, max(h, 1.0) * cell)
        if not _intrudes(center, size, angle, height, keepout, clearance):
            rects.append((center, size, float(angle), height, len(cells)))
            continue
        step = max(1, int(round(tile_m / cell)))
        for ty in range(ys.min(), ys.max() + 1, step):
            for tx in range(xs.min(), xs.max() + 1, step):
                inside = (ys >= ty) & (ys < ty + step) & (xs >= tx) & (xs < tx + step)
                if not inside.any():
                    continue
                sy, sx = ys[inside], xs[inside]
                center = to_world((sx.min() + sx.max() + 1) / 2, (sy.min() + sy.max() + 1) / 2)
                size = ((sx.max() - sx.min() + 1) * cell, (sy.max() - sy.min() + 1) * cell)
                height = height_of(sy, sx)
                if not _intrudes(center, size, 0.0, height, keepout, clearance):
                    rects.append((center, size, 0.0, height, int(inside.sum())))
    rects.sort(key=lambda r: -r[4])
    return [{"id": f"D{rank:02}", "shape": "box",
             "center": [round(c[0], 3), round(c[1], 3), round(hgt / 2, 3)],
             "size": [round(s[0], 3), round(s[1], 3), round(hgt, 3)],
             "rotation_deg": [0.0, 0.0, round(ang, 2)], "role": "depth_occupancy", "collision": True, "label": "深度占据体块"}
            for rank, (c, s, ang, hgt, _) in enumerate(rects[:max_blocks])]


def solve_geometry(analysis: dict[str, Any], track: dict[str, Any], job_id: str, title: str, subject: str = "person",
                   camera_height: float = 1.6, resolution: tuple[int, int] | None = None, max_ray_m: float = 80.0,
                   scale_source: str = "auto") -> tuple[SceneSpec, dict[str, Any]]:
    """``scale_source``: auto (metric track if metric, else camera height) | metric | camera_height."""
    video = analysis["video"]
    fps = int(round(video["fps"]))
    W, H = video["width"], video["height"]
    meta = track.get("meta", {})
    img_w, img_h = meta.get("image_size") or [W, H]
    kscale = np.array([W / img_w, H / img_h, 1.0])
    kind, subj_h, subj_r = SUBJECT_KINDS.get(subject, SUBJECT_KINDS["person"])
    duration = round(video["frames"] / fps, 6)
    world_path: list[list[float]] = []
    shots_out, clouds, cams_world, shot_info = [], [], [], []
    prev_end: tuple[float, float, float] | None = None
    prev_heading: float | None = None
    for n, shot in enumerate(analysis["shots"]):
        rows = np.nonzero(track["shot"] == n)[0]
        rows = rows[np.argsort(track["frames"][rows])]
        if len(rows) < 2:
            raise GeometryError(f"{shot['id']} 只有 {len(rows)} 帧相机，无法做几何求解")
        c2w = track["c2w"][rows].astype(np.float64)
        K = track["K"][rows].astype(np.float64) * kscale[None, :, None]
        frames = track["frames"][rows]
        depth = track["depth"][rows].astype(np.float32) if "depth" in track else None
        conf = track["conf"][rows].astype(np.float32) if "conf" in track else None
        start, end = shot["start_frame"], shot["end_frame"]
        samples = {int(k): v for k, v in shot["occupancy_samples"].items()}
        boxes = _interp_boxes(samples, list(range(start, end + 1))) if samples else {}
        metric = bool(meta.get("metric")) and scale_source != "camera_height"
        frame = _frame_of_shot(c2w, K, depth, conf, (W, H), metric, camera_height, subj_h,
                               {int(f): boxes[int(f)] for f in frames if int(f) in boxes}, frames)
        A, origin, s = frame["A"], frame["origin"], frame["scale"]

        def local(p: np.ndarray, A=A, origin=origin, s=s) -> np.ndarray:
            return s * (A @ (p - origin))

        cams = interpolate_cameras(frames, c2w, list(range(start, end + 1)))
        subj_local = []
        for f in sorted(samples):
            box = samples[f]["bbox"]
            m = cams[f]
            Kf = K[int(np.argmin(np.abs(frames - f)))]
            u, v = (box[0] + box[2]) / 2 * W, box[3] * H
            ray = local(m[:3, :3] @ np.array([(u - Kf[0, 2]) / Kf[0, 0], (v - Kf[1, 2]) / Kf[1, 1], 1.0]) + m[:3, 3])
            cpos = local(m[:3, 3])
            d = ray - cpos
            hit = None
            if d[2] < -1e-6:
                t = -cpos[2] / d[2]
                if 0 < t * np.linalg.norm(d[:2]) < max_ray_m:
                    hit = cpos + t * d
            if hit is None and depth is not None:
                i = int(np.argmin(np.abs(frames - f)))
                dm = depth[i]
                cy = min(int((box[1] + box[3]) / 2 * dm.shape[0]), dm.shape[0] - 1)
                cx = min(int((box[0] + box[2]) / 2 * dm.shape[1]), dm.shape[1] - 1)
                if dm[cy, cx] > 0:
                    hit = cpos + d * float(dm[cy, cx])
            if hit is not None:
                away = hit[:2] - cpos[:2]
                dist, min_dist = float(np.linalg.norm(away)), subj_r + 0.6
                if dist < min_dist:
                    direction = away / dist if dist > 1e-6 else _unit(d[:2])
                    hit = np.array([*(cpos[:2] + direction * min_dist), 0.0])
                subj_local.append([f / fps, float(hit[0]), float(hit[1]), 0.0])

        phi, offset = 0.0, (0.0, 0.0)
        if subj_local:
            first = subj_local[0]
            local_heading = None
            if len(subj_local) > 3:
                a, b = subj_local[0], subj_local[min(len(subj_local) - 1, 3)]
                if math.dist(a[1:3], b[1:3]) / max(b[0] - a[0], 1e-6) > 0.3:
                    local_heading = math.atan2(-(b[1] - a[1]), b[2] - a[2])
            if prev_heading is not None and local_heading is not None:
                phi = prev_heading - local_heading
            c, si = math.cos(phi), math.sin(phi)
            rx, ry = first[1] * c - first[2] * si, first[1] * si + first[2] * c
            target = prev_end or (0.0, 0.0, 0.0)
            offset = (target[0] - rx, target[1] - ry)
        c, si = math.cos(phi), math.sin(phi)
        Rz = np.array([[c, -si, 0.0], [si, c, 0.0], [0.0, 0.0, 1.0]])
        shift = np.array([offset[0], offset[1], 0.0])

        def world(p: np.ndarray, Rz=Rz, shift=shift) -> np.ndarray:
            return Rz @ p + shift

        shot_subj = [[t, *world(np.array([x, y, 0.0]))[:2], 0.0] for t, x, y, _ in subj_local]
        world_path.extend(shot_subj)
        if len(shot_subj) >= 2:
            a, b = shot_subj[-2], shot_subj[-1]
            prev_end = (b[1], b[2], 0.0)
            if math.dist(a[1:3], b[1:3]) > 0.01:
                prev_heading = math.atan2(-(b[1] - a[1]), b[2] - a[2])
        elif shot_subj:
            prev_end = (shot_subj[-1][1], shot_subj[-1][2], 0.0)

        keys, aims, rolls = [], [], []
        every = max(1, fps // 4)
        shot_cams = []
        for k, f in enumerate(range(start, end + 1)):
            m = cams[f]
            pos = world(local(m[:3, 3]))
            cams_world.append(pos)
            shot_cams.append(pos)
            if k % every and f != end:
                continue
            rot = Rz @ A @ m[:3, :3]
            fwd, right = rot[:, 2], rot[:, 0]
            t = round(f / fps, 5)
            keys.append([t, *(round(float(v), 4) for v in pos)])
            aims.append([t, *(round(float(v), 4) for v in pos + 10 * fwd)])
            rolls.append([t, round(math.degrees(math.asin(max(-1.0, min(1.0, float(right[2]))))), 3)])
        lens = float(np.median(K[:, 0, 0])) / W * 36.0
        shots_out.append({"id": shot["id"], "start_s": round(start / fps, 6), "end_s": round((end + 1) / fps, 6),
                          "lens_mm": round(min(max(lens, 8.0), 300.0), 2), "title": f"反推镜头 {shot['id']}",
                          "action": _describe_flow(shot["flow"]),
                          "camera": {"keys": keys, "interpolation": "cubic", "aim_keys": aims, "roll_keys": rolls}})
        if depth is not None:
            for i in np.linspace(0, len(rows) - 1, min(6, len(rows))).round().astype(int):
                pts, uv = _backproject(depth[i], K[i], c2w[i], (W, H), conf=None if conf is None else conf[i])
                box = boxes.get(int(frames[i]))
                if box is not None:
                    inside = (uv[:, 0] >= box[0]) & (uv[:, 0] <= box[2]) & (uv[:, 1] >= box[1]) & (uv[:, 1] <= box[3])
                    pts = pts[~inside]
                near = pts[np.linalg.norm(pts - c2w[i, :3, 3], axis=1) < 60.0 / max(s, 1e-9)][::3]
                clouds.append((s * (near - origin) @ A.T) @ Rz.T + shift)
        speed = float(np.max(np.linalg.norm(np.diff(np.array(shot_cams), axis=0), axis=1)) * fps) if len(shot_cams) > 1 else 0.0
        entry = {"shot": shot["id"], "frames": int(len(rows)), **frame["info"], "max_camera_speed_mps": round(speed, 2)}
        if speed > MAX_CAMERA_SPEED_MPS:
            entry["suspect"] = f"相机最快 {speed:.1f} m/s，超过 {MAX_CAMERA_SPEED_MPS:g} m/s：该镜位姿很可能估计失败，建议对照启发式结果或人工修正"
        shot_info.append(entry)

    shots_out[-1]["end_s"] = duration
    actors, path_pts = [], np.zeros((0, 3))
    if world_path:
        path = _smooth(_limit_speed(_median(sorted(world_path)), MAX_SOLVED_SPEED.get(kind, 7.0)))
        dedup = []
        for k in path:
            if not dedup or k[0] > dedup[-1][0] + 1e-6:
                dedup.append([round(v, 4) for v in k])
        actors.append({"id": "A", "kind": kind, "label": "主体", "role": "主体", "height_m": subj_h, "radius_m": subj_r,
                       "color": "#d9363e", "path": {"keys": dedup, "interpolation": "cubic"}})
        path_pts = np.array([[k[1], k[2], 0.0] for k in dedup])
    keepout = np.concatenate([np.array(cams_world).reshape(-1, 3), path_pts]) if cams_world else path_pts
    blocks = _occupancy_blocks(np.concatenate(clouds) if clouds else np.zeros((0, 3)), keepout)
    xs = [k[1] for k in world_path] or [0.0]
    ys = [k[2] for k in world_path] or [0.0]
    blocks.insert(0, {"id": "G00", "shape": "plane", "center": [sum(xs) / len(xs), sum(ys) / len(ys), -0.05],
                      "size": [200.0, 200.0, 0.1], "role": "ground", "collision": False, "label": "地面"})
    gravity = sorted({i["gravity"] for i in shot_info})
    scale = sorted({i["scale_from"] for i in shot_info})
    info = {"source": meta.get("source", "external"), "metric": bool(meta.get("metric")), "scale_source": scale_source,
            "shots": shot_info, "blocks_from_depth": len(blocks) - 1}
    scene = SceneSpec.model_validate({
        "schema": "wbs.scene/1.0", "id": job_id, "title": title, "fps": fps, "duration_s": duration,
        "resolution": list(resolution or (W, H)), "precision": "low", "palette": "identity", "time_map": None,
        "blocks": blocks, "actors": actors, "shots": shots_out, "events": [], "render": {"engine": "workbench"},
        "source": {"kind": "reverse", "video": video["file"], "analysis": "analysis/analysis.json",
                   "camera_track": "analysis/camera_track.npz"},
        "meta": {"solver": GEOMETRY_SOLVER.format(source=info["source"], gravity="/".join(gravity), scale="/".join(scale)),
                 "draft": True, "geometry": info,
                 "assumptions": {"camera_height_m": camera_height, "subject": subject, "subject_height_m": subj_h},
                 "environment": "story", "environment_description": "由原视频反推的体块场景（几何求解草稿）",
                 "content_class_label": "运动"},
    })
    return scene, info
