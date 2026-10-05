from __future__ import annotations

import pytest
from pydantic import ValidationError

from wbs import camera_language as CL
from wbs.config import Spec
from wbs.forward import texts
from wbs.forward.story import story_to_scene
from wbs.models.story import StoryPlan
from wbs.qc.dynamic import framing_precheck
from wbs.qc.grammar import declared_vs_measured

EVENTS3 = [{"id": "E01", "at_s": 0.5, "choice": "看", "consequence": "明确"},
           {"id": "E02", "at_s": 1.5, "choice": "转", "consequence": "改道"},
           {"id": "E03", "at_s": 2.5, "choice": "到", "consequence": "结束"}]


def _plan(shot: dict):
    return {"title": "t", "logline": "l", "content_class": "narrative", "duration_s": 3.0, "world": "w", "story": "s",
            "goal": "g", "obstacle": "o", "stakes": "k", "ending": "e",
            "characters": [{"id": "A", "role": "主角", "color": "#d9363e"}],
            "paths": {"A": [[0, 0, 0, 0], [3, 0, 3, 0]]}, "events": EVENTS3, "twist": 1, "setup": [0],
            "shots": [{"id": "S01", "start_s": 0, "end_s": 3.0, "title": "a", "action": "走", "aim_actor": "A",
                       "camera_keys": [[0, 0, -4, 1.6]], **shot}]}


def test_vocabulary_covers_existing_and_new_moves():
    for move in ("push", "pull", "pan", "truck", "fpv", "follow", "focus_shift", "top_down", "one_take", "handheld", "pov",
                 "static", "tilt", "crane", "orbit", "lead", "side_track", "mount"):
        assert move in CL.MOVES and CL.label("move", move)
    assert CL.framing_of(0.3, "pawn") == "wide" and CL.framing_of(0.3, "vehicle") == "wide"
    assert CL.framing_of(1.0, "pawn") == "medium" and CL.framing_of(1.0, "vehicle") == "medium"
    assert CL.angle_of(-30) == "high" and CL.angle_of(0, roll_deg=8) == "dutch"


def test_story_shots_carry_camera_language_into_scene_and_card():
    plan = StoryPlan.model_validate(_plan({"framing": "medium", "angle": "eye", "move": "follow"}))
    scene = story_to_scene(plan, Spec(duration_s=3, fps=24, resolution=(320, 180)), "X").to_json_dict()
    shot = scene["shots"][0]
    assert (shot["framing"], shot["angle"], shot["move"]) == ("medium", "eye", "follow")
    card = texts.director_card(scene)
    assert "中景·平视·跟随" in card and "Blender 机位对象 CAMERA_S01" in card and "CAMERA_S01_position" not in card
    with pytest.raises(ValidationError):
        StoryPlan.model_validate(_plan({"framing": "cowboy_shot"}))


def test_procedural_labels_match_what_the_camera_frames():
    from wbs.forward.procedural import generate
    from wbs.taxonomy import sample_controls

    checked = 0
    for control in sample_controls(8, seed=11):
        scene = generate(control, Spec(duration_s=8, fps=24, resolution=(320, 180)), "P").to_json_dict()
        if not scene["actors"]:
            continue
        rows = declared_vs_measured(scene, framing_precheck(scene))
        assert all(r["status"] == "ok" for r in rows), [r["detail"] for r in rows if r["status"] != "ok"]
        assert set(scene["meta"]["framing_intent"]) == {r["shot"] for r in rows}
        checked += 1
    assert checked >= 4


def test_declared_framing_is_checked_against_the_camera():
    plan = StoryPlan.model_validate(_plan({"framing": "extreme_wide", "angle": "overhead"}))
    scene = story_to_scene(plan, Spec(duration_s=3, fps=24, resolution=(320, 180)), "X").to_json_dict()
    rows = declared_vs_measured(scene, framing_precheck(scene))
    assert rows[0]["status"] == "warning"
    assert "大远景" in rows[0]["detail"] and "顶拍" in rows[0]["detail"]
