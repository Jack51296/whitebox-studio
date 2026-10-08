from __future__ import annotations

import copy
import dataclasses

import numpy as np

from wbs.blender import kinematics as K
from wbs.config import Dressing, Spec, load_settings
from wbs.forward.dressing import dress, floor_lines, split_containers
from wbs.forward.procedural import generate
from wbs.forward.story import story_to_scene
from wbs.models.scene import SceneSpec
from wbs.models.story import StoryPlan
from wbs.qc.dynamic import dynamic_gate
from wbs.taxonomy import sample_controls

STACK = {"id": "CY", "shape": "box", "center": [25, 59.5, 2.8], "size": [26, 13, 5.6], "role": "container"}
TOWER = {"id": "B1", "shape": "box", "center": [-30, 0, 9], "size": [30, 40, 18], "role": "building"}


def test_container_stacks_split_into_units_that_keep_their_group():
    blocks, units = split_containers([STACK])
    assert units == len(blocks) == 2 * 5 * 2 and all(b["group"] == "CY" for b in blocks)
    lo, hi = K.block_aabb(STACK)
    for b in blocks:
        a, c = K.block_aabb(b)
        assert all(a[i] >= lo[i] - 1e-6 and c[i] <= hi[i] + 1e-6 for i in range(3))
    scene = {"id": "s", "title": "s", "fps": 24, "duration_s": 2.0, "resolution": [320, 180], "blocks": blocks,
             "shots": [{"id": "S01", "start_s": 0, "end_s": 2.0, "camera": {"keys": [[0, 0, 0, 2]], "aim_keys": [[0, 25, 59, 2]]}}],
             "events": [{"id": "E01", "at_s": 1.0, "role": "setup", "targets": ["CY"]}]}
    SceneSpec.model_validate(scene)


def test_floor_lines_follow_building_height_and_never_collide():
    lines = floor_lines([TOWER, {**TOWER, "id": "LOW", "size": [10, 10, 4]}], 3.2)
    assert len(lines) == 5 and all(not b["collision"] and b["group"] == "B1" for b in lines)
    assert [b["center"][2] for b in lines] == [3.2, 6.4, 9.6, 12.8, 16.0]


def _chase_plan(**extra):
    events = [{"id": "E01", "at_s": 1.0, "choice": "起步", "consequence": "出发"},
              {"id": "E02", "at_s": 8.0, "choice": "加速", "consequence": "拉开"},
              {"id": "E03", "at_s": 16.0, "choice": "远去", "consequence": "结束"}]
    return StoryPlan.model_validate({
        "title": "t", "logline": "l", "content_class": "motion", "duration_s": 20.0, "world": "w", "story": "s",
        "goal": "g", "obstacle": "o", "stakes": "k", "ending": "e",
        "characters": [{"id": "A", "role": "车", "color": "#d9363e", "kind": "vehicle", "height_m": 1.4}],
        "routes": {"A": {"waypoints": [{"x": 0, "y": 0}, {"x": 0, "y": 400}], "start_moving": True}},
        "blocks": [{"id": "G00", "shape": "plane", "center": [0, 200, -0.05], "size": [200, 600, 0.1], "role": "ground",
                    "collision": False}, TOWER, STACK],
        "events": events, "twist": 1, "setup": [0],
        "shots": [{"id": "S01", "start_s": 0, "end_s": 20.0, "title": "a", "action": "a", "framing": "wide",
                   "rig": {"type": "follow", "height_m": 3.0}}], **extra})


def test_story_scenes_get_posts_and_lane_marks_clear_of_paths_and_cameras():
    settings = load_settings()
    spec = Spec(duration_s=20, fps=24, resolution=(320, 180))
    scene = story_to_scene(_chase_plan(), spec, "D", dressing=settings.forward.dressing).to_json_dict()
    counts = scene["meta"]["dressing"]
    assert counts["reference_posts"] > 5 and counts["lane_marks"] > 10 and counts["container_units"] == 20
    assert counts["floor_lines"] == 5
    gate = dynamic_gate(scene, settings.qc.dynamic_gate)
    assert gate["status"] == "passed", gate["issue_counts"]
    posts = np.array([b["center"][:2] for b in scene["blocks"] if b["role"] == "reference_post"])
    path = np.array([k[1:3] for k in scene["actors"][0]["path"]["keys"]])
    assert min(np.hypot(path[:, 0] - x, path[:, 1] - y).min() for x, y in posts) > 2.3 + 0.15 + 0.6
    plain = story_to_scene(_chase_plan(dressing=False), spec, "P", dressing=settings.forward.dressing).to_json_dict()
    assert "dressing" not in plain["meta"] and len(plain["blocks"]) == 3


def test_dressing_a_dressed_scene_again_replaces_its_own_cues():
    cfg = load_settings().forward.dressing
    scene = story_to_scene(_chase_plan(), Spec(duration_s=20, fps=24, resolution=(320, 180)), "D",
                           dressing=cfg).to_json_dict()
    again = copy.deepcopy(scene)
    assert dress(again, cfg) == scene["meta"]["dressing"]
    assert SceneSpec.model_validate(again).to_json_dict()["blocks"] == scene["blocks"]
    authored = {"id": "kerb_post", "shape": "cylinder", "center": [5, 5, 1], "size": [0.3, 0.3, 2], "role": "reference_post"}
    kept = {"blocks": [authored], "actors": []}
    dress(kept, cfg)
    assert kept["blocks"] == [authored], "only the platform's own cues are replaced"


def test_parallel_routes_keep_posts_on_the_outer_kerbs():
    plan = _chase_plan().model_dump(mode="json")
    plan["characters"].append({"id": "B", "role": "追车", "color": "#2f6fdb", "kind": "vehicle", "height_m": 1.6})
    plan["routes"]["B"] = {"waypoints": [{"x": -7.5, "y": 0}, {"x": -7.5, "y": 400}], "start_moving": True}
    scene = story_to_scene(StoryPlan.model_validate(plan), Spec(duration_s=20, fps=24, resolution=(320, 180)), "D",
                           dressing=load_settings().forward.dressing).to_json_dict()
    xs = [b["center"][0] for b in scene["blocks"] if b["role"] == "reference_post"]
    assert xs and all(x > 0 or x < -7.5 for x in xs), "no post may stand between the two lanes"
    assert any(x > 0 for x in xs) and any(x < -7.5 for x in xs)

    plan["characters"].append({"id": "C", "role": "追车", "color": "#e0a800", "kind": "vehicle", "height_m": 1.6})
    plan["routes"]["C"] = {"waypoints": [{"x": 7.5, "y": 0}, {"x": 7.5, "y": 400}], "start_moving": True}
    plan["routes"]["A"] = {"waypoints": [{"x": 0, "y": 0}, {"x": 0, "y": 200}], "start_moving": True}
    scene = story_to_scene(StoryPlan.model_validate(plan), Spec(duration_s=20, fps=24, resolution=(320, 180)), "D",
                           dressing=load_settings().forward.dressing).to_json_dict()
    posts = [b["center"][:2] for b in scene["blocks"] if b["role"] == "reference_post"]
    assert posts and all(not -7.5 < x < 7.5 for x, _ in posts), "the middle lane stays clear after A turns off"


def test_street_scenes_from_the_structure_tree_are_dressed():
    base = dataclasses.replace(sample_controls(1, seed=2)[0], subject="object", subject_child="vehicle")
    streets = []
    for seed in range(40):
        scene = generate(dataclasses.replace(base, seed=seed), load_settings().spec("forward_batch"), "T").to_json_dict()
        if scene["meta"]["environment"] == "street":
            streets.append(scene)
            break
    assert streets, "no street environment sampled"
    counts = streets[0]["meta"]["dressing"]
    assert counts.get("floor_lines", 0) + counts.get("reference_posts", 0) + counts.get("lane_marks", 0) > 0
    assert dynamic_gate(streets[0], load_settings().qc.dynamic_gate)["issue_counts"].get("actor_block_penetration", 0) == 0
    assert dress({"blocks": [], "actors": []}, Dressing(enabled=False)) == {}
