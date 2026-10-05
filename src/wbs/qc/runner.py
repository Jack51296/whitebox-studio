"""Run every automatic check for a job and write honest reports (team formats).

Status layers (生产与复核记录): technical (automatic, this module) · sampled_visual · normal_speed_viewing ·
curation. Only a person sets the last three (``wbs review mark``); until then they stay not_run / pending.
"""

from __future__ import annotations

import time
from typing import Any

from ..config import Settings
from ..jsonio import read_json, write_json
from ..layout import JobPaths, Workspace
from ..ledger import Ledger
from ..models.common import combine
from .diversity import diversity_review
from .dynamic import dynamic_gate
from .grammar import grammar_report
from .media import media_check
from .pregate import GRAMMAR_REPORT
from .reports import consistency_report, editing_check, framing_report, orientation_check, speed_report
from .temporal import temporal_evidence

HUMAN_FIELDS = {"sampled_visual": "not_run", "normal_speed_viewing": "not_run", "curation": "pending"}


def _evidence(job: JobPaths, *names: str) -> list[str]:
    return [n for n in names if (job.root / n).exists()]


def _temporal_check(job: JobPaths, temporal: dict | None) -> dict[str, Any]:
    if not temporal:
        return {"status": "not_run", "observed": "视频缺失，未做时域统计；正常速度观感不自动代填。",
                "evidence_paths": _evidence(job, "reports/media-check.json")}
    vb = temporal["vbench"]
    vbench = f"VBench {vb['scores']}" if vb.get("scores") else f"VBench 未运行（{vb.get('reason') or vb.get('errors')}）"
    return {"status": temporal["status"],
            "observed": (f"自动时域证据：亮度闪烁 p95 {temporal['luma_flicker_p95']}，黑帧 {len(temporal['black_frames_1based'])}，"
                         f"单帧闪帧 {len(temporal['single_frame_flashes_1based'])}，"
                         f"非计划切点 {len(temporal['unexpected_spikes_1based'])}；{vbench}。"
                         f"{'；'.join(temporal['problems'])}正常速度观感仍需人工。"),
            "evidence_paths": _evidence(job, "reports/时序证据.json")}


def experience_report(job: JobPaths, scene: dict, reports: dict[str, dict], reviews: dict[str, dict]) -> dict[str, Any]:
    video = job.video(scene["title"])
    media = reports["media"]
    framing = reports["framing"]
    dyn = reports["dynamic"]
    checks = {
        "minimal_modeling": {"status": "passed" if scene.get("precision") == "low" else "not_run",
                             "observed": "低精度几何代理：体块场景＋刚体主体，只平移与绕竖轴转向；无手持物与特效轨迹。",
                             "evidence_paths": _evidence(job, "scene.json", "分镜总览.jpg")},
        "orientation_stability": {"status": reports["orientation"]["status"],
                                  "observed": f"逐 {reports['orientation']['sample_hz']}Hz 朝向采样，最大偏航速率 "
                                              f"{reports['orientation']['max_yaw_rate_dps']} °/s。",
                                  "evidence_paths": _evidence(job, "reports/朝向检查.json")},
        "temporal_stability": _temporal_check(job, reports.get("temporal")),
        "framing_contrast": {"status": framing.get("status", "not_applicable") if len(scene["shots"]) > 1 or framing.get("status") == "failed" else "not_applicable",
                             "observed": f"主体投影高度比 {framing.get('ratio')}；逐镜可见率见景别对照。",
                             "evidence_paths": _evidence(job, "reports/景别对照.json")},
        "route_geometry": {"status": dyn["status"],
                           "observed": f"{dyn['sample_hz']}Hz 采样：相机最小净距 {dyn['minimum_camera_clearance_m']} m，"
                                       f"主体最大穿插 {dyn['maximum_actor_block_penetration_m']} m，问题 {dyn['issue_counts'] or '无'}。",
                           "evidence_paths": _evidence(job, "reports/制作数据检查.json", "路线俯视图.png")},
        "build_consistency": {"status": reports["consistency"]["status"],
                              "observed": f"Blender 实测与 scene.json 计算偏差：主体 {reports['consistency']['max_actor_deviation_m']} m，"
                                          f"相机 {reports['consistency']['max_camera_deviation_m']} m。",
                              "evidence_paths": _evidence(job, "audit/samples.json", "reports/构建一致性.json")},
        "cut_continuity": {"status": reports["editing"]["status"],
                           "observed": "切点位置与分镜一致性（自动检测）；切点前后画面连续性需人工抽帧复核。",
                           "evidence_paths": _evidence(job, "reports/剪辑检查.json")},
        "media": {"status": "passed" if media["passed"] else "failed", "observed": "; ".join(media["problems"]) or "解码、帧数、帧率、分辨率、无音轨均符合",
                  "evidence_paths": _evidence(job, "reports/media-check.json")},
        "sampled_frame_review": {"status": reviews.get("sampled_visual", {}).get("value", "not_run"),
                                 "observed": reviews.get("sampled_visual", {}).get("note") or "待人工查看故事板与关键帧",
                                 "evidence_paths": _evidence(job, "分镜总览.jpg", "故事板")},
    }
    normal = reviews.get("normal_speed_viewing")
    return {"video_file": video.name, "video_sha256": media.get("sha256"),
            "status": "rendered_test; normal_speed_viewing_" + (normal["value"] if normal else "pending"),
            "checks": checks,
            "normal_speed_review": {"status": normal["value"] if normal else "not_run",
                                    "observed": (normal or {}).get("note") or "未进行正常速度连续观看；不代填观感通过。",
                                    "reviewer": (normal or {}).get("reviewer"), "evidence_paths": []},
            "user_acceptance": reviews.get("curation", {}).get("value", "pending")}


def qc_job(job: JobPaths, settings: Settings, ledger: Ledger, ws: Workspace | None = None) -> dict[str, Any]:
    scene = read_json(job.scene)
    video = job.video(scene["title"])
    reports: dict[str, dict] = {}
    reports["media"] = media_check(video, scene) if video.exists() else {"passed": False, "problems": ["video missing"]}
    write_json(job.report("media-check.json"), reports["media"])
    motion = "warn" if job.read_meta().get("route") == "reverse" else "enforce"
    reports["dynamic"] = {**dynamic_gate(scene, settings.qc.dynamic_gate, motion=motion), "stage": "post_render"}
    write_json(job.report("制作数据检查.json"), reports["dynamic"])
    samples = read_json(job.samples) if job.samples.exists() else None
    reports["editing"] = editing_check(scene, video if video.exists() else None, samples)
    write_json(job.report("剪辑检查.json"), reports["editing"])
    if video.exists():
        fps = int(scene["fps"])
        reports["temporal"] = temporal_evidence(video, [int(round(s["start_s"] * fps)) for s in scene["shots"][1:]], fps)
        write_json(job.report("时序证据.json"), reports["temporal"])
    reports["speed"] = speed_report(scene, gate=settings.qc.dynamic_gate)
    write_json(job.report("速度测量.json"), reports["speed"])
    reports["orientation"] = orientation_check(scene, gate=settings.qc.dynamic_gate)
    write_json(job.report("朝向检查.json"), reports["orientation"])
    if samples is not None:
        reports["framing"] = framing_report(scene, samples, settings.qc.framing)
        reports["consistency"] = consistency_report(scene, samples)
    else:
        reports["framing"] = {"status": "not_run", "reason": "audit/samples.json missing (render first)"}
        reports["consistency"] = {"status": "not_run", "reason": "audit/samples.json missing",
                                  "max_actor_deviation_m": None, "max_camera_deviation_m": None}
    write_json(job.report("景别对照.json"), reports["framing"])
    write_json(job.report("构建一致性.json"), reports["consistency"])
    reports["grammar"] = {**grammar_report(scene, settings.qc), "stage": "post_render"}
    write_json(job.report(GRAMMAR_REPORT), reports["grammar"])
    others = {}
    if ws is not None:
        for other in ws.iter_jobs():
            if other.root != job.root and other.scene.exists():
                others[other.key] = read_json(other.scene)
    reports["diversity"] = diversity_review(scene, others, settings.qc.diversity_threshold)
    write_json(job.report("diversity-review.json"), reports["diversity"])

    reviews = ledger.reviews(job.key)
    experience = experience_report(job, scene, reports, reviews)
    write_json(job.report("experience-report.json"), experience)
    framing_status = reports["framing"].get("status")
    grammar_status = reports["grammar"]["status"]
    technical = combine("passed" if reports["media"]["passed"] else "failed", reports["dynamic"]["status"],
                        reports["editing"]["status"], reports["orientation"]["status"] if reports["orientation"]["status"] != "not_applicable" else None,
                        reports["consistency"]["status"], framing_status if framing_status != "not_applicable" else None,
                        grammar_status if grammar_status == "failed" else None)
    summary = {"job": job.key, "title": scene["title"], "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "render": "rendered" if video.exists() else "not_rendered", "technical": technical,
               **{field: reviews.get(field, {}).get("value", default) for field, default in HUMAN_FIELDS.items()},
               "static_gate": job.read_meta().get("static_gate", "not_applicable"),
               "checks": {"media": "passed" if reports["media"]["passed"] else "failed",
                          "dynamic_gate": reports["dynamic"]["status"], "editing": reports["editing"]["status"],
                          "orientation": reports["orientation"]["status"], "framing": framing_status,
                          "build_consistency": reports["consistency"]["status"],
                          "grammar_readability": grammar_status,
                          "grammar_warnings": reports["grammar"]["warning_count"],
                          "diversity_similar": reports["diversity"]["similar_count"]},
               "reviews": reviews}
    write_json(job.report("质检汇总.json"), summary)
    job.update_meta(qc_status=technical, status="qc_done", video_sha256=reports["media"].get("sha256"))
    return summary
