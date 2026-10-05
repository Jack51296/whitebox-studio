from __future__ import annotations

import pytest
from pydantic import ValidationError

from wbs.config import Spec, load_settings
from wbs.forward.story import story_to_scene
from wbs.models.story import StoryPlan
from wbs.qc.dynamic import dynamic_gate, framing_precheck
from wbs.qc.grammar import declared_vs_measured

RIGS = [("static", "wide", "eye", {}), ("pan", "wide", "eye", {}), ("follow", "medium", "", {"handheld": 0.4}),
        ("lead", "medium", "eye", {}), ("side_track", "wide", "eye", {}), ("push", "medium", "eye", {}),
        ("pull", "wide", "eye", {}), ("crane", "wide", "high", {"rise_m": 6}), ("orbit", "medium", "", {"arc_deg": 120}),
        ("top_down", "wide", "overhead", {}), ("mount", "", "", {}), ("pov", "", "", {})]
GROUND = {"id": "G00", "shape": "plane", "center": [0, 100, -0.05], "size": [300, 500, 0.1], "role": "ground",
          "collision": False}


def _plan(shots, blocks=(GROUND,)):
    events = [{"id": "E01", "at_s": 1.0, "choice": "起步", "consequence": "出发"},
              {"id": "E02", "at_s": 8.0, "choice": "转弯", "consequence": "绕开"},
              {"id": "E03", "at_s": 20.0, "choice": "抵达", "consequence": "结束"}]
    duration = shots[-1]["end_s"]
    return StoryPlan.model_validate({
        "title": "rigs", "logline": "l", "content_class": "motion", "duration_s": duration, "world": "w", "story": "s",
        "goal": "g", "obstacle": "o", "stakes": "k", "ending": "e",
        "characters": [{"id": "A", "role": "车", "color": "#d9363e", "kind": "vehicle", "height_m": 1.4}],
        "routes": {"A": {"waypoints": [{"x": 0, "y": 0}, {"x": 0, "y": 150}, {"x": 60, "y": 200}, {"x": 60, "y": 320}],
                         "start_moving": True}},
        "blocks": list(blocks), "events": [e for e in events if e["at_s"] < duration], "twist": 1, "setup": [0],
        "shots": shots})


def _shots(rigs):
    return [{"id": f"S{i + 1:02}", "start_s": i * 2.0, "end_s": (i + 1) * 2.0, "title": kind, "action": kind,
             "framing": framing, "angle": angle, "rig": {"type": kind, "target": "A", **extra}}
            for i, (kind, framing, angle, extra) in enumerate(rigs)]


def test_every_rig_compiles_clear_of_obstacles_and_keeps_the_subject_in_frame():
    settings = load_settings()
    scene = story_to_scene(_plan(_shots(RIGS)), Spec(duration_s=24, fps=24, resolution=(1280, 720)), "R").to_json_dict()
    assert dynamic_gate(scene, settings.qc.dynamic_gate)["status"] == "passed"
    assert framing_precheck(scene, cfg=settings.qc.framing)["status"] == "passed"
    moves = [s["move"] for s in scene["shots"]]
    assert moves == [r[0] for r in RIGS]
    follow = scene["shots"][2]["camera"]
    assert follow["responses"] and follow["aim_actor"] == "A"
    assert scene["shots"][-1]["camera"]["aim_keys"] and set(scene["meta"]["rigs"]) == {s["id"] for s in scene["shots"]}
    medium = [r for r in declared_vs_measured(scene, framing_precheck(scene)) if r["declared"]["framing"] == "medium"]
    assert medium and all(r["measured"]["framing"] == "medium" for r in medium)


def test_rig_is_pushed_out_of_a_building():
    wall = {"id": "B1", "shape": "box", "center": [14, 20, 8], "size": [8, 40, 16], "role": "building"}
    shots = _shots([("side_track", "wide", "eye", {"side": "right"})]) + \
        [{"id": "S02", "start_s": 2.0, "end_s": 21.0, "title": "t", "action": "a", "rig": {"type": "follow"}}]
    scene = story_to_scene(_plan(shots, (GROUND, wall)), Spec(duration_s=21, fps=24, resolution=(1280, 720)),
                           "R").to_json_dict()
    assert scene["meta"]["rigs"]["S01"]["placement"] != "as_planned"
    counts = dynamic_gate(scene, load_settings().qc.dynamic_gate)["issue_counts"]
    assert counts.get("camera_block_clearance", 0) == 0 and counts.get("camera_jump", 0) == 0


def test_a_shot_needs_camera_keys_or_a_rig():
    shot = {"id": "S01", "start_s": 0, "end_s": 24, "title": "t", "action": "a"}
    with pytest.raises(ValidationError, match="camera_keys or a rig"):
        _plan([shot])
