from __future__ import annotations

import collections
import json

import pytest
from pydantic import ValidationError

from wbs.config import DynamicGate, Spec, load_settings
from wbs.forward import texts
from wbs.forward.batch import plan_batch
from wbs.forward.longtake import plan_longtake, plan_to_scene
from wbs.forward.procedural import generate
from wbs.forward.story import plan_story, story_to_scene
from wbs.layout import Workspace
from wbs.ledger import Ledger
from wbs.models import SceneSpec, schema_documents
from wbs.providers import CallContext, get_providers
from wbs.qc.dynamic import dynamic_gate
from wbs.taxonomy import _violates, sample_controls

SPEC = Spec(duration_s=15, fps=24, resolution=(1920, 1080))


def _minimal(**over):
    scene = {"schema": "wbs.scene/1.0", "id": "T", "title": "t", "fps": 24, "duration_s": 2.0,
             "resolution": [320, 180], "actors": [{"id": "A", "path": {"keys": [[0, 0, 0, 0], [2, 0, 2, 0]]}}],
             "shots": [{"id": "S01", "start_s": 0.0, "end_s": 1.0, "camera": {"keys": [[0, 0, -5, 1.6]], "aim_actor": "A"}},
                       {"id": "S02", "start_s": 1.0, "end_s": 2.0, "camera": {"keys": [[0, 3, -5, 1.6]], "aim_actor": "A"}}]}
    scene.update(over)
    return scene


def test_scene_contract_accepts_valid_and_rejects_invalid():
    SceneSpec.model_validate(_minimal())
    bad_shots = [
        [{"id": "S01", "start_s": 0.0, "end_s": 1.0, "camera": {"keys": [[0, 0, 0, 1]], "aim_actor": "A"}},
         {"id": "S02", "start_s": 1.2, "end_s": 2.0, "camera": {"keys": [[0, 0, 0, 1]], "aim_actor": "A"}}],
        [{"id": "S01", "start_s": 0.0, "end_s": 1.01, "camera": {"keys": [[0, 0, 0, 1]], "aim_actor": "A"}},
         {"id": "S02", "start_s": 1.01, "end_s": 2.0, "camera": {"keys": [[0, 0, 0, 1]], "aim_actor": "A"}}],
        [{"id": "S01", "start_s": 0.0, "end_s": 2.0, "camera": {"keys": [[0, 0, 0, 1]], "aim_actor": "Z"}}],
    ]
    for shots in bad_shots:
        with pytest.raises(ValidationError):
            SceneSpec.model_validate(_minimal(shots=shots))
    with pytest.raises(ValidationError):
        SceneSpec.model_validate(_minimal(unknown_field=1))


def test_json_schemas_exported_match_models(tmp_path):
    from wbs.config import REPO_ROOT

    for name, doc in schema_documents().items():
        path = REPO_ROOT / "schemas" / f"{name}.schema.json"
        assert path.exists(), f"run scripts/export_schemas.py ({name})"
        assert json.loads(path.read_text(encoding="utf-8")) == doc, f"schemas/{name}.schema.json is stale"


def test_taxonomy_sampling_is_deterministic_stratified_and_valid():
    a, b = sample_controls(120, seed=3), sample_controls(120, seed=3)
    assert a == b
    counts = collections.Counter(c.subject for c in a)
    assert max(counts.values()) - min(counts.values()) <= 1
    assert sum(c.content_class == "narrative" for c in a) == 60
    for c in a:
        assert not _violates(c.as_dict())
        assert (c.shot_count == 1) == (c.shot_form == "long_take")


def test_procedural_scenes_are_deterministic_and_mostly_clean():
    controls = sample_controls(48, seed=5)
    passed = 0
    for c in controls:
        first = generate(c, SPEC, "J").to_json_dict()
        assert first == generate(c, SPEC, "J").to_json_dict()
        passed += dynamic_gate(first, DynamicGate())["status"] == "passed"
        text, ref = texts.control_layer_prompt(first)
        assert ref == "forward.control_layer@1.0.0" and "{{" not in text
    assert passed / len(controls) >= 0.9


def test_batch_planning_reseeds_until_gate_passes(workspace):
    settings = load_settings()
    ws = Workspace(settings.workspace).ensure()
    jobs = plan_batch(ws, settings, "B1", n=6, seed=9)
    assert len(jobs) == 6
    assert (ws.batch_dir("B1") / "批次清单.xlsx").exists()
    for job in jobs:
        meta = job.read_meta()
        assert meta["pregate"] == "passed", meta
        assert job.scene.exists() and (job.prompts_dir / "白模控制层.txt").exists()


def test_story_and_longtake_mock_routes(workspace, tmp_path):
    settings = load_settings()
    providers = get_providers(settings, Ledger(tmp_path / "l.sqlite"))
    plan, info = plan_story("两个人在广场上追逐一只滚动的箱子", providers, CallContext("t/s", "story_plan"),
                            settings.spec("forward_story"))
    assert info["simulated"] is True
    scene = story_to_scene(plan, settings.spec("forward_story"), "S1").to_json_dict()
    assert dynamic_gate(scene, settings.qc.dynamic_gate)["status"] == "passed"
    assert "（模拟规划" in texts.director_card(scene)

    lt, _ = plan_longtake("穿过博物馆的五个展厅", providers, CallContext("t/l", "longtake_plan"),
                          settings.spec("forward_longtake"), zones=5)
    lscene = plan_to_scene(lt, settings.spec("forward_longtake"), "L1").to_json_dict()
    assert len(lscene["shots"]) == 1
    assert dynamic_gate(lscene, settings.qc.dynamic_gate)["status"] == "passed"
    times = [e["at_s"] for e in lscene["events"]]
    assert times == sorted(times) and len(times) == 4


EVENTS3 = [{"id": "E01", "at_s": 0.3, "mechanism": "目标", "choice": "望向出口", "consequence": "明确目的"},
           {"id": "E02", "at_s": 1.2, "mechanism": "转折", "choice": "改走侧门", "consequence": "绕开障碍"},
           {"id": "E03", "at_s": 2.6, "mechanism": "后果", "choice": "穿过侧门", "consequence": "抵达"}]


def _plan_dict(**over):
    base = {"title": "t", "logline": "l", "content_class": "narrative", "duration_s": 3.0, "world": "w", "story": "s",
            "goal": "g", "obstacle": "o", "stakes": "k", "ending": "e",
            "characters": [{"id": "A", "role": "主角", "color": "#d9363e"}],
            "paths": {"A": [[0, 0, 0, 0], [3, 0, 3, 0]]}, "events": EVENTS3, "twist": 1, "setup": [0],
            "shots": [{"id": "S01", "start_s": 0, "end_s": 3.0, "title": "a", "action": "a",
                       "camera_keys": [[0, 0, -4, 1.6]], "aim_actor": "A"}]}
    base.update(over)
    return base


def test_story_event_chain_rules():
    from wbs.models.story import StoryPlan

    plan = StoryPlan.model_validate(_plan_dict())
    assert [e["role"] for e in plan.events_with_roles()] == ["setup", "twist", "beat"]
    for bad in (dict(setup=[1]), dict(setup=[2], twist=1), dict(setup=[]), dict(twist=5),
                dict(events=[EVENTS3[1], EVENTS3[0], EVENTS3[2]]),
                dict(events=[{**EVENTS3[0], "consequence": ""}, EVENTS3[1], EVENTS3[2]]),
                dict(beats=[{"start_s": 2.0, "end_s": 1.0, "condition": "c", "action": "a", "change": "x"}])):
        with pytest.raises(ValidationError):
            StoryPlan.model_validate(_plan_dict(**bad))


def test_director_card_marks_setup_twist_and_route_beats():
    from wbs.models.story import StoryPlan

    beats = [{"start_s": 0.0, "end_s": 1.5, "condition": "目标明确", "action": "直行", "change": "发现障碍"},
             {"start_s": 1.5, "end_s": 3.0, "condition": "侧门可通", "action": "转向", "change": "绕开障碍"}]
    plan = StoryPlan.model_validate(_plan_dict(beats=beats, semantic_only="衣摆与表情只在最终AI表现"))
    scene = story_to_scene(plan, Spec(duration_s=3, fps=24, resolution=(320, 180)), "X").to_json_dict()
    card = texts.director_card(scene)
    assert "E01（铺垫）" in card and "E02（转折）" in card and "望向出口 → 明确目的" in card
    assert "路线与空间交接" in card and "0.000–1.500s：目标明确 → 直行 → 发现障碍" in card
    assert "衣摆与表情只在最终AI表现" in card
    assert "路线与空间交接" in texts.continuation_prompt(scene)


def test_vehicle_cast_texts_use_vehicle_wording_and_story_lighting():
    from wbs.models.story import StoryPlan

    spec = Spec(duration_s=3, fps=24, resolution=(320, 180))
    cars = [{"id": "A", "role": "逃逸车", "color": "#d9363e", "kind": "vehicle", "height_m": 1.4},
            {"id": "B", "role": "追车", "color": "#2f6fdb", "kind": "vehicle", "height_m": 1.6}]
    plan = StoryPlan.model_validate(_plan_dict(characters=cars, lighting="深夜，高杆灯与车灯",
                                               paths={"A": [[0, 0, 0, 0], [3, 0, 30, 0]], "B": [[0, 6, 0, 0], [3, 6, 30, 0]]}))
    scene = story_to_scene(plan, spec, "X").to_json_dict()
    card, cont, (v2v, _) = texts.director_card(scene), texts.continuation_prompt(scene), texts.v2v_render_prompt(scene)
    assert "无四肢" not in card + cont and "A、B 为载具体块" in card and "引擎、轮胎摩擦、刹车与环境声" in card
    assert "主体保持全片共享轨迹" in card and "还原为真实汽车" in cont
    assert "深夜，高杆灯与车灯" in v2v and "A为红色汽车（逃逸车，" in v2v and "不要原地跑" not in v2v

    human = story_to_scene(StoryPlan.model_validate(_plan_dict()), spec, "Y").to_json_dict()
    assert "人物均为无四肢几何代理" in texts.director_card(human)
    assert "自然日光" in texts.v2v_render_prompt(human)[0] and "不要原地跑" in texts.v2v_render_prompt(human)[0]


def test_story_shot_times_snap_to_frames():
    from wbs.models.story import StoryPlan

    plan = StoryPlan.model_validate({
        "title": "t", "logline": "l", "content_class": "narrative", "duration_s": 3.0, "world": "w", "story": "s",
        "goal": "g", "obstacle": "o", "stakes": "k", "ending": "e",
        "characters": [{"id": "A", "role": "主角", "color": "#d9363e"}],
        "paths": {"A": [[0, 0, 0, 0], [3, 0, 3, 0]]}, "events": EVENTS3, "twist": 1, "setup": [0],
        "shots": [{"id": "S01", "start_s": 0, "end_s": 1.01, "title": "a", "action": "a", "camera_keys": [[0, 0, -4, 1.6]], "aim_actor": "A"},
                  {"id": "S02", "start_s": 1.01, "end_s": 3.0, "title": "b", "action": "b", "camera_keys": [[0, 3, -4, 1.6]], "aim_actor": "A"}]})
    scene = story_to_scene(plan, Spec(duration_s=3, fps=24, resolution=(320, 180)), "X")
    assert scene.shots[0].end_s == pytest.approx(1.0)
    assert scene.source["shots_snapped_to_frames"] is True
