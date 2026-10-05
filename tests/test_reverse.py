from __future__ import annotations

import dataclasses
import math

import cv2
import numpy as np
import pytest
from conftest import requires_blender, requires_ffmpeg

from wbs.config import Spec, load_settings
from wbs.forward.procedural import generate
from wbs.jsonio import read_json, write_json
from wbs.layout import Workspace
from wbs.ledger import Ledger
from wbs.providers import get_providers
from wbs.render import render_job
from wbs.reverse import refine
from wbs.reverse.cuts import detect_cuts
from wbs.reverse.flow import pair_motion
from wbs.reverse.lines import structure_lines
from wbs.reverse.occupancy import foreground_box
from wbs.reverse.pipeline import intake, run_analysis, solve_and_gate
from wbs.taxonomy import sample_controls
from wbs.tools import find_tool

RNG = np.random.default_rng(0)
TEXTURE = cv2.GaussianBlur(RNG.integers(0, 255, (400, 600), dtype=np.uint8), (5, 5), 0)


def _crop(dx: float = 0.0, dy: float = 0.0, scale: float = 1.0, angle: float = 0.0) -> np.ndarray:
    m = cv2.getRotationMatrix2D((300, 200), angle, scale)
    m[:, 2] += (dx, dy)
    return cv2.warpAffine(TEXTURE, m, (600, 400))[100:280, 140:460]


def test_flow_signs_follow_documented_convention():
    base = _crop()
    right = pair_motion(base, _crop(dx=3))
    assert right["ok"] and right["dx"] > 0.005 and abs(right["dy"]) < 0.003
    down = pair_motion(base, _crop(dy=3))
    assert down["dy"] > 0.01
    zoom = pair_motion(base, _crop(scale=1.03))
    assert zoom["scale"] > 1.02
    ccw = pair_motion(base, _crop(angle=2.0))
    assert ccw["roll_deg"] > 1.5


def _smooth_texture(seed: int) -> np.ndarray:
    noise = np.random.default_rng(seed).integers(0, 255, (180, 640)).astype(np.float32)
    return cv2.normalize(cv2.GaussianBlur(noise, (0, 0), 8), None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)


def test_cuts_are_isolated_spikes_not_fast_motion():
    a, b = _smooth_texture(1), _smooth_texture(2)
    frames = [np.roll(a, 4 * i, axis=1)[:, :320] for i in range(30)]
    frames += [np.roll(b, 4 * i, axis=1)[:, :320] for i in range(30)]
    cuts, _ = detect_cuts(np.stack(frames), fps=24)
    assert cuts == [30]


def test_structure_line_angle():
    img = np.full((180, 320), 200, np.uint8)
    cv2.line(img, (20, 160), (300, 160 - int(280 * math.tan(math.radians(30)))), 30, 3)
    lines = structure_lines(img)
    assert lines and abs(lines[0]["angle_deg"] - 30) < 2


def test_occupancy_finds_moving_block_over_static_background():
    bg = _crop()
    prev, cur = bg.copy(), bg.copy()
    cv2.rectangle(prev, (100, 60), (140, 140), 0, -1)
    cv2.rectangle(cur, (106, 60), (146, 140), 0, -1)
    box = foreground_box(prev, cur)
    assert box is not None
    x0, y0, x1, y1 = box["bbox"]
    assert 0.25 < x0 < 0.4 and 0.4 < x1 < 0.5 and y1 > 0.7


def test_refine_mirror_negates_x():
    scene = {"shots": [{"id": "S01", "start_s": 0, "end_s": 1, "camera": {"keys": [[0, 1, 2, 3]], "aim_keys": [[0, 4, 5, 6]],
                                                                             "roll_keys": [[0, 5]]}}],
             "actors": [], "blocks": [{"id": "B", "center": [2, 0, 0], "size": [1, 1, 1], "rotation_deg": [0, 0, 30]}],
             "schema": "wbs.scene/1.0", "id": "x", "title": "t", "fps": 24, "duration_s": 1.0, "resolution": [32, 18]}
    out = refine.apply(scene, {"mirror_x": True})
    assert out["shots"][0]["camera"]["keys"][0][1] == -1
    assert out["shots"][0]["camera"]["roll_keys"][0][1] == -5
    assert out["blocks"][0]["center"][0] == -2 and out["blocks"][0]["rotation_deg"][2] == -30


@pytest.mark.blender
@requires_blender
@requires_ffmpeg
def test_reverse_end_to_end_on_own_render(workspace):
    settings = load_settings()
    ws = Workspace(settings.workspace).ensure()
    control = dataclasses.replace(sample_controls(1, seed=4, subjects=["person"])[0], subject_child="single",
                                  shot_form="multi_shot", shot_count=2, camera_move="truck", color="identity")
    src = ws.job("T", "SRC")
    src.root.mkdir(parents=True)
    scene = generate(control, Spec(duration_s=4.0, fps=24, resolution=(480, 270)), "SRC").to_json_dict()
    write_json(src.scene, scene)
    src.update_meta(title="源")
    render_job(src, settings, save_blend=False)
    true_cut = round(scene["shots"][1]["start_s"] * 24) + 1

    job = ws.job("T", "REV")
    intake(job, src.video(scene["title"]), "自有生成（测试）")
    providers = get_providers(settings, Ledger(ws.ledger_path))
    analysis = run_analysis(job, providers)
    assert analysis["cuts"]["frames_1based"] == [true_cut]
    assert analysis["annotation"]["simulated"] is True
    result = solve_and_gate(job, settings, providers, max_refine=1)
    assert result["static_gate"] in ("passed", "failed")
    gate = read_json(job.report("静态闸门.json"))
    assert set(gate["checks"]) == {"layout", "structure_lines", "mirror", "occupancy", "interpenetration"}
    assert list((job.reports_dir / "static_gate").glob("S01_*.jpg"))

    if find_tool("face_model"):
        from wbs.reverse.online import build_package

        render_job(job, settings, save_blend=False)
        manifest = build_package(job, settings, job.root / job.read_meta()["source_video"])
        assert manifest["status"] == "export_only" and manifest["uploaded"] is False
        assert manifest["face_blur"]["manual_check_required"] is True
