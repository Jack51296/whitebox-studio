"""Reverse pipeline driver: analyze → solve → static gate (+ refine loop) → dynamic gate."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ..config import Settings
from ..errors import WbsError
from ..hashing import sha256_file
from ..jsonio import read_json, write_json
from ..layout import JobPaths
from ..providers import CallContext, Providers
from ..qc.dynamic import dynamic_gate
from ..qc.static import static_gate
from ..render import run_blender
from . import refine
from .analyze import analyze
from .annotate import annotate as annotate_frames
from .camera_motion import label_shots
from .solve import solve


def intake(job: JobPaths, video: Path, license_note: str) -> Path:
    if not license_note or not license_note.strip():
        raise WbsError("反推的源视频必须登记授权来源（--license），例如“自有拍摄”“已获授权：合同号 …”")
    job.root.mkdir(parents=True, exist_ok=True)
    dest = job.root / "source" / Path(video).name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if Path(video).resolve() != dest.resolve():
        shutil.copyfile(video, dest)
    job.update_meta(route="reverse", source_video=dest.relative_to(job.root).as_posix(), source_sha256=sha256_file(dest),
                    source_license=license_note.strip(), status="intake")
    return dest


def run_analysis(job: JobPaths, providers: Providers | None = None, *, annotate: bool = True, cuts: str | None = None,
                 subject_backend: str | None = None, subject: str = "person", prompt: str | None = None,
                 camera_motion: str | None = None) -> dict[str, Any]:
    meta = job.read_meta()
    analysis = analyze(job.root / meta["source_video"], job.analysis_dir, cuts=cuts, subject_backend=subject_backend,
                       subject=subject, prompt=prompt)
    if providers is not None and annotate:
        annotate_frames(analysis, job.analysis_dir, providers, CallContext(job_key=job.key, step="reverse_annotate",
                                                                           batch_id=job.batch_id))
    label_shots(analysis, job.analysis_dir, providers, CallContext(job_key=job.key, step="camera_motion", batch_id=job.batch_id),
                backend=camera_motion)
    write_json(job.analysis_dir / "analysis.json", analysis)
    job.update_meta(status="analyzed", cuts_s=analysis["cuts"]["times_s"], cuts_backend=analysis["cuts"]["detector"]["backend"],
                    subject_backend=analysis["subject_detection"]["backend"])
    return analysis


def gate_frames(analysis: dict) -> str:
    frames = sorted({i + 1 for s in analysis["shots"] for i in s["first_mid_last_frames"]})
    return ",".join(str(f) for f in frames)


def run_static_gate(job: JobPaths, settings: Settings, analysis: dict) -> dict[str, Any]:
    if job.frames_dir.exists():
        shutil.rmtree(job.frames_dir)
    run_blender(job, settings, frames=gate_frames(analysis), audit=True, save_blend=False)
    report = static_gate(analysis, job.analysis_dir, read_json(job.scene), read_json(job.samples), job.frames_dir,
                         job.reports_dir / "static_gate", settings.qc.static_gate)
    write_json(job.report("静态闸门.json"), report)
    return report


def camera_track(job: JobPaths, analysis: dict, backend: str, *, metric: bool = False) -> Path:
    """Run the geometry backend in the vision worker; the NPZ lands next to analysis.json."""
    from ..vision.fetch import model_path
    from ..vision.worker import run

    out = job.analysis_dir / "camera_track.npz"
    video = analysis["video"]
    request: dict[str, Any] = {"video": str(job.root / job.read_meta()["source_video"]), "out": str(out),
                               "width": video["width"], "height": video["height"],
                               "shots": [{"start": s["start_frame"], "end": s["end_frame"]} for s in analysis["shots"]]}
    if backend == "da3":
        request["model"] = "da3-base" if (model_path("da3-base") / "model.safetensors").exists() else "da3-small"
        if metric and (model_path("da3metric-large") / "model.safetensors").exists():
            request["metric_model"] = "da3metric-large"
        run("geometry_da3", request)
    else:
        run("geometry_mapanything", request)
    return out


def solve_scene(job: JobPaths, analysis: dict, title: str, providers: Providers | None = None, *, subject: str = "person",
                lens_mm: float = 28.0, geometry: str | None = None, track_file: Path | None = None,
                camera_height: float = 1.6, scale: str = "camera_height") -> tuple[dict, dict[str, Any]]:
    """Geometry solve when a track is available (imported or from the worker), else the heuristic solver.

    ``scale``: camera_height (assumed camera height over the fitted ground) | metric (DA3METRIC-LARGE depth) | auto.
    """
    from ..vision import resolve
    from ..vision.worker import VisionUnavailable
    from .geometry import GeometryError, load_track, solve_geometry

    info: dict[str, Any] = {"requested": geometry or "config"}
    track = None
    try:
        if track_file is not None or geometry == "npz":
            if track_file is None:
                raise GeometryError("--geometry npz 需要 --track 指定外部相机轨迹（MegaSaM / COLMAP 转换的 NPZ）")
            dest = job.analysis_dir / "camera_track.npz"
            if Path(track_file).resolve() != dest.resolve():
                shutil.copyfile(track_file, dest)
            track, info["backend"] = load_track(dest, analysis), "npz"
        else:
            choice = resolve("geometry", geometry)
            info.update(backend=choice.used, fallback_reasons=choice.skipped or None)
            if choice.used != "heuristic":
                track = load_track(camera_track(job, analysis, choice.used, metric=scale != "camera_height"), analysis)
        if track is not None:
            scene, geo = solve_geometry(analysis, track, job.job_id, title, subject=subject, camera_height=camera_height,
                                        scale_source=scale)
            label_shots(analysis, job.analysis_dir, providers, CallContext(job.key, "camera_motion", job.batch_id),
                        track=track)
            write_json(job.analysis_dir / "analysis.json", analysis)
            return scene.to_json_dict(), {**info, "geometry": geo}
    except (GeometryError, VisionUnavailable, OSError, ValueError) as exc:
        info.update(backend="heuristic", error=f"{type(exc).__name__}: {exc}")
    return solve(analysis, job.job_id, title, subject=subject, lens_mm=lens_mm).to_json_dict(), {**info, "backend": "heuristic"}


def solve_and_gate(job: JobPaths, settings: Settings, providers: Providers, *, subject: str = "person",
                   lens_mm: float = 28.0, max_refine: int = 2, geometry: str | None = None,
                   track_file: Path | None = None, camera_height: float = 1.6, scale: str = "camera_height") -> dict[str, Any]:
    analysis = read_json(job.analysis_dir / "analysis.json")
    title = job.read_meta().get("title") or f"反推_{job.job_id}"
    scene, solver = solve_scene(job, analysis, title, providers, subject=subject, lens_mm=lens_mm, geometry=geometry,
                                track_file=track_file, camera_height=camera_height, scale=scale)
    write_json(job.scene, scene)
    job.update_meta(solver=solver)
    history = []
    report = run_static_gate(job, settings, analysis)
    history.append({"round": 0, "status": report["status"], "checks": report["checks"]})
    for n in range(1, max_refine + 1):
        if report["status"] == "passed":
            break
        ctx = CallContext(job_key=job.key, step=f"reverse_refine_{n}", batch_id=job.batch_id)
        adj, info = refine.propose(analysis, report, providers, ctx)
        if not adj.get("mirror_x") and not adj.get("shots"):
            history.append({"round": n, "adjustments": adj, "info": info, "stopped": "no applicable adjustment"})
            break
        scene = refine.apply(scene, adj)
        write_json(job.scene, scene)
        report = run_static_gate(job, settings, analysis)
        history.append({"round": n, "adjustments": adj, "info": info, "status": report["status"], "checks": report["checks"]})
    dyn = dynamic_gate(scene, settings.qc.dynamic_gate, motion="warn")
    write_json(job.report("制作数据检查.json"), {**dyn, "stage": "reverse_solve"})
    write_json(job.report("反推修正记录.json"), {"rounds": history})
    job.update_meta(title=scene["title"], shots=len(scene["shots"]), static_gate=report["status"],
                    pregate=dyn["status"], status="solved")
    return {"static_gate": report["status"], "dynamic_gate": dyn["status"], "rounds": len(history) - 1,
            "solver": {k: solver.get(k) for k in ("backend", "requested", "error")}}
