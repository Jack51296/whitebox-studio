"""Workspace and job directory conventions (the on-disk contract every stage relies on)."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .jsonio import read_json, write_json

SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
UNSAFE_FILENAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def check_id(value: str, what: str = "id") -> str:
    if not SAFE_ID.match(value):
        raise ValueError(f"invalid {what} '{value}': use letters, digits, '_' or '-' (max 64)")
    return value


def safe_filename(title: str, fallback: str = "untitled") -> str:
    cleaned = UNSAFE_FILENAME.sub("_", title).strip(" .")
    return cleaned or fallback


@dataclass(frozen=True)
class JobPaths:
    root: Path

    @property
    def job_id(self) -> str:
        return self.root.name

    @property
    def batch_id(self) -> str:
        return self.root.parent.name

    @property
    def key(self) -> str:
        return f"{self.batch_id}/{self.job_id}"

    job_json = property(lambda self: self.root / "job.json")
    scene = property(lambda self: self.root / "scene.json")
    control_spec = property(lambda self: self.root / "control_spec.json")
    story_plan = property(lambda self: self.root / "story_plan.json")
    longtake_plan = property(lambda self: self.root / "longtake_plan.json")
    prompts_dir = property(lambda self: self.root / "prompts")
    render_dir = property(lambda self: self.root / "render")
    frames_dir = property(lambda self: self.root / "render" / "frames")
    keyframes_dir = property(lambda self: self.root / "render" / "keyframes")
    audit_dir = property(lambda self: self.root / "audit")
    samples = property(lambda self: self.root / "audit" / "samples.json")
    blocks = property(lambda self: self.root / "audit" / "blocks.json")
    reports_dir = property(lambda self: self.root / "reports")
    storyboard_dir = property(lambda self: self.root / "故事板")
    overview = property(lambda self: self.root / "分镜总览.jpg")
    director_card = property(lambda self: self.root / "剧本与分镜导演卡.txt")
    continuation = property(lambda self: self.root / "视频续作提示词.txt")
    vtt = property(lambda self: self.root / "导演注释.vtt")
    route_map = property(lambda self: self.root / "路线俯视图.png")
    analysis_dir = property(lambda self: self.root / "analysis")
    v2v_dir = property(lambda self: self.root / "v2v")

    def report(self, name: str) -> Path:
        return self.reports_dir / name

    def title(self) -> str:
        meta = self.read_meta()
        return str(meta.get("title") or self.job_id)

    def video(self, title: str | None = None) -> Path:
        return self.root / f"{safe_filename(title or self.title(), self.job_id)}_白模参考.mp4"

    def blend(self, title: str | None = None) -> Path:
        return self.root / f"{safe_filename(title or self.title(), self.job_id)}.blend"

    def read_meta(self) -> dict[str, Any]:
        return read_json(self.job_json) if self.job_json.exists() else {}

    def update_meta(self, **fields: Any) -> dict[str, Any]:
        meta = self.read_meta()
        meta.update(fields)
        meta["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        write_json(self.job_json, meta)
        return meta


@dataclass(frozen=True)
class Workspace:
    root: Path

    @property
    def ledger_path(self) -> Path:
        return self.root / ".wbs" / "ledger.sqlite"

    @property
    def batches(self) -> Path:
        return self.root / "batches"

    @property
    def registry(self) -> Path:
        return self.root / "registry"

    def ensure(self) -> Workspace:
        for path in (self.root / ".wbs", self.batches, self.registry, self.root / "logs"):
            path.mkdir(parents=True, exist_ok=True)
        return self

    def batch_dir(self, batch_id: str) -> Path:
        return self.batches / check_id(batch_id, "batch id")

    def job(self, batch_id: str, job_id: str) -> JobPaths:
        return JobPaths(self.batch_dir(batch_id) / check_id(job_id, "job id"))

    def iter_jobs(self, batch_id: str | None = None) -> list[JobPaths]:
        roots = [self.batch_dir(batch_id)] if batch_id else sorted(p for p in self.batches.glob("*") if p.is_dir())
        jobs: list[JobPaths] = []
        for batch in roots:
            jobs.extend(JobPaths(p) for p in sorted(batch.glob("*")) if (p / "job.json").exists())
        return jobs


def job_from_path(path: Path) -> JobPaths:
    path = Path(path).resolve()
    if path.is_file():
        path = path.parent
    if not (path / "job.json").exists():
        raise FileNotFoundError(f"{path} is not a job directory (job.json missing)")
    return JobPaths(path)
