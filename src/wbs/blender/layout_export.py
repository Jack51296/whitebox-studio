"""Export the mesh layout of any .blend (e.g. an Infinigen room) as white-box blocks (runs inside Blender).

    blender -b room.blend --factory-startup -noaudio --python layout_export.py -- --out layout.json [--exclude ACTOR_,CAMERA_]

Each mesh object becomes a box: rotated about Z when the object is only yawed, otherwise its world-space
axis-aligned bounds. Sizes are in scene units (metres for Infinigen).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector


def _box(obj) -> dict:
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    rot = obj.matrix_world.to_euler("XYZ")
    yaw_only = abs(math.sin(rot.x)) < 1e-3 and abs(math.sin(rot.y)) < 1e-3
    if yaw_only:
        local = [Vector(c) for c in obj.bound_box]
        scale = obj.matrix_world.to_scale()
        size = [(max(c[i] for c in local) - min(c[i] for c in local)) * abs(scale[i]) for i in range(3)]
        center = obj.matrix_world @ (sum(local, Vector()) / 8)
        yaw = math.degrees(rot.z)
    else:
        lo = [min(c[i] for c in corners) for i in range(3)]
        hi = [max(c[i] for c in corners) for i in range(3)]
        size = [h - lo_ for lo_, h in zip(lo, hi)]
        center = Vector([(a + b) / 2 for a, b in zip(lo, hi)])
        yaw = 0.0
    return {"center": [round(v, 4) for v in center], "size": [round(max(v, 0.01), 4) for v in size],
            "rotation_deg": [0.0, 0.0, round(yaw, 3)]}


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser(prog="wbs-layout-export")
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--exclude", default="", help="comma-separated object name prefixes to skip")
    p.add_argument("--min-size", type=float, default=0.1)
    args = p.parse_args(argv)
    skip = tuple(x for x in args.exclude.split(",") if x)
    blocks = []
    for obj in sorted(bpy.context.scene.objects, key=lambda o: o.name):
        if obj.type != "MESH" or (skip and obj.name.startswith(skip)) or not obj.visible_get():
            continue
        box = _box(obj)
        if max(box["size"]) < args.min_size:
            continue
        flat = box["size"][2] < 0.12 and box["center"][2] - box["size"][2] / 2 < 0.05
        blocks.append({"id": f"LY{len(blocks):03}", "shape": "box", **box, "role": "ground" if flat else "layout",
                       "collision": not flat, "label": obj.name[:60]})
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"schema": "wbs.layout/1.0", "source": bpy.data.filepath, "units": "m",
                                    "blocks": blocks}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"WBS_LAYOUT {len(blocks)} blocks -> {args.out}")


if __name__ == "__main__":
    main()
