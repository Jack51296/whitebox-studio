from __future__ import annotations

import csv
import dataclasses
import json
import math
import subprocess
import zipfile

import pytest
from conftest import requires_blender

from wbs.config import Spec, load_settings
from wbs.export import CUT_HEADER, CUT_LIST, camera_cuts, export_models
from wbs.forward.procedural import generate
from wbs.jsonio import write_json
from wbs.layout import Workspace, safe_filename
from wbs.ledger import Ledger
from wbs.qc.finish import finish_batch
from wbs.render import run_blender
from wbs.taxonomy import sample_controls
from wbs.tools import require_tool

FRAMES = (1, 12, 24, 36, 48)
REIMPORT = """
import json, sys
from pathlib import Path
import bpy
folder, stem, frames = Path(sys.argv[-3]), sys.argv[-2], [int(f) for f in sys.argv[-1].split(",")]
def positions():
    out = {}
    for f in frames:
        bpy.context.scene.frame_set(f)
        for o in bpy.data.objects:
            name = o.name.split(".")[0]
            if name.startswith(("ACTOR_", "CAMERA_")):
                out[f"{name}@{f}"] = list(o.matrix_world.translation)
    return out
result = {"source": positions()}
for fmt in ("fbx", "glb"):
    bpy.ops.wm.read_homefile(use_empty=True)
    path = str(folder / f"{stem}.{fmt}")
    if fmt == "fbx":
        bpy.ops.import_scene.fbx(filepath=path, anim_offset=0.0)
    else:
        bpy.ops.import_scene.gltf(filepath=path)
    result[fmt] = positions()
print("RESULT " + json.dumps(result))
"""


def _scene(seed: int = 5) -> dict:
    control = dataclasses.replace(sample_controls(1, seed=seed, subjects=["person"])[0], subject_child="single",
                                  shot_form="multi_shot", shot_count=2, camera_move="follow", color="identity")
    return generate(control, Spec(duration_s=2.0, fps=24, resolution=(320, 180)), "M_0001").to_json_dict()


def test_camera_cuts_cover_the_timeline_in_file_frames():
    scene = _scene()
    rows = camera_cuts(scene)
    assert [r[0] for r in rows] == [s["id"] for s in scene["shots"]]
    assert rows[0][2] == 1 and rows[-1][3] == 48
    assert all(nxt[2] == cur[3] + 1 for cur, nxt in zip(rows, rows[1:]))
    assert all(r[1] == f"CAMERA_{r[0]}" and r[7] for r in rows)


@pytest.mark.blender
@requires_blender
def test_model_export_round_trips_motion_and_never_overwrites(workspace, tmp_path):
    settings = load_settings()
    ws = Workspace(settings.workspace).ensure()
    job = ws.job("M", "M_0001")
    job.root.mkdir(parents=True)
    scene = _scene()
    write_json(job.scene, scene)
    job.update_meta(title=scene["title"])
    run_blender(job, settings, render=False, audit=False, save_blend=True)

    result = export_models(job, settings)
    folder = job.root / result["folder"]
    stem = safe_filename(scene["title"], job.job_id)
    assert result["folder"] == "模型导出" and set(result["files"]) == {"fbx", "glb"}
    assert (folder / f"{stem}.fbx").stat().st_size > 0 and (folder / f"{stem}.glb").stat().st_size > 0
    with (folder / CUT_LIST).open(encoding="utf-8-sig") as stream:
        rows = list(csv.reader(stream))
    assert rows[0] == CUT_HEADER and len(rows) == len(scene["shots"]) + 1
    assert job.read_meta()["model_export"]["folder"] == "模型导出"

    script = tmp_path / "reimport.py"
    script.write_text(REIMPORT, encoding="utf-8")
    proc = subprocess.run([require_tool("blender"), "-b", str(job.blend(scene["title"])), "--factory-startup",
                           "-noaudio", "--python-exit-code", "1", "--python", str(script), "--", str(folder), stem,
                           ",".join(map(str, FRAMES))],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    assert proc.returncode == 0, proc.stdout[-800:] + proc.stderr[-800:]
    data = json.loads(next(line for line in proc.stdout.splitlines() if line.startswith("RESULT "))[7:])
    source = data["source"]
    assert any(k.startswith("ACTOR_") for k in source) and any(k.startswith("CAMERA_") for k in source)
    for fmt in ("fbx", "glb"):
        got = data[fmt]
        assert set(source) <= set(got), f"{fmt} lost objects: {sorted(set(source) - set(got))[:5]}"
        worst = max(math.dist(source[k], got[k]) for k in source)
        assert worst < 0.01, f"{fmt} re-import drifts {worst:.3f} m from the .blend"

    again = export_models(job, settings, ("glb",))
    assert again["folder"].startswith("模型导出_") and set(again["files"]) == {"glb"}
    assert (folder / f"{stem}.fbx").exists(), "the first export must stay untouched"

    finished = finish_batch(ws, Ledger(ws.ledger_path), "M", export_models=True, settings=settings)
    assert finished["model_exports"]["M_0001"].startswith("模型导出_")
    with zipfile.ZipFile(finished["zip"]) as zf:
        names = zf.namelist()
    assert any(n.endswith(f"模型导出/{stem}.fbx") for n in names) and any(n.endswith(CUT_LIST) for n in names)
    assert not any(n.endswith(".blend") for n in names)
