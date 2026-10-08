"""Batch finish: 交付清单.json, 生产与复核记录.json, 阅读说明.md, refreshed manifest and an optional ZIP.

Existing deliveries are never overwritten: each ZIP gets a timestamped name.
"""

from __future__ import annotations

import time
import zipfile
from pathlib import Path
from typing import Any

from .. import __version__
from ..config import Settings, load_settings
from ..forward.batch import write_manifest
from ..hashing import sha256_file
from ..jsonio import read_json, write_json, write_text
from ..layout import JobPaths, Workspace
from ..ledger import Ledger

ZIP_EXCLUDE_DIRS = {"render", "source", "_template"}
READING_GUIDE = """# 阅读说明

本批次由 whitebox-studio {version} 生成。

## 目录
- `批次清单.xlsx / .csv`：每条任务的结构树控制信息、闸门与质检结论、视频文件名。
- `交付清单.json`：交付的视频、时长、镜头数、内容类别和校验和。
- `生产与复核记录.json`：每条视频四类状态。
- `<任务ID>/`：白模参考视频、分镜总览、故事板、剧本与分镜导演卡、视频续作提示词、导演注释（VTT）、路线俯视图、
  `prompts/`（白模控制层与 V2V 渲染提示词）、`reports/`（全部检查报告）、`scene.json`（唯一数据源）。
- `<任务ID>/模型导出/`（`wbs batch finish --export-models` 或 `wbs export` 时生成）：FBX、GLB 与 `镜头切换.csv`。
  时间线上的切镜不会随 FBX/GLB 导出，按 CSV 建镜头切换轨道；在 Blender 里导入 FBX 时把 Animation Offset 设为 0。

## 状态含义（如实记录，不代填）
- technical：程序自动检查（解码/帧数/帧率/无音轨、动态穿模闸门、切点、朝向、Blender 构建一致性、主体可见率）。
- sampled_visual：有人看过故事板与关键帧后标记；未标记即 not_run。
- normal_speed_viewing：有人以正常速度完整看过后标记；未标记即 not_run。
- curation：用户是否采用；未标记即 pending。
- 人工标记命令：`wbs review mark <任务目录> --field normal_speed_viewing --value passed --reviewer 姓名`。

## 注意
- 路线图、VTT、故事板只用于核对，不要把线条、箭头、字幕画进最终视频。
- mock 模式产生的规划、图片均标注为模拟，不代表真实模型结果。
"""


def _job_item(job: JobPaths) -> tuple[dict[str, Any], dict[str, Any]]:
    meta = job.read_meta()
    scene = read_json(job.scene) if job.scene.exists() else {}
    summary = read_json(job.report("质检汇总.json")) if job.report("质检汇总.json").exists() else {}
    video = job.video(scene.get("title")) if scene else None
    exists = bool(video and video.exists())
    sha = summary.get("video_sha256") or meta.get("video_sha256") or (sha256_file(video) if exists else None)
    labels = (scene.get("meta") or {}).get("labels") or {}
    story = (scene.get("meta") or {}).get("story") or {}
    content = labels.get("content_class_label") or (scene.get("meta") or {}).get("content_class_label", "")
    item = {"key": job.job_id, "title": scene.get("title", job.job_id), "folder": job.root.name, "route": meta.get("route"),
            "video": video.name if exists else None, "duration_seconds": scene.get("duration_s"),
            "physical_shots": len(scene.get("shots", [])), "content_class": {"叙事": "narrative", "运动": "motion"}.get(content, content),
            "one_take": len(scene.get("shots", [])) == 1,
            "logline": story.get("logline") or (labels and f"{labels.get('subject_label')}｜{labels.get('camera_move_label')}"),
            "sha256": sha}
    record = {"title": item["title"], "video_sha256": sha, "render": "rendered" if exists else "not_rendered",
              "technical": summary.get("technical", "not_run"), "sampled_visual": summary.get("sampled_visual", "not_run"),
              "normal_speed_viewing": summary.get("normal_speed_viewing", "not_run"),
              "curation": summary.get("curation", "pending"), "report": f"{job.root.name}/reports/experience-report.json"}
    return item, record


def finish_batch(ws: Workspace, ledger: Ledger, batch_id: str, make_zip: bool = True,
                 include_blend: bool = False, export_models: bool = False,
                 settings: Settings | None = None) -> dict[str, Any]:
    """``export_models`` writes FBX/GLB + 镜头切换.csv into each rendered job before the ZIP is built."""
    batch_dir = ws.batch_dir(batch_id)
    jobs = ws.iter_jobs(batch_id)
    run_id = ledger.start_run("batch_finish", {"batch_id": batch_id, "jobs": len(jobs)})
    status = "failed"
    try:
        result = _deliver(batch_dir, batch_id, jobs, make_zip, include_blend, export_models, settings)
        status = "succeeded"
    finally:
        ledger.finish_run(run_id, status)
    return result


def _deliver(batch_dir: Path, batch_id: str, jobs: list[JobPaths], make_zip: bool, include_blend: bool,
             export_models: bool, settings: Settings | None) -> dict[str, Any]:
    exported: dict[str, Any] = {}
    if export_models:
        from ..export import export_models as export_job

        settings = settings or load_settings()
        for job in jobs:
            if job.scene.exists() and job.blend(read_json(job.scene)["title"]).exists():
                exported[job.job_id] = export_job(job, settings)["folder"]
    items, records = zip(*[_job_item(j) for j in jobs]) if jobs else ((), ())
    rendered = [i for i in items if i["video"]]
    delivery = {"platform_version": __version__, "batch_id": batch_id, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "rendered_videos": len(rendered), "total_seconds": round(sum(i["duration_seconds"] or 0 for i in rendered), 3),
                "motion": sum(i["content_class"] == "motion" for i in rendered),
                "narrative": sum(i["content_class"] == "narrative" for i in rendered),
                "normal_speed_review": "passed" if records and all(r["normal_speed_viewing"] == "passed" for r in records) else "not_run",
                "items": list(items)}
    review = {"requested_videos": len(jobs), "rendered_videos": len(rendered),
              "user_adopted": sum(r["curation"] == "adopted" for r in records),
              "normal_speed_reviewed": sum(r["normal_speed_viewing"] in ("passed", "failed") for r in records),
              "items": list(records),
              "scope": "technical 为程序自动检查；sampled_visual / normal_speed_viewing / curation 只由人工标记，未标记不代填"}
    write_json(batch_dir / "交付清单.json", delivery)
    write_json(batch_dir / "生产与复核记录.json", review)
    write_text(batch_dir / "阅读说明.md", READING_GUIDE.format(version=__version__))
    write_manifest(batch_dir, jobs)
    result: dict[str, Any] = {"delivery": str(batch_dir / "交付清单.json"), "jobs": len(jobs), "rendered": len(rendered)}
    if export_models:
        result["model_exports"] = exported
    if make_zip:
        result["zip"] = str(_zip(batch_dir, jobs, include_blend))
    return result


def _zip(batch_dir: Path, jobs: list[JobPaths], include_blend: bool) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = batch_dir.parent / f"交付_{batch_dir.name}_{stamp}.zip"
    with zipfile.ZipFile(out, "x", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in ("交付清单.json", "生产与复核记录.json", "阅读说明.md", "批次清单.xlsx", "批次清单.csv", "batch.json"):
            if (batch_dir / name).exists():
                zf.write(batch_dir / name, f"{batch_dir.name}/{name}")
        for job in jobs:
            for path in sorted(job.root.rglob("*")):
                rel = path.relative_to(job.root)
                if path.is_dir() or (rel.parts and rel.parts[0] in ZIP_EXCLUDE_DIRS):
                    continue
                if path.suffix == ".blend" and not include_blend:
                    continue
                zf.write(path, f"{batch_dir.name}/{job.root.name}/{rel.as_posix()}")
    return out
