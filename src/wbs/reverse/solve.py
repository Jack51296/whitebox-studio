"""Analysis → draft scene.json (camera_solve + scene_blocks, [D2]). Heuristic monocular solve.

Per shot the camera starts at a local origin (height ``camera_height``) and integrates measured
background flow: horizontal flow → yaw, vertical flow → pitch, rotation → roll, scale → dolly along
the view axis (all with the measured signs). The subject is placed from its occupancy box: the
feet ray is intersected with the ground, falling back to size-based distance. Shots are chained so
the subject path stays continuous. Structure lines become thin "edge" blocks that reproduce the
line's screen position and tilt in the shot's middle frame.

Known limits (recorded in scene.meta.solver): pan vs truck is ambiguous from one view (treated as
pan); one subject; absolute scale comes from the assumed subject height and camera height.
"""

from __future__ import annotations

import math
from typing import Any

from ..models.scene import SceneSpec

SUBJECT_KINDS = {"person": ("pawn", 1.7, 0.35), "animal": ("block_animal", 1.0, 0.95),
                 "bird": ("block_bird", 0.35, 0.95), "fish": ("block_fish", 0.5, 0.65),
                 "vehicle": ("vehicle", 1.5, 2.3), "robot": ("robot", 2.0, 0.55), "product": ("prop", 0.6, 0.4)}
SOLVER = ("reverse/solve 1.1 (heuristic monocular: pan-vs-truck ambiguous → pan; single subject; scale from assumed sizes; "
          "subject path median-filtered and speed-limited)")
MAX_SOLVED_SPEED = {"pawn": 7.0, "block_animal": 12.0, "block_bird": 18.0, "block_fish": 4.0, "vehicle": 30.0,
                    "robot": 3.0, "prop": 1.0}


def _forward(yaw: float, pitch: float) -> tuple[float, float, float]:
    return (-math.sin(yaw) * math.cos(pitch), math.cos(yaw) * math.cos(pitch), math.sin(pitch))


def _ray(cam_yaw: float, cam_pitch: float, cam_roll: float, sx: float, sy: float, fx: float,
         aspect: float) -> tuple[float, float, float]:
    """World direction of the normalised screen point (sx, sy) (0..1, y down)."""
    xr = (sx - 0.5) / fx
    yu = (0.5 - sy) * aspect / fx
    c, s = math.cos(cam_roll), math.sin(cam_roll)
    xr, yu = xr * c - yu * s, xr * s + yu * c
    fwd = _forward(cam_yaw, cam_pitch)
    right = (math.cos(cam_yaw), math.sin(cam_yaw), 0.0)
    up = (right[1] * fwd[2] - right[2] * fwd[1], right[2] * fwd[0] - right[0] * fwd[2],
          right[0] * fwd[1] - right[1] * fwd[0])
    d = tuple(f + xr * r + yu * u for f, r, u in zip(fwd, right, up))
    n = math.sqrt(sum(v * v for v in d))
    return (d[0] / n, d[1] / n, d[2] / n)


def _interp_boxes(samples: dict[int, dict[str, Any]], frames: list[int]) -> dict[int, list[float]]:
    keys = sorted(samples)
    out = {}
    for f in frames:
        if not keys:
            break
        if f <= keys[0]:
            out[f] = samples[keys[0]]["bbox"]
        elif f >= keys[-1]:
            out[f] = samples[keys[-1]]["bbox"]
        else:
            j = max(k for k in keys if k <= f)
            k2 = min(k for k in keys if k >= f)
            u = 0.0 if k2 == j else (f - j) / (k2 - j)
            a, b = samples[j]["bbox"], samples[k2]["bbox"]
            out[f] = [a[i] + u * (b[i] - a[i]) for i in range(4)]
    return out


def _smooth(points: list[list[float]], radius: int = 2) -> list[list[float]]:
    out = []
    for i, p in enumerate(points):
        window = points[max(0, i - radius):i + radius + 1]
        out.append([p[0]] + [sum(q[k] for q in window) / len(window) for k in (1, 2, 3)])
    return out


def _median(points: list[list[float]], radius: int = 2) -> list[list[float]]:
    out = []
    for i, p in enumerate(points):
        window = points[max(0, i - radius):i + radius + 1]
        out.append([p[0]] + [sorted(q[k] for q in window)[len(window) // 2] for k in (1, 2, 3)])
    return out


def _limit_speed(points: list[list[float]], max_speed: float) -> list[list[float]]:
    """Clamp per-step horizontal displacement so measurement jumps cannot become teleports."""
    if not points:
        return points
    out = [list(points[0])]
    for p in points[1:]:
        q = out[-1]
        dt = max(p[0] - q[0], 1e-6)
        dx, dy = p[1] - q[1], p[2] - q[2]
        step = math.hypot(dx, dy)
        limit = max_speed * dt
        if step > limit:
            dx, dy = dx / step * limit, dy / step * limit
        out.append([p[0], q[1] + dx, q[2] + dy, p[3]])
    return out


def solve(analysis: dict[str, Any], job_id: str, title: str, subject: str = "person", lens_mm: float = 28.0,
          camera_height: float = 1.6, resolution: tuple[int, int] | None = None, max_ray_m: float = 60.0) -> SceneSpec:
    video = analysis["video"]
    fps = int(round(video["fps"]))
    W, H = resolution or (video["width"], video["height"])
    aspect = video["height"] / video["width"]
    fx = lens_mm / 36.0
    kind, subj_h, subj_r = SUBJECT_KINDS.get(subject, SUBJECT_KINDS["person"])
    duration = round(video["frames"] / fps, 6)

    world_path: list[list[float]] = []
    shots_out, blocks = [], []
    prev_end: tuple[float, float, float] | None = None
    prev_heading: float | None = None
    for shot in analysis["shots"]:
        start, end = shot["start_frame"], shot["end_frame"]
        per = {p["frame"]: p for p in shot["flow"]["per_frame"]}
        samples = {int(k): v for k, v in shot["occupancy_samples"].items()}
        frames = list(range(start, end + 1))
        boxes = _interp_boxes(samples, frames)
        yaw = pitch = roll = 0.0
        pos = [0.0, 0.0, camera_height]
        dist = None
        cams, subj_local = [], []
        for f in frames:
            p = per.get(f)
            if p and p.get("ok"):
                yaw += math.atan(p["dx"] / fx)
                pitch = max(-1.2, min(1.2, pitch + math.atan(p["dy"] * aspect / fx)))
                roll -= math.radians(p["roll_deg"])
                if dist and abs(p["scale"] - 1.0) > 0.002:
                    step = dist * (1.0 - 1.0 / p["scale"])
                    fwd = _forward(yaw, 0.0)
                    pos = [pos[0] + fwd[0] * step, pos[1] + fwd[1] * step, pos[2]]
            box = boxes.get(f)
            if box is not None:
                cx, bottom, hf = (box[0] + box[2]) / 2, box[3], max(box[3] - box[1], 1e-3)
                ray = _ray(yaw, pitch, roll, cx, bottom, fx, aspect)
                hit = None
                if ray[2] < -1e-3:
                    t = pos[2] / -ray[2]
                    if t < max_ray_m:
                        hit = (pos[0] + ray[0] * t, pos[1] + ray[1] * t, 0.0)
                if hit is None:
                    d = subj_h * fx / (aspect * hf)
                    center = _ray(yaw, pitch, roll, cx, (box[1] + box[3]) / 2, fx, aspect)
                    hit = (pos[0] + center[0] * d, pos[1] + center[1] * d, 0.0)
                dist = math.dist(hit[:2], pos[:2])
                min_dist = subj_r + 0.6
                if dist < min_dist:
                    ux, uy = (hit[0] - pos[0], hit[1] - pos[1]) if dist > 1e-6 else _forward(yaw, 0.0)[:2]
                    norm = math.hypot(ux, uy) or 1.0
                    hit = (pos[0] + ux / norm * min_dist, pos[1] + uy / norm * min_dist, 0.0)
                    dist = min_dist
                subj_local.append([f / fps, *hit])
            cams.append((f / fps, list(pos), yaw, pitch, roll))

        phi, offset = 0.0, (0.0, 0.0)
        if subj_local:
            first = subj_local[0]
            if len(subj_local) > 3:
                a, b = subj_local[0], subj_local[min(len(subj_local) - 1, 3)]
                speed = math.dist(a[1:3], b[1:3]) / max(b[0] - a[0], 1e-6)
                local_heading = math.atan2(-(b[1] - a[1]), b[2] - a[2]) if speed > 0.3 else None
            else:
                local_heading = None
            if prev_heading is not None and local_heading is not None:
                phi = prev_heading - local_heading
            c, s = math.cos(phi), math.sin(phi)
            rx, ry = first[1] * c - first[2] * s, first[1] * s + first[2] * c
            target = prev_end or (0.0, 0.0, 0.0)
            offset = (target[0] - rx, target[1] - ry)
        c, s = math.cos(phi), math.sin(phi)

        def to_world(x: float, y: float, c=c, s=s, offset=offset) -> tuple[float, float]:
            return (x * c - y * s + offset[0], x * s + y * c + offset[1])

        shot_subj = [[t, *to_world(x, y), 0.0] for t, x, y, _ in subj_local]
        world_path.extend(shot_subj)
        if len(shot_subj) >= 2:
            a, b = shot_subj[-2], shot_subj[-1]
            prev_end = (b[1], b[2], 0.0)
            if math.dist(a[1:3], b[1:3]) > 0.01:
                prev_heading = math.atan2(-(b[1] - a[1]), b[2] - a[2])
        elif shot_subj:
            prev_end = tuple(shot_subj[-1][1:])

        keys, aims, rolls = [], [], []
        sample_every = max(1, fps // 4)
        for i, (t, p, cy, cp, cr) in enumerate(cams):
            if i % sample_every and i != len(cams) - 1:
                continue
            wx, wy = to_world(p[0], p[1])
            fwd = _forward(cy + phi, cp)
            keys.append([round(t, 5), round(wx, 4), round(wy, 4), round(p[2], 4)])
            aims.append([round(t, 5), round(wx + fwd[0] * 10, 4), round(wy + fwd[1] * 10, 4), round(p[2] + fwd[2] * 10, 4)])
            rolls.append([round(t, 5), round(math.degrees(cr), 3)])
        shots_out.append({"id": shot["id"], "start_s": round(start / fps, 6), "end_s": round((end + 1) / fps, 6),
                          "lens_mm": lens_mm, "title": f"反推镜头 {shot['id']}",
                          "action": _describe_flow(shot["flow"]),
                          "camera": {"keys": keys, "interpolation": "cubic", "aim_keys": aims, "roll_keys": rolls}})
        blocks.extend(_line_blocks(shot, cams, to_world, phi, fx, aspect))

    shots_out[-1]["end_s"] = duration
    actors = []
    if world_path:
        path = _smooth(_limit_speed(_median(sorted(world_path)), MAX_SOLVED_SPEED.get(kind, 7.0)))
        dedup = []
        for k in path:
            if not dedup or k[0] > dedup[-1][0] + 1e-6:
                dedup.append([round(v, 4) for v in k])
        actors.append({"id": "A", "kind": kind, "label": "主体", "role": "主体", "height_m": subj_h, "radius_m": subj_r,
                       "color": "#d9363e", "path": {"keys": dedup, "interpolation": "cubic"}})
    xs = [k[1] for k in world_path] or [0.0]
    ys = [k[2] for k in world_path] or [0.0]
    blocks.insert(0, {"id": "G00", "shape": "plane", "center": [sum(xs) / len(xs), sum(ys) / len(ys), -0.05],
                      "size": [200.0, 200.0, 0.1], "role": "ground", "collision": False, "label": "地面"})
    return SceneSpec.model_validate({
        "schema": "wbs.scene/1.0", "id": job_id, "title": title, "fps": fps, "duration_s": duration,
        "resolution": [W, H], "precision": "low", "palette": "identity", "time_map": None,
        "blocks": blocks, "actors": actors, "shots": shots_out, "events": [], "render": {"engine": "workbench"},
        "source": {"kind": "reverse", "video": video["file"], "analysis": "analysis/analysis.json"},
        "meta": {"solver": SOLVER, "draft": True, "assumptions": {"lens_mm": lens_mm, "camera_height_m": camera_height,
                                                                 "subject": subject, "subject_height_m": subj_h},
                 "environment": "story", "environment_description": "由原视频反推的体块场景（草稿）",
                 "content_class_label": "运动"},
    })


def _describe_flow(flow: dict[str, Any]) -> str:
    parts = []
    if abs(flow["pan_x_total"]) > 0.05:
        parts.append(f"水平摇 {flow['pan_x_total']:+.2f} 画幅")
    if abs(flow["pan_y_total"]) > 0.05:
        parts.append(f"垂直摇 {flow['pan_y_total']:+.2f} 画幅")
    if abs(flow["zoom_total"] - 1) > 0.05:
        parts.append(f"推拉缩放 ×{flow['zoom_total']:.2f}")
    if abs(flow["roll_total_deg"]) > 2:
        parts.append(f"滚转 {flow['roll_total_deg']:+.1f}°")
    return "；".join(parts) or "相机基本固定"


def _ground_distance(pos: list[float], ray: tuple[float, float, float], default: float, limit: float = 60.0) -> float:
    if ray[2] < -1e-3:
        t = pos[2] / -ray[2]
        horiz = t * math.hypot(ray[0], ray[1])
        if horiz < limit:
            return horiz
    return default


def _line_blocks(shot: dict[str, Any], cams: list, to_world, phi: float, fx: float, aspect: float,
                 distance: float = 15.0, min_len: float = 0.35) -> list[dict[str, Any]]:
    """Grounded blocks reproducing each long dominant line of the middle frame.

    Near-vertical lines become pillars / building corners standing where the lower end's ray meets
    the ground; tilted lines become beams whose lower end rests on the ground.
    """
    mid_index = len(cams) // 2
    _, pos, yaw, pitch, roll = cams[mid_index]
    out = []
    for n, line in enumerate(shot["lines"]["mid"]):
        if line["length"] < min_len or abs(line["angle_deg"]) < 8:
            continue
        lower, upper = sorted((line["p0"], line["p1"]), key=lambda p: -p[1])
        d_low = _ray(yaw, pitch, roll, lower[0], lower[1], fx, aspect)
        dist = _ground_distance(pos, d_low, distance)
        if abs(line["angle_deg"]) >= 60:
            horiz_low = math.hypot(d_low[0], d_low[1]) or 1e-6
            bx, by = pos[0] + d_low[0] / horiz_low * dist, pos[1] + d_low[1] / horiz_low * dist
            d_up = _ray(yaw, pitch, roll, upper[0], upper[1], fx, aspect)
            horiz_up = math.hypot(d_up[0], d_up[1]) or 1e-6
            top = max(1.0, pos[2] + d_up[2] / horiz_up * dist)
            wx, wy = to_world(bx, by)
            out.append({"id": f"L_{shot['id']}_{n}", "shape": "box", "center": [round(wx, 3), round(wy, 3), round(top / 2, 3)],
                        "size": [0.8, 0.8, round(top, 3)], "rotation_deg": [0.0, 0.0, round(math.degrees(yaw + phi), 2)],
                        "role": "structure_line", "collision": True, "label": f"结构线推断（竖直边）{line['angle_deg']:+.0f}°"})
            continue
        pts = []
        for sx, sy in (lower, upper):
            d = _ray(yaw, pitch, roll, sx, sy, fx, aspect)
            horiz = math.hypot(d[0], d[1]) or 1e-6
            x, y = to_world(pos[0] + d[0] / horiz * dist, pos[1] + d[1] / horiz * dist)
            pts.append((x, y, max(0.05, pos[2] + d[2] / horiz * dist)))
        (x0, y0, z0), (x1, y1, z1) = pts
        length = math.dist(pts[0], pts[1])
        if length < 0.5:
            continue
        horiz = math.hypot(x1 - x0, y1 - y0)
        yaw_deg = math.degrees(math.atan2(-(x1 - x0), y1 - y0)) if horiz > 1e-6 else 0.0
        tilt = math.degrees(math.atan2(z1 - z0, horiz))
        out.append({"id": f"L_{shot['id']}_{n}", "shape": "box",
                    "center": [round((x0 + x1) / 2, 3), round((y0 + y1) / 2, 3), round((z0 + z1) / 2, 3)],
                    "size": [0.3, round(length, 3), 0.3], "rotation_deg": [round(tilt, 2), 0.0, round(yaw_deg, 2)],
                    "role": "structure_line", "collision": False, "label": f"结构线推断 {line['angle_deg']:+.0f}°"})
    return out
