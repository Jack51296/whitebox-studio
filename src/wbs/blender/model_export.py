"""Export a built white-box .blend as interchange models (runs inside Blender).

Usage: blender -b <scene.blend> --python model_export.py -- --out <dir> --stem <name> --formats fbx,glb --status <json>

FBX: metric units, animation baked per frame, only animated channels keyed (frame f lands at f/fps seconds;
Blender's own FBX importer adds a one-frame offset unless its Animation Offset is 0). GLB: the whole scene as one
animation clip, so a viewer's play button runs all cars/actors and cameras together; cameras keep their field of
view (Blender's glTF importer shows a different focal length because it sets a vertical sensor fit).
Timeline markers (camera switching) do not travel in either format; the host writes 镜头切换.csv next to them.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import bpy


def _call(op, **kwargs):
    """Run an exporter, dropping keywords this Blender version does not know."""
    dropped = []
    while True:
        try:
            op(**kwargs)
            return dropped
        except TypeError as exc:
            match = re.search(r'keyword "(\w+)" unrecognized', str(exc))
            if not match or match.group(1) not in kwargs:
                raise
            dropped.append(match.group(1))
            kwargs.pop(match.group(1))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--stem", required=True)
    parser.add_argument("--formats", default="fbx,glb")
    parser.add_argument("--status", required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    formats = [f.strip().lower() for f in args.formats.split(",") if f.strip()]
    files, dropped = {}, {}
    if "fbx" in formats:
        path = out / f"{args.stem}.fbx"
        dropped["fbx"] = _call(bpy.ops.export_scene.fbx, filepath=str(path), use_selection=False,
                               object_types={"MESH", "CAMERA", "EMPTY"}, apply_scale_options="FBX_SCALE_UNITS",
                               add_leaf_bones=False, bake_anim=True, bake_anim_use_all_actions=False,
                               bake_anim_use_nla_strips=False, bake_anim_force_startend_keying=False,
                               bake_anim_step=1.0, bake_anim_simplify_factor=0.0)
        files["fbx"] = path.name
    if "glb" in formats:
        path = out / f"{args.stem}.glb"
        dropped["glb"] = _call(bpy.ops.export_scene.gltf, filepath=str(path), export_format="GLB", export_cameras=True,
                               export_animations=True, export_animation_mode="SCENE",
                               export_anim_scene_split_object=False, export_force_sampling=True,
                               export_frame_range=True, export_apply=True)
        files["glb"] = path.name
    scene = bpy.context.scene
    status = {"schema": "wbs.model_export/1.0", "blender_version": bpy.app.version_string, "files": files,
              "dropped_options": dropped, "frames": [scene.frame_start, scene.frame_end], "fps": scene.render.fps,
              "objects": len(bpy.data.objects), "cameras": sum(o.type == "CAMERA" for o in bpy.data.objects)}
    Path(args.status).write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
