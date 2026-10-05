from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from wbs.config import Spec, load_settings
from wbs.forward import story_chain
from wbs.forward.story_chain import revise_plan, run_chain, snapshot_version
from wbs.forward.story_examples import compare_card, load_team_cards, pick_examples, reference_stats
from wbs.jsonio import read_json
from wbs.layout import JobPaths
from wbs.ledger import Ledger
from wbs.providers import CallContext, get_providers
from wbs.providers.base import LLMResult, Usage
from wbs.providers.mock import MOCK_HANDLERS

SPEC = Spec(duration_s=26, fps=24, resolution=(1280, 720))


def _mock_providers(tmp_path):
    return get_providers(load_settings(), Ledger(tmp_path / "l.sqlite"))


needs_team_samples = pytest.mark.skipif(not load_team_cards(), reason="references/ 缺失（团队样例）")


@needs_team_samples
def test_team_cards_parse_and_examples():
    cards = load_team_cards()
    assert len(cards) >= 21
    examples = pick_examples()
    assert [c.content_class for c in examples] == ["narrative", "narrative", "motion", "motion"]
    assert all(c.logline and c.story for c in examples)
    ref = reference_stats("motion")
    assert ref["cards"] >= 10 and ref["median_shot_length_s"]["median"] < 4


def test_chain_mock_builds_valid_plan(workspace, tmp_path):
    plan, info = run_chain("两个人在广场上追逐一只滚动的箱子", _mock_providers(tmp_path), CallContext("t/c", "story"), SPEC,
                           "motion")
    assert [layer["layer"] for layer in info["layers"]] == list(story_chain.LAYERS)
    assert info["simulated"] is True and info["critique"][0]["status"] == "skipped"
    assert len(info["examples"]) == (4 if load_team_cards() else 0)
    assert plan.events[plan.twist].mechanism == "判断与再选择" and plan.setup == [0, 1]
    assert {c["rule"] for c in info["mechanical_checks"]} >= {"事件数 4–9", "转折位于全片 20%–85%"}


class ScriptedLLM:
    """Pretends to be a real model: layer tasks use the mock handlers, critique/revise are scripted."""

    is_real = True
    model = "scripted"

    def __init__(self):
        self.critiques = [{"passed": False, "issues": [{"layer": "shots", "rule": "镜长", "detail": "S01 过长",
                                                         "fix": "缩短"}]}, {"passed": True, "issues": []}]
        self.calls = []

    def complete(self, ctx, *, system, user, images=None, json_mode=False, task=None, payload=None):
        self.calls.append(task)
        if task == "story_critique":
            text = json.dumps(self.critiques.pop(0), ensure_ascii=False)
        elif task == "story_revise":
            plan = json.loads(user.split("当前 StoryPlan：", 1)[1].split("\n\nJSON Schema：", 1)[0])
            plan["notes"] = "按评审修正：缩短 S01"
            text = json.dumps(plan, ensure_ascii=False)
        else:
            text = MOCK_HANDLERS[task](payload or {})
        return LLMResult(text=text, usage=Usage(), model=self.model, simulated=False)


def test_critique_loop_revises_until_passed(workspace):
    llm = ScriptedLLM()
    providers = SimpleNamespace(llm=llm, policy=SimpleNamespace(dry_run=False))
    plan, info = run_chain("快递员穿过街区送最后一单", providers, CallContext("t/c", "story"), SPEC, "narrative")
    assert [h["passed"] for h in info["critique"]] == [False, True]
    assert info["critique"][0]["revised"] is True and plan.notes == "按评审修正：缩短 S01"
    assert llm.calls.count("story_critique") == 2 and llm.calls.count("story_revise") == 1


def test_revise_levels_rerun_only_the_needed_layers(workspace, tmp_path):
    providers = _mock_providers(tmp_path)
    old, _ = run_chain("两人会合", providers, CallContext("t/r", "story"), SPEC, "narrative")
    plan, info = revise_plan(old, "两人会合", "第三镜太长", "shots", providers, CallContext("t/r", "rev"), SPEC)
    assert [layer["layer"] for layer in info["layers"]] == ["shots"] and plan.title == old.title
    assert "已按看片意见修订" in plan.notes
    plan, info = revise_plan(old, "两人会合", "转折不清楚", "story", providers, CallContext("t/r", "rev"), SPEC)
    assert [layer["layer"] for layer in info["layers"]] == ["events", "space", "shots"]
    assert "转折不清楚" in plan.story
    with pytest.raises(ValueError):
        revise_plan(old, "x", "y", "everything", providers, CallContext("t/r", "rev"), SPEC)


def test_space_layer_requires_setup_targets_and_feasible_routes(workspace):
    state = {"characters": {"characters": [{"id": "A", "kind": "vehicle"}]},
             "events": {"events": [{"id": "E02", "at_s": 3.0, "targets": ["gap_wall", "A"]}]}}
    route = {"waypoints": [{"x": 0, "y": 0}, {"x": 0, "y": 150}]}
    missing = story_chain.SpaceLayer(routes={"A": route})
    assert any("gap_wall" in p for p in story_chain.layer_problems("space", missing, state, 26.0))
    built = story_chain.SpaceLayer(blocks=[{"id": "gap_wall", "center": [6, 60, 2], "size": [1, 12, 4]}], routes={"A": route})
    assert story_chain.layer_problems("space", built, state, 26.0) == []
    rushed = story_chain.SpaceLayer(blocks=built.blocks, routes={"A": {"waypoints": [
        {"x": 0, "y": 0}, {"x": 0, "y": 150, "at_s": 3.0}]}})
    assert any("路线做不到" in p for p in story_chain.layer_problems("space", rushed, state, 26.0))


def test_snapshot_version_keeps_every_previous_delivery(tmp_path):
    job = JobPaths(tmp_path / "B" / "J")
    job.root.mkdir(parents=True)
    (job.root / "scene.json").write_text("{}", encoding="utf-8")
    (job.root / "片_白模参考.mp4").write_bytes(b"v1")
    first = snapshot_version(job)
    (job.root / "片_白模参考.mp4").write_bytes(b"v2")
    second = snapshot_version(job)
    assert (first.name, second.name) == ("v01", "v02")
    assert (first / "片_白模参考.mp4").read_bytes() == b"v1" and (second / "片_白模参考.mp4").read_bytes() == b"v2"


@needs_team_samples
def test_compare_card_reports_structure_against_team():
    card = "\n".join(["《t》剧本与分镜导演卡", "26秒｜1280×720｜24fps｜运动类｜1个物理镜头｜无音轨", "", "一句话：x",
                      "完整剧本：y", "E01 成片0.000s／源0.000s：a → b", "S01｜0.000–26.000s｜全片"])
    report = compare_card(card, "motion")
    assert report["status"] == "informational"
    assert any("镜头数" in n for n in report["notes"]) and any("转折" in n for n in report["notes"])


def test_cli_story_imports_external_plan_with_vehicles(workspace, tmp_path):
    from wbs.cli import app
    from wbs.forward.story import _mock_story_plan

    plan = json.loads(_mock_story_plan({"brief": "两车追逐", "duration_s": 12, "fps": 24}))
    plan["characters"][0].update(kind="vehicle", height_m=1.5, role="逃逸车")
    source = tmp_path / "plan.json"
    source.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    result = CliRunner().invoke(app, ["forward", "story", "--batch", "X", "--job", "X1", "--brief", "两车追逐",
                                      "--plan", str(source), "--no-render"])
    assert result.exit_code == 0, result.output
    job = JobPaths(workspace / "batches" / "X" / "X1")
    review = read_json(job.report("故事评审.json"))
    assert review["mode"] == "external" and review["simulated"] is False
    actors = {a["id"]: a for a in read_json(job.scene)["actors"]}
    first = plan["characters"][0]["id"]
    assert actors[first]["kind"] == "vehicle" and actors[first]["radius_m"] == 2.3
    assert all(a["kind"] == "pawn" and a["radius_m"] == 0.35 for i, a in actors.items() if i != first)


def test_cli_story_then_revise_backs_up_first(workspace):
    from wbs.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["forward", "story", "--batch", "S", "--job", "S1", "--brief", "两人抢一只箱子",
                                 "--no-render"])
    assert result.exit_code == 0, result.output
    job = JobPaths(workspace / "batches" / "S" / "S1")
    assert read_json(job.report("故事评审.json"))["mode"] == "chain"
    result = runner.invoke(app, ["forward", "revise", str(job.root), "--notes", "第二镜更近一些", "--level", "shots",
                                 "--no-render"])
    assert result.exit_code == 0, result.output
    assert (job.root / "versions" / "v01" / "story_plan.json").exists()
    meta = job.read_meta()
    assert meta["revisions"][0]["level"] == "shots" and "第二镜更近一些" in read_json(job.story_plan)["notes"]
