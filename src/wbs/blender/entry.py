"""Blender entry point.

    blender -b --factory-startup -noaudio --python-exit-code 1 --python entry.py -- --scene scene.json --out render/ ...
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def parse(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="wbs-blender")
    p.add_argument("--scene", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path, help="render directory; frames go to <out>/frames")
    p.add_argument("--audit", type=Path)
    p.add_argument("--audit-step", type=int, default=1)
    p.add_argument("--blend", type=Path)
    p.add_argument("--engine", default="workbench", choices=("workbench", "eevee"))
    p.add_argument("--frames", default="all", help="'all' or comma-separated frame numbers")
    p.add_argument("--no-render", action="store_true")
    p.add_argument("--fbx-library")
    p.add_argument("--status", type=Path)
    p.add_argument("--passes", default="", help="control passes after the render: comma-separated seg,depth")
    return p.parse_args(argv)


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    args = parse(argv)
    import audit
    import bpy  # noqa: F401  (fail fast outside Blender)
    import build
    import render_ops

    status: dict = {"started_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "blender_version": bpy.app.version_string}
    t0 = time.time()
    scene = json.loads(args.scene.read_text(encoding="utf-8-sig"))
    handles = build.build_scene(scene, args.fbx_library)
    status["render"] = render_ops.configure(args.engine, tuple(scene.get("render", {}).get("background", (0.82,) * 3)))
    status["build_s"] = round(time.time() - t0, 2)
    if args.audit:
        t1 = time.time()
        status["audit"] = audit.run(scene, handles, args.audit, step=max(1, args.audit_step))
        status["audit_s"] = round(time.time() - t1, 2)
    if args.blend:
        args.blend.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(args.blend), compress=True)
        status["blend"] = str(args.blend)
    if not args.no_render:
        t2 = time.time()
        frames = None if args.frames == "all" else [int(x) for x in args.frames.split(",") if x.strip()]
        status["frames_rendered"] = render_ops.render_frames(args.out / "frames", frames)
        status["render_s"] = round(time.time() - t2, 2)
    if args.passes:
        import passes

        t3 = time.time()
        frames = None if args.frames == "all" else [int(x) for x in args.frames.split(",") if x.strip()]
        status["passes"] = passes.render_passes(scene, args.out / "passes",
                                                [p for p in args.passes.split(",") if p.strip()], frames)
        status["passes_s"] = round(time.time() - t3, 2)
    status["total_s"] = round(time.time() - t0, 2)
    status["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    if args.status:
        args.status.parent.mkdir(parents=True, exist_ok=True)
        args.status.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    print("WBS_STATUS " + json.dumps(status, ensure_ascii=False))


if __name__ == "__main__":
    main()
