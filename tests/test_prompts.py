from __future__ import annotations

import pytest

from wbs.forward.story import planning_guides
from wbs.prompts import get_prompt, library

GUIDES = planning_guides("motion", 26)
SAMPLES = {
    "forward.control_layer": dict(
        spec={"duration_s": 15, "fps": 24, "frames": 360, "resolution": (1920, 1080)},
        control={"shot_form_label": "多镜头", "shot_count": 3, "subject_label": "人·双人", "color_label": "灰白色",
                 "viewpoint_label": "正常视角", "camera_move_label": "跟随镜头", "content_class_label": "运动",
                 "color_rule": "场景统一灰白色体块"},
        summary={"environment": "广场与几组方块障碍", "subjects": ["A：红色棋子"], "motion": "A 由南向北奔跑",
                 "shots": ["S01 0–5 秒 跟随"]}),
    "forward.v2v_render": dict(style_label="写实实拍风格", world="现代都市", subjects=["两名成年人"],
                               environment="开阔广场。", camera_rule="沿用原视频运镜。", lighting="自然日光。",
                               long_take=True, vehicles=False),
    "forward.control_rewrite": dict(control_prompt="目标：……"),
    "forward.story_planner": dict(brief="两人抢一只箱子", duration_s=26, fps=24, content_class="motion",
                                  schema_json="{}", **GUIDES),
    **{f"forward.story_chain.{layer}": dict(
        brief="两人抢一只箱子", duration_s=26, fps=24, content_class="motion", context_json="{}", notes="",
        examples=[{"title": "样例", "content_class": "motion", "duration_s": 26, "shots": 6, "logline": "一句话",
                   "story": "剧本", "structure": ["目标：x"], "events": ["E01｜0s｜a → b"]}],
        schema_json="{}", **GUIDES) for layer in ("premise", "characters", "events", "space", "shots")},
    "forward.story_critique": dict(plan_json="{}", findings_json="[]"),
    "forward.story_revise": dict(plan_json="{}", issues_json="[]", schema_json="{}"),
    "reverse.camera_motion": dict(
        shots=[{"id": "S01", "start_s": 0, "end_s": 2, "images": ["图1"],
                "rules": {"labels": ["pan_left"], "ambiguous": ["摇↔横移"]}}],
        taxonomy={"pan_left": "左摇", "truck_left": "左移"}),
    "reverse.online_convert": dict(duration_s=15, template_desc="四个格子展示积木人", subject_form="积木人",
                                   scene_structure="地面合并为一整块平面；", cut_times=["2.5", "6.0"]),
    "reverse.refine_agent": dict(analysis_summary="{}", gate_report="{}"),
    "reverse.annotate_subjects": dict(shots=[{"id": "S01", "start_s": 0, "end_s": 2.5, "images": ["图1", "图2", "图3"]}],
                                      auto_json="{}"),
    "v2v.planner_user": dict(job_id="B_0001",
                             video={"name": "v.mp4", "width": 1280, "height": 720, "fps": 24, "duration_s": 26,
                                    "frames": 624},
                             overview_name="分镜总览.jpg", director_card="卡", continuation="续",
                             options={"image_model": "GPT", "video_model": "SD2.5", "style": "真实电影实拍",
                                      "aspect": "16:9", "max_images": 4, "adaptation": ""}),
}


def test_library_loads_with_unique_ids_and_provenance():
    prompts = library()
    assert len(prompts) >= 25
    for prompt in prompts.values():
        assert prompt.version and prompt.source, prompt.id
        if prompt.kind == "verbatim":
            assert "source_url" not in prompt.meta, prompt.id
            assert prompt.body.strip(), prompt.id


@pytest.mark.parametrize("prompt_id", sorted(SAMPLES))
def test_templates_render(prompt_id):
    prompt = get_prompt(prompt_id)
    assert prompt.kind == "template"
    text = prompt.render(**SAMPLES[prompt_id])
    assert "{{" not in text and "{%" not in text


def test_planner_prompts_carry_the_rules_the_checks_enforce():
    space = get_prompt("forward.story_chain.space").render(**SAMPLES["forward.story_chain.space"])
    assert "vehicle（车辆）：最高" in space and "routes" in space
    shots = get_prompt("forward.story_chain.shots").render(**SAMPLES["forward.story_chain.shots"])
    assert "extreme_wide=大远景" in shots and "side_track 侧向并行" in shots and "团队同类样例" in shots
    planner = get_prompt("forward.story_planner").render(**SAMPLES["forward.story_planner"])
    assert all(k in planner for k in ("vehicle（车辆）", "orbit=环绕", "focus_region", "团队同类样例"))
    events = get_prompt("forward.story_chain.events").render(**SAMPLES["forward.story_chain.events"])
    assert "targets" in events and "focus_region" in events


def test_every_template_has_sample_variables():
    templates = {p.id for p in library().values() if p.kind == "template"}
    assert templates == set(SAMPLES)


def test_verbatim_prompts_reject_variables():
    with pytest.raises(ValueError):
        get_prompt("v2v.style.real").render(x=1)


def test_realistic_style_prompt_is_deduplicated():
    body = get_prompt("v2v.style.real").body
    assert body.count("以输入白膜图仅作为「镜头构图与主体空间站位」参考") == 1
