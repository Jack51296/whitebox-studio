"""Per-frame audit sampled from the built Blender scene (runs inside Blender).

Writes the evidence the Python QC reads: actual actor/camera transforms and projected screen boxes
for every frame (samples.json) and actual block bounds (blocks.json).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector


def _screen_box(scene, cam, corners: list[Vector]) -> dict:
    pts = [world_to_camera_view(scene, cam, c) for c in corners]
    front = [p for p in pts if p.z > 0.0]
    if not front:
        return {"visible": False, "bbox": None, "area": 0.0, "depth": None, "center": None}
    x0 = max(0.0, min(p.x for p in front))
    x1 = min(1.0, max(p.x for p in front))
    y0 = max(0.0, min(p.y for p in front))
    y1 = min(1.0, max(p.y for p in front))
    visible = x1 > x0 and y1 > y0
    return {"visible": visible, "bbox": [round(x0, 4), round(1 - y1, 4), round(x1, 4), round(1 - y0, 4)] if visible else None,
            "area": round((x1 - x0) * (y1 - y0), 5) if visible else 0.0,
            "depth": round(min(p.z for p in front), 3),
            "center": [round((x0 + x1) / 2, 4), round(1 - (y0 + y1) / 2, 4)] if visible else None,
            "partial": len(front) < len(pts)}


def _occluded_fraction(scene, depsgraph, cam, parts: list, own: set[str]) -> float:
    """Share of in-frame sample points on the actor whose line of sight from the camera hits another object first."""
    origin = cam.matrix_world.translation
    points = []
    for part in parts:
        corners = [part.matrix_world @ Vector(c) for c in part.bound_box]
        center = sum(corners, Vector((0.0, 0.0, 0.0))) / len(corners)
        points += [center] + [center + (c - center) * 0.85 for c in corners]
    in_frame = []
    for p in points:
        v = world_to_camera_view(scene, cam, p)
        if v.z > 0.0 and 0.0 <= v.x <= 1.0 and 0.0 <= v.y <= 1.0:
            in_frame.append(p)
    if not in_frame:
        return 1.0
    blocked = 0
    for p in in_frame:
        ray = p - origin
        distance = ray.length
        if distance < 1e-6:
            continue
        hit, _, _, _, obj, _ = scene.ray_cast(depsgraph, origin, ray.normalized(), distance=max(distance - 0.02, 0.0))
        if hit and obj is not None and obj.name not in own:
            blocked += 1
    return round(blocked / len(in_frame), 4)


def run(scene_spec: dict, handles: dict, out_dir: Path, step: int = 1) -> dict:
    scene = bpy.context.scene
    ev = handles["evaluator"]
    cams = handles["cameras"]
    out_dir.mkdir(parents=True, exist_ok=True)
    samples = []
    for f in range(scene.frame_start, scene.frame_end + 1, step):
        scene.frame_set(f)
        t = ev.frame_time(f)
        idx = ev.shot_index(t)
        cam = cams[idx]
        mw = cam.matrix_world
        forward = (mw.to_3x3() @ Vector((0.0, 0.0, -1.0))).normalized()
        row = {"frame": f, "time": round(t, 5), "shot": scene_spec["shots"][idx]["id"],
               "active_camera": scene.camera.name if scene.camera else None,
               "camera": {"name": cam.name, "pos": [round(v, 4) for v in mw.translation],
                          "forward": [round(v, 5) for v in forward], "lens_mm": round(cam.data.lens, 3),
                          "pitch_deg": round(math.degrees(math.asin(max(-1.0, min(1.0, forward.z)))), 3),
                          "heading_deg": round(math.degrees(math.atan2(-forward.x, forward.y)), 3)},
               "actors": {}}
        depsgraph = bpy.context.evaluated_depsgraph_get()
        for aid, handle in handles["actors"].items():
            root = handle["root"]
            corners = [part.matrix_world @ Vector(c) for part in handle["parts"] for c in part.bound_box]
            box = _screen_box(scene, cam, corners)
            loc = root.matrix_world.translation
            own = {root.name} | {part.name for part in handle["parts"]}
            occluded = _occluded_fraction(scene, depsgraph, cam, handle["parts"], own) if box["visible"] else 1.0
            row["actors"][aid] = {"pos": [round(v, 4) for v in loc],
                                  "yaw_deg": round(math.degrees(root.matrix_world.to_euler("XYZ").z), 3),
                                  "distance_m": round((loc - mw.translation).length, 3), **box,
                                  "occluded_fraction": occluded}
        samples.append(row)
    w, h = scene.render.resolution_x, scene.render.resolution_y
    doc = {"schema": "wbs.audit.samples/1.0", "source": "blender", "blender_version": bpy.app.version_string,
           "fps": scene.render.fps, "frames": scene.frame_end, "resolution": [w, h], "step": step, "samples": samples}
    (out_dir / "samples.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    blocks = []
    for bid, obj in handles["blocks"].items():
        corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
        lo = [round(min(c[i] for c in corners), 4) for i in range(3)]
        hi = [round(max(c[i] for c in corners), 4) for i in range(3)]
        blocks.append({"id": bid, "aabb": [lo, hi]})
    (out_dir / "blocks.json").write_text(json.dumps({"schema": "wbs.audit.blocks/1.0", "blocks": blocks},
                                                    ensure_ascii=False), encoding="utf-8")
    return {"samples": len(samples), "blocks": len(blocks)}
