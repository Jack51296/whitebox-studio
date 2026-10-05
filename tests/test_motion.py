from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from wbs.blender import kinematics as K
from wbs.config import DynamicGate, Spec, load_settings
from wbs.forward.motion import RouteInfeasible, Waypoint, compile_route
from wbs.forward.procedural import generate
from wbs.forward.story import story_to_scene
from wbs.forward.story_chain import SpaceLayer, layer_problems
from wbs.models.story import StoryPlan
from wbs.qc.dynamic import MOTION_TYPES, dynamic_gate
from wbs.qc.reports import orientation_check
from wbs.taxonomy import sample_controls

GATE = DynamicGate()
CHASE = [Waypoint(-3, -240), Waypoint(0, 40, at_s=12.0), Waypoint(2, 64), Waypoint(20, 70.2), Waypoint(24, 78),
         Waypoint(24, 134, at_s=20.0), Waypoint(21, 248, at_s=26.0)]


def _scene(keys, kind="vehicle", duration=26.0, interpolation="smooth"):
    radius, height = (2.3, 1.4) if kind == "vehicle" else (0.35, 1.7)
    return {"id": "t", "title": "t", "fps": 24, "duration_s": duration, "resolution": [320, 180], "blocks": [],
            "actors": [{"id": "A", "kind": kind, "height_m": height, "radius_m": radius,
                        "path": {"keys": keys, "interpolation": interpolation}}],
            "shots": [{"id": "S01", "start_s": 0.0, "end_s": duration, "lens_mm": 32,
                       "camera": {"keys": [[0, 80, 0, 40]], "aim_actor": "A"}}]}


def test_gate_flags_impossible_vehicle_cornering_and_warns_for_reverse():
    keys = [[0, 0, 0, 0], [2, 0, 40, 0], [2.6, 8, 48, 0], [5, 50, 48, 0]]
    scene = _scene(keys, duration=5.0, interpolation="cubic")
    gate = dynamic_gate(scene, GATE)
    assert gate["status"] == "failed" and gate["issue_counts"].get("actor_lateral")
    assert "g）" in next(i["detail"] for i in gate["issues"] if i["type"] == "actor_lateral")
    warned = dynamic_gate(scene, GATE, motion="warn")
    assert all(i["type"] not in MOTION_TYPES for i in warned["issues"]) and warned["warning_counts"]
    sharp = _scene([[0, 0, 0, 0], [2, 0, 40, 0], [2.3, 5, 44, 0], [4, 40, 44, 0]], duration=4.0, interpolation="cubic")
    report = orientation_check(sharp, gate=GATE)
    assert report["status"] == "failed" and "sliding" in report["problems"]["A"]


def test_route_compiler_respects_limits_and_timing():
    keys = compile_route(CHASE, GATE.limits("vehicle"), end_s=26.0, label="A")
    gate = dynamic_gate(_scene(keys), GATE)
    assert gate["status"] == "passed", gate["issues"][:3]
    track = K.Track(keys, "smooth")
    for w in CHASE[1:]:
        if w.at_s is not None:
            assert math.dist(track.at(w.at_s)[:2], (w.x, w.y)) < 0.5
    peaks = gate["motion"]["A"]
    assert peaks["peak_lateral"] <= GATE.limits("vehicle").lateral


def test_route_compiler_explains_infeasible_legs_and_holds_stops():
    with pytest.raises(RouteInfeasible, match=r"leg 0→1 .* fastest within limits"):
        compile_route([Waypoint(0, 0), Waypoint(0, 300, at_s=5.0)], GATE.limits("vehicle"), end_s=10.0)
    keys = compile_route([Waypoint(0, 0), Waypoint(0, 30, stop_s=2.0), Waypoint(20, 30)], GATE.limits("pawn"), 20.0)
    track = K.Track(keys, "smooth")
    arrive = next(k[0] for k in keys if abs(k[2] - 30.0) < 1e-6)
    assert all(track.at(arrive + d)[:2] == pytest.approx((0.0, 30.0), abs=1e-6) for d in (0.2, 1.0, 1.9))
    assert dynamic_gate(_scene(keys, "pawn", 20.0), GATE)["status"] == "passed"


def _plan(**over):
    events = [{"id": "E01", "at_s": 1.0, "choice": "起步", "consequence": "出发"},
              {"id": "E02", "at_s": 5.0, "choice": "转弯", "consequence": "绕开"},
              {"id": "E03", "at_s": 9.0, "choice": "抵达", "consequence": "结束"}]
    base = {"title": "t", "logline": "l", "content_class": "motion", "duration_s": 12.0, "world": "w", "story": "s",
            "goal": "g", "obstacle": "o", "stakes": "k", "ending": "e",
            "characters": [{"id": "A", "role": "车", "color": "#d9363e", "kind": "vehicle", "height_m": 1.4}],
            "routes": {"A": {"waypoints": [{"x": 0, "y": 0}, {"x": 0, "y": 60, "at_s": 6.0},
                                           {"x": 30, "y": 70, "at_s": 12.0}]}},
            "events": events, "twist": 1, "setup": [0],
            "shots": [{"id": "S01", "start_s": 0, "end_s": 12.0, "title": "a", "action": "a",
                       "camera_keys": [[0, 60, 20, 30]], "aim_actor": "A"}]}
    base.update(over)
    return base


def test_story_routes_compile_into_smooth_paths():
    plan = StoryPlan.model_validate(_plan())
    scene = story_to_scene(plan, Spec(duration_s=12, fps=24, resolution=(320, 180)), "X").to_json_dict()
    assert scene["actors"][0]["path"]["interpolation"] == "smooth"
    assert dynamic_gate(scene, GATE)["status"] == "passed"
    with pytest.raises(ValidationError, match="either a path or a route"):
        StoryPlan.model_validate(_plan(paths={"A": [[0, 0, 0, 0], [12, 0, 50, 0]]}))


def test_space_layer_reports_infeasible_routes_to_the_planner():
    data = SpaceLayer.model_validate({"routes": {"A": {"waypoints": [{"x": 0, "y": 0}, {"x": 0, "y": 400, "at_s": 3.0}]}}})
    state = {"characters": {"characters": [{"id": "A", "kind": "vehicle"}]}}
    problems = layer_problems("space", data, state, 12.0)
    assert any("路线做不到" in p and "fastest within limits" in p for p in problems)


def test_procedural_scenes_move_plausibly():
    settings = load_settings()
    for i, control in enumerate(sample_controls(12, seed=5)):
        scene = generate(control, settings.spec("forward_batch"), f"M{i}").to_json_dict()
        gate = dynamic_gate(scene, settings.qc.dynamic_gate)
        motion = [x for x in gate["issues"] if x["type"] in MOTION_TYPES + ("actor_speed",)]
        assert not motion, (control.subject_child, motion[:2])


def test_smooth_interpolation_reproduces_constant_acceleration():
    keys = [[t / 10, 0.0, 0.5 * 4.0 * (t / 10) ** 2, 0.0] for t in range(21)]
    smooth, cubic = K.Track(keys, "smooth"), K.Track(keys, "cubic")
    h = 1e-3
    for t in (0.35, 1.05, 1.55):
        acc = (smooth.at(t + h)[1] - 2 * smooth.at(t)[1] + smooth.at(t - h)[1]) / h ** 2
        assert acc == pytest.approx(4.0, abs=0.05)
    assert abs(cubic.at(0.05)[1] - 0.005) > abs(smooth.at(0.05)[1] - 0.005)
    hold = K.Track([[0, 0, 0, 0], [1, 1, 0, 0], [2, 1, 0, 0], [3, 3, 0, 0]], "smooth")
    assert all(abs(hold.at(1 + k / 20)[0] - 1.0) < 1e-9 for k in range(21))
