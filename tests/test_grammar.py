from __future__ import annotations

import pytest

from wbs.config import QC, Grammar, Readability, load_settings
from wbs.forward.story_chain import mechanical_checks
from wbs.forward.story_examples import load_team_cards
from wbs.models.story import StoryPlan
from wbs.qc.dynamic import framing_precheck
from wbs.qc.grammar import grammar_report, grammar_rules, pacing
from wbs.qc.pregate import pregate


def _scene(cams, lens=35, duration=None, framings=None, content="运动", speed=4.0):
    n = len(cams)
    duration = duration or 2.0 * n
    step = duration / n
    shots = [{"id": f"S{i + 1:02}", "start_s": i * step, "end_s": (i + 1) * step, "lens_mm": lens,
              "framing": (framings or [""] * n)[i], "camera": {"keys": [[i * step, *c]], "aim_actor": "A"}}
             for i, c in enumerate(cams)]
    return {"id": "g", "title": "g", "fps": 24, "duration_s": duration, "resolution": [320, 180], "blocks": [],
            "actors": [{"id": "A", "kind": "pawn", "height_m": 1.7, "radius_m": 0.35,
                        "path": {"keys": [[0, 0, 0, 0], [duration, 0, speed * duration, 0]], "interpolation": "linear"}}],
            "shots": shots, "meta": {"content_class_label": content}}


def _rules(scene):
    return {r["rule"]: r for r in grammar_rules(scene, framing_precheck(scene), Grammar())}


def test_jump_cut_axis_crossing_and_screen_direction():
    jump = _rules(_scene([(6, 1.5, 1.6), (6, 2.5, 1.6)], speed=1.0))
    assert jump["30° 规则（跳切）"]["status"] == "warning"
    crossed = _rules(_scene([(10, 4, 1.6), (-10, 4, 1.6)]))
    assert crossed["180° 轴线"]["status"] == "warning" and crossed["屏幕方向"]["status"] == "warning"
    bridged = _rules(_scene([(10, 4, 1.6), (0, -14, 1.6), (-10, 12, 1.6)]))
    assert bridged["180° 轴线"]["status"] == "ok"


def test_establishing_shot_and_variety():
    rules = _rules(_scene([(2.0, 0.3, 1.6), (2.2, 5, 1.6)], framings=["close", "close"]))
    assert rules["开场建立镜头"]["status"] == "warning"
    wide_open = _rules(_scene([(30, -10, 3), (6, 4, 1.6)]))
    assert wide_open["开场建立镜头"]["status"] == "ok" and wide_open["景别多样性"]["status"] == "ok"


@pytest.mark.skipif(not load_team_cards(), reason="team director-card samples not present")
def test_pacing_compares_with_team_samples():
    slow = pacing(_scene([(30, -10, 3), (10, 30, 2)], duration=26.0))
    assert slow["status"] == "warning" and slow["team"]["cards"] > 0
    assert pacing(_scene([(30, -10, 3)], duration=26.0))["status"] == "not_applicable"


def test_warnings_fail_the_pregate_only_when_enforced():
    scene = _scene([(6, 1.5, 1.6), (6, 2.5, 1.6)], speed=1.0)
    scene["events"] = [{"id": "E01", "at_s": 1.0, "role": "setup"}]
    relaxed = grammar_report(scene, QC())
    assert relaxed["status"] == "warning" and relaxed["warning_count"] >= 2
    strict = QC(grammar=Grammar(enforce=True), readability=Readability(enforce=True))
    assert grammar_report(scene, strict)["status"] == "failed"
    settings = load_settings().model_copy(update={"qc": strict})
    assert pregate(scene, settings)["status"] == "failed"


def test_mechanical_checks_include_what_the_gate_will_say():
    events = [{"id": "E01", "at_s": 1.0, "choice": "起步", "consequence": "出发", "targets": ["A"]},
              {"id": "E02", "at_s": 5.0, "choice": "转弯", "consequence": "绕开"},
              {"id": "E03", "at_s": 9.0, "choice": "抵达", "consequence": "结束"}]
    plan = StoryPlan.model_validate({
        "title": "t", "logline": "l", "content_class": "motion", "duration_s": 12.0, "world": "w", "story": "s",
        "goal": "g", "obstacle": "o", "stakes": "k", "ending": "e",
        "characters": [{"id": "A", "role": "车", "color": "#d9363e", "kind": "vehicle", "height_m": 1.4}],
        "paths": {"A": [[0, 0, 0, 0], [3, 0, 60, 0], [3.5, 12, 66, 0], [12, 90, 66, 0]]},
        "events": events, "twist": 1, "setup": [0],
        "shots": [{"id": "S01", "start_s": 0, "end_s": 12.0, "title": "a", "action": "a", "framing": "wide",
                   "rig": {"type": "follow"}}]})
    rules = {c["rule"]: c for c in mechanical_checks(plan)}
    assert rules["运动在各主体种类的物理上限内"]["status"] == "warning"
    assert "铺垫 E01 在镜头里看得清" in rules
