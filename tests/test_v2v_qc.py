from __future__ import annotations

import copy
import dataclasses
import json

import pytest
from conftest import requires_blender, requires_ffmpeg

from wbs.config import REPO_ROOT, Spec, load_settings
from wbs.forward.procedural import generate
from wbs.jsonio import read_json, write_json
from wbs.layout import Workspace
from wbs.ledger import Ledger
from wbs.providers import get_providers
from wbs.qc import review
from wbs.qc.finish import finish_batch
from wbs.qc.runner import qc_job
from wbs.render import render_job
from wbs.taxonomy import sample_controls
from wbs.v2v import contract, pipeline, planner

TEAM_SAMPLE = REPO_ROOT / "references" / "whitebox-world-studio-1.2.0" / "01_塔背的出口_单人立体穿梭"
needs_team_sample = pytest.mark.skipif(not TEAM_SAMPLE.exists(), reason="references/ 缺失（团队样例）")


def test_team_director_card_shots_parse():
    card = (REPO_ROOT / "tests/fixtures/director-card.txt").read_text(encoding="utf-8")
    shots = planner.parse_shots(card)
    assert [s["id"] for s in shots] == [f"S0{i}" for i in range(1, 9)]
    assert shots[0]["start"] == 0.0 and shots[-1]["end"] == 26.0
    assert all(a["end"] == pytest.approx(b["start"]) for a, b in zip(shots, shots[1:]))


@requires_ffmpeg
@needs_team_sample
def test_team_sample_v2v_regression_under_mock(workspace):
    """Plan requirement: the team's 塔背的出口 four files → contract-valid plan, frames, mock images, package."""
    settings = load_settings()
    ws = Workspace(settings.workspace).ensure()
    ledger = Ledger(ws.ledger_path)
    job = ws.job("REG", "TOWER")
    job.root.mkdir(parents=True)
    job.update_meta(route="v2v_only", title="塔背的出口")
    inputs = planner.inputs_from_dir(TEAM_SAMPLE, job.job_id)
    assert inputs.video.name == "塔背的出口_白模参考.mp4"
    providers = get_providers(settings, ledger)
    record = pipeline.run_plan(job, inputs, providers, {"max_images": 4})
    assert record["contract_valid"] and record["shots"] == 8
    plan = read_json(job.v2v_dir / "SP输出_v3.json")
    assert plan["video_prompt"].startswith("将无声白模预演替换为真人实拍，")
    summary = pipeline.run_images(job, inputs, providers)
    assert summary["succeeded"] == len(plan["image_prompts"]) == 4
    frames = read_json(job.v2v_dir / "关键帧" / "frame_manifest.json")
    assert frames["status"] == "ready" and len(frames["anchors"]) == 3
    pkg = pipeline.run_package(job, inputs, providers, {})
    assert pkg["files_complete"] is True and pkg["package_ready"] is False

    external = job.root / "外部SP.json"
    edited = copy.deepcopy(plan)
    edited["video_prompt"] = edited["video_prompt"].replace("（mock 规划，仅用于离线联调）", "（人工撰写）")
    write_json(external, edited)
    record = pipeline.run_plan(job, inputs, providers, {"max_images": 4}, external=external)
    assert record["contract_valid"] and record["simulated"] is False and record["attempts"][0]["model"] == "external"
    assert "（人工撰写）" in (job.v2v_dir / "视频渲染提示词.txt").read_text(encoding="utf-8")
    del edited["video_prompt"]
    write_json(external, edited)
    with pytest.raises(Exception, match="契约"):
        pipeline.run_plan(job, inputs, providers, {"max_images": 4}, external=external)


def test_framing_precheck_flags_subject_out_of_frame():
    from wbs.qc.dynamic import framing_precheck

    base = {"schema": "wbs.scene/1.0", "id": "F", "title": "f", "fps": 24, "duration_s": 2.0, "resolution": [320, 180],
            "actors": [{"id": "A", "path": {"keys": [[0, 0, 0, 0], [2, 0, 2, 0]]}}]}
    good = dict(base, shots=[{"id": "S01", "start_s": 0, "end_s": 2, "camera": {"keys": [[0, 0, -6, 1.6]], "aim_actor": "A"}}])
    bad = dict(base, shots=[{"id": "S01", "start_s": 0, "end_s": 2,
                             "camera": {"keys": [[0, 0, -6, 1.6]], "aim_keys": [[0, 0, -12, 1.6]]}}])
    assert framing_precheck(good)["status"] == "passed"
    assert framing_precheck(bad)["status"] == "failed"


def _mock_plan(shots=3):
    payload = {"shots": [{"n": i, "id": f"S0{i}", "start": i - 1.0, "end": float(i), "title": f"t{i}", "action": "a"}
                         for i in range(1, shots + 1)],
               "frames": [{"shot_n": 2, "time_s": 1.5, "frame_1based": 37}], "options": {"max_images": 3}, "story": {}}
    return json.loads(planner._mock_v2v_plan(payload))


def test_contract_accepts_mock_plan_and_catches_violations():
    plan = _mock_plan()
    assert contract.validate(plan, 3) == []
    bad = copy.deepcopy(plan)
    bad["image_prompts"][0]["space_source"]["anchors"] = [bad["image_prompts"][1]["space_source"]["anchors"][0]]
    assert any("text_to_image" in e for e in contract.validate(bad, 3))
    bad = copy.deepcopy(plan)
    bad["image_prompts"][1]["ref"] = "@图5"
    assert any("consecutive" in e for e in contract.validate(bad, 3))
    bad = copy.deepcopy(plan)
    bad["video_prompt"] = bad["video_prompt"].replace("将无声白模预演替换为真人实拍，", "")
    assert any("must start" in e for e in contract.validate(bad, 3))
    bad = copy.deepcopy(plan)
    bad["image_prompts"][1]["shot_ids"] = [1]
    assert any("same set" in e for e in contract.validate(bad, 3))
    bad = copy.deepcopy(plan)
    bad["image_prompts"][1]["reference_image_ids"] = []
    assert any("reference_image_ids" in e for e in contract.validate(bad, 3))
    assert contract.validate({"status": "needs_input", "image_prompts": [], "video_prompt": "", "issues": []}) == []


@pytest.mark.blender
@requires_blender
@requires_ffmpeg
def test_render_qc_review_finish_and_v2v(workspace):
    settings = load_settings()
    ws = Workspace(settings.workspace).ensure()
    ledger = Ledger(ws.ledger_path)
    control = dataclasses.replace(sample_controls(1, seed=8, subjects=["person"])[0], subject_child="duo",
                                  shot_form="multi_shot", shot_count=3, camera_move="follow", color="identity")
    job = ws.job("B", "B_0001")
    job.root.mkdir(parents=True)
    scene = generate(control, Spec(duration_s=6.0, fps=24, resolution=(480, 270)), "B_0001").to_json_dict()
    write_json(job.scene, scene)
    job.update_meta(title=scene["title"], route="forward_batch")
    render_job(job, settings, save_blend=False)

    summary = qc_job(job, settings, ledger, ws)
    assert summary["checks"]["media"] == "passed"
    assert summary["checks"]["build_consistency"] == "passed"
    editing = read_json(job.report("剪辑检查.json"))
    assert editing["basis"] == "blender_active_camera", editing
    assert editing["status"] == "passed", editing
    assert summary["sampled_visual"] == "not_run" and summary["normal_speed_viewing"] == "not_run"
    assert summary["curation"] == "pending"
    exp = read_json(job.report("experience-report.json"))
    assert exp["normal_speed_review"]["status"] == "not_run"
    review.mark(job, ledger, "normal_speed_viewing", "passed", "测试员", "完整看过")
    assert read_json(job.report("质检汇总.json"))["normal_speed_viewing"] == "passed"
    with pytest.raises(ValueError):
        review.mark(job, ledger, "normal_speed_viewing", "great", "测试员")

    done = finish_batch(ws, ledger, "B")
    delivery = read_json(ws.batch_dir("B") / "交付清单.json")
    assert delivery["rendered_videos"] == 1 and done["zip"].endswith(".zip")
    records = read_json(ws.batch_dir("B") / "生产与复核记录.json")
    assert records["items"][0]["normal_speed_viewing"] == "passed"

    providers = get_providers(settings, ledger)
    inputs = pipeline.job_inputs(job)
    record = pipeline.run_plan(job, inputs, providers, {"max_images": 3})
    assert record["contract_valid"] and record["simulated"]
    plan = read_json(job.v2v_dir / "SP输出_v3.json")
    assert contract.validate(plan, len(scene["shots"])) == []
    summary = pipeline.run_images(job, inputs, providers)
    assert summary["succeeded"] == len(plan["image_prompts"])
    again = pipeline.run_images(job, inputs, providers)
    gen = read_json(job.v2v_dir / "生成记录.json")
    assert again["succeeded"] == summary["succeeded"] and all(v["requests"] == 1 for v in gen["items"].values())
    pkg = pipeline.run_package(job, inputs, providers, {})
    assert pkg["files_complete"] is True and pkg["package_ready"] is False and pkg["simulated_images"] is True
    sub = pipeline.run_submit(job, providers)
    assert sub["status"] == "exported"
    assert (job.v2v_dir / "项目_提交包" / "提交说明.md").exists()
