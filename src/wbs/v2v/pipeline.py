"""V2V driver for a white-model job (or any folder holding the four files)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import Settings
from ..errors import WbsError
from ..jsonio import read_json, write_json
from ..layout import JobPaths
from ..providers import CallContext, Providers
from . import execute, package, planner

DEFAULT_OPTIONS = {"image_model": "GPT", "video_model": "SD2.5", "style": "真实电影实拍", "aspect": "16:9",
                   "max_images": 4, "adaptation": ""}


def job_inputs(job: JobPaths) -> planner.V2VInputs:
    scene = read_json(job.scene)
    video = job.video(scene["title"])
    missing = [str(p.name) for p in (video, job.overview, job.director_card, job.continuation) if not p.exists()]
    if missing:
        raise WbsError(f"{job.key} 缺少 V2V 四文件：{missing}（先运行 wbs render）")
    frames = job.report("storyboard-frames.json")
    storyboard = planner.normalize_storyboard(read_json(frames)["frames"]) if frames.exists() else []
    return planner.V2VInputs(job.job_id, video, job.overview, job.director_card, job.continuation, storyboard, job.root)


def options_with_defaults(options: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(DEFAULT_OPTIONS)
    merged.update({k: v for k, v in (options or {}).items() if v is not None})
    return merged


def run_plan(job: JobPaths, inputs: planner.V2VInputs, providers: Providers, options: dict[str, Any],
             external: Path | None = None) -> dict[str, Any]:
    ctx = CallContext(job_key=job.key, step="v2v_plan", batch_id=job.batch_id)
    result = planner.plan(inputs, job.v2v_dir, providers, ctx, options_with_defaults(options), external=external)
    job.update_meta(v2v_plan="complete", v2v_plan_simulated=result["record"]["simulated"])
    return result["record"]


def run_images(job: JobPaths, inputs: planner.V2VInputs, providers: Providers) -> dict[str, Any]:
    plan_path = job.v2v_dir / "SP输出_v3.json"
    if not plan_path.exists():
        raise WbsError("先运行 wbs v2v plan")
    ctx = CallContext(job_key=job.key, step="v2v_images", batch_id=job.batch_id)
    record = execute.generate(plan_path, inputs.video, job.v2v_dir, providers, ctx)
    job.update_meta(v2v_images=record["summary"])
    return record["summary"]


def run_package(job: JobPaths, inputs: planner.V2VInputs, providers: Providers, options: dict[str, Any]) -> dict[str, Any]:
    manifest = package.build_package(inputs, job.v2v_dir, options_with_defaults(options), providers.image.model)
    job.update_meta(v2v_package_ready=manifest["package_ready"], v2v_files_complete=manifest["files_complete"])
    return {k: manifest[k] for k in ("files_complete", "package_ready", "simulated_images", "missing")}


def run_submit(job: JobPaths, providers: Providers) -> dict[str, Any]:
    ctx = CallContext(job_key=job.key, step="v2v_submit", batch_id=job.batch_id)
    result = package.submit(job.v2v_dir, providers, ctx)
    job.update_meta(v2v_submit=result.status)
    return {"status": result.status, "message": result.message, "remote_id": result.remote_id}


def run_compare(job: JobPaths, settings: Settings, inputs: planner.V2VInputs, result_video: Path | None = None) -> Path:
    from ..qc.temporal import temporal_evidence

    result_video = result_video or (job.v2v_dir / "结果" / "v2v_result.mp4")
    out = package.compare(inputs.video, result_video, job.v2v_dir / "双路对比.mp4", settings.render.video)
    white = temporal_evidence(inputs.video)
    temporal = temporal_evidence(result_video, [c - 1 for c in white["unexpected_spikes_1based"]])
    write_json(job.v2v_dir / "成片时序证据.json", {**temporal, "reference": "白模的切点视为计划切点",
                                                   "note": "V2V 流程内部不做视觉评分；本文件只作评估证据"})
    job.update_meta(v2v_compare=out.name, v2v_temporal=temporal["status"])
    return out
