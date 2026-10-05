from __future__ import annotations

import dataclasses
import math

import cv2
import pytest
from conftest import requires_blender, requires_ffmpeg

from wbs.blender.kinematics import SceneEvaluator
from wbs.config import Spec, load_settings
from wbs.control_passes import render_passes
from wbs.forward.layout import export_blend_layout, import_layout
from wbs.forward.procedural import generate
from wbs.jsonio import read_json, write_json
from wbs.layout import Workspace
from wbs.media.ffmpeg import probe
from wbs.render import render_job, run_blender
from wbs.taxonomy import sample_controls


@pytest.mark.blender
@requires_blender
@requires_ffmpeg
def test_small_render_matches_scene_kinematics(workspace):
    settings = load_settings()
    ws = Workspace(settings.workspace).ensure()
    control = dataclasses.replace(sample_controls(1, seed=21, subjects=["person"])[0], subject_child="duo",
                                  shot_form="multi_shot", shot_count=2, camera_move="follow", color="identity")
    spec = Spec(duration_s=2.0, fps=24, resolution=(320, 180))
    job = ws.job("T", "T_0001")
    job.root.mkdir(parents=True)
    scene = generate(control, spec, "T_0001").to_json_dict()
    write_json(job.scene, scene)
    job.update_meta(title=scene["title"])

    out = render_job(job, settings, save_blend=False)

    info = probe(job.video(scene["title"]), count_frames=True)
    assert (info["width"], info["height"], info["frames"], info["audio_streams"]) == (320, 180, 48, 0)
    samples = read_json(job.samples)["samples"]
    assert len(samples) == 48
    ev = SceneEvaluator(scene)
    for row in samples:
        t = ev.frame_time(row["frame"])
        assert row["shot"] == scene["shots"][ev.shot_index(t)]["id"]
        for aid, actor in row["actors"].items():
            assert math.dist(actor["pos"], ev.actor_state(aid, t)[0]) < 1e-3
        assert math.dist(row["camera"]["pos"], ev.camera_state(t)["pos"]) < 1e-3
    assert out["storyboard"] >= 2
    assert job.overview.exists() and job.director_card.exists() and job.vtt.exists()
    assert read_json(job.report("导演卡对照.json"))["status"] == "informational"

    report = render_passes(job, settings, ["seg", "depth", "edge"])
    folder = job.root / "控制通道"
    for name in ("seg", "depth", "edge"):
        video = folder / report["passes"][name]["video"]
        assert probe(video, count_frames=True)["frames"] == 48
    legend = read_json(folder / "seg_legend.json")["objects"]
    assert any(v["class"].startswith("actor:") for v in legend.values())
    assert any(v["class"] == "block:ground" for v in legend.values())
    depth = cv2.VideoCapture(str(folder / report["passes"]["depth"]["video"]))
    ok, frame = depth.read()
    depth.release()
    assert ok and frame.max() > 60 and frame.min() < 20, "depth pass must vary between near objects and the sky"


@pytest.mark.blender
@requires_blender
def test_blend_layout_round_trip_and_import(workspace):
    settings = load_settings()
    ws = Workspace(settings.workspace).ensure()
    control = dataclasses.replace(sample_controls(1, seed=5, subjects=["person"])[0], subject_child="single",
                                  shot_form="multi_shot", shot_count=2, camera_move="follow", color="identity")
    job = ws.job("L", "L_0001")
    job.root.mkdir(parents=True)
    scene = generate(control, Spec(duration_s=2.0, fps=24, resolution=(320, 180)), "L_0001").to_json_dict()
    write_json(job.scene, scene)
    job.update_meta(title=scene["title"])
    run_blender(job, settings, render=False, audit=False, save_blend=True)
    layout = export_blend_layout(job.blend(), job.root / "layout.json", exclude="ACTOR_,CAMERA_")
    by_label = {b["label"]: b for b in layout["blocks"]}
    boxes = [b for b in scene["blocks"] if b["shape"] == "box" and not any(b.get("rotation_deg", [0, 0, 0])[:2])]
    assert boxes
    for b in boxes:
        got = by_label[f"BLOCK_{b['id']}"]
        assert math.dist(got["center"], b["center"]) < 1e-2 and math.dist(got["size"], b["size"]) < 1e-2
    assert any(b["role"] == "ground" for b in layout["blocks"])
    report = import_layout(job, settings, job.root / "layout.json")
    assert report["imported_blocks"] == sum(b["role"] != "ground" for b in layout["blocks"])
    assert report["dynamic_gate"] in ("passed", "failed") and (job.root / "versions").exists()
