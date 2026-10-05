"""Batch orchestration: render → QC per job with ledger-backed resume, a worker pool and per-job isolation."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .config import Settings
from .forward.batch import write_manifest
from .hashing import sha256_file
from .jsonio import read_json
from .layout import JobPaths, Workspace
from .ledger import Ledger
from .log import get_logger, log_event
from .pipeline import run_step
from .qc.runner import qc_job
from .render import render_job

log = get_logger("orchestrate")


def process_jobs(ws: Workspace, settings: Settings, jobs: list[JobPaths], *, render: bool = True, qc: bool = True,
                 workers: int = 1, force: bool = False, engine: str | None = None,
                 keep_frames: bool = False) -> dict[str, Any]:
    ledger = Ledger(ws.ledger_path)
    run_id = ledger.start_run("process", {"jobs": [j.key for j in jobs], "render": render, "qc": qc,
                                          "workers": workers, "engine": engine, "force": force})

    def one(job: JobPaths) -> dict[str, Any]:
        outcome: dict[str, Any] = {"job": job.key}
        try:
            scene_hash = sha256_file(job.scene)
            before = ledger.get_step(job.key, "render")
            if render:
                scene = read_json(job.scene)
                inputs = {"scene": scene_hash, "engine": engine or settings.render.engine,
                          "encode": settings.render.video.model_dump()}
                if not job.video(scene["title"]).exists():
                    force_render = True
                else:
                    force_render = force
                run_step(ledger, job.key, "render",
                         lambda: render_job(job, settings, engine=engine, keep_frames=keep_frames),
                         inputs=inputs, run_id=run_id, force=force_render, retries=0)
                after = ledger.get_step(job.key, "render")
                skipped = before is not None and after is not None and before.finished_at == after.finished_at
                outcome["render"] = "skipped (ledger: same inputs already succeeded)" if skipped else "rendered"
            if qc:
                summary = qc_job(job, settings, ledger, ws)
                ledger.begin_step(job.key, "qc", scene_hash, run_id)
                ledger.succeed_step(job.key, "qc", {"technical": summary["technical"]})
                outcome["technical"] = summary["technical"]
            return {**outcome, "status": "ok"}
        except Exception as exc:  # noqa: BLE001  (one bad job must not stop the batch)
            job.update_meta(status="failed", error=f"{type(exc).__name__}: {exc}"[:800])
            log_event(log, "job failed", job=job.key, error=str(exc)[:300])
            return {"job": job.key, "status": "failed", "error": f"{type(exc).__name__}: {exc}"[:800]}

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(one, jobs))
    failed = [r for r in results if r["status"] != "ok"]
    ledger.finish_run(run_id, "succeeded" if not failed else ("failed" if len(failed) == len(results) else "partial"))
    for batch in {j.batch_id for j in jobs}:
        batch_dir = ws.batch_dir(batch)
        write_manifest(batch_dir, ws.iter_jobs(batch))
    return {"run_id": run_id, "ok": len(results) - len(failed), "failed": failed, "jobs": results}


def resolve_targets(ws: Workspace, target: str) -> list[JobPaths]:
    """A job directory, a batch directory or a batch id."""
    from pathlib import Path

    from .layout import job_from_path

    path = Path(target)
    if path.exists() and (path / "job.json").exists():
        return [job_from_path(path)]
    if path.exists() and path.is_dir():
        return [JobPaths(p) for p in sorted(path.iterdir()) if (p / "job.json").exists()]
    if (ws.batches / target).is_dir():
        return ws.iter_jobs(target)
    raise FileNotFoundError(f"'{target}' 不是任务目录、批次目录或批次 ID")
