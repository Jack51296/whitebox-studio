"""Review export for large-scale viewing: one group per job with source / white-box / V2V slices.

With FiftyOne (Apache-2.0) installed the batch becomes a grouped dataset (slices shown side by side, tags per
sample); without it a manifest JSON is written that reviewers can edit. ``sync`` turns review tags back into
ledger marks through ``review.mark`` — a named reviewer is required, nothing is marked automatically.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..jsonio import read_json, write_json
from ..layout import JobPaths, Workspace
from ..ledger import Ledger
from . import review

TAGS = {"sampled_visual:passed": ("sampled_visual", "passed"), "sampled_visual:failed": ("sampled_visual", "failed"),
        "normal_speed:passed": ("normal_speed_viewing", "passed"), "normal_speed:failed": ("normal_speed_viewing", "failed"),
        "adopted": ("curation", "adopted"), "rejected": ("curation", "rejected")}
FIELD_TAG = {("sampled_visual", v): f"sampled_visual:{v}" for v in ("passed", "failed")} | \
    {("normal_speed_viewing", v): f"normal_speed:{v}" for v in ("passed", "failed")} | \
    {("curation", v): v for v in ("adopted", "rejected")}


def _slices(job: JobPaths) -> dict[str, str | None]:
    meta = job.read_meta()
    source = job.root / meta["source_video"] if meta.get("source_video") else None
    white = job.video(meta["title"]) if meta.get("title") else None
    results = [job.v2v_dir / "结果" / "v2v_result.mp4", *sorted((job.v2v_dir / "项目_提交包").glob("成片_*.mp4"))]
    v2v = next((p for p in results if p.exists()), None)
    return {"source": str(source) if source and source.exists() else None,
            "whitebox": str(white) if white and white.exists() else None, "v2v": str(v2v) if v2v else None}


def collect(ws: Workspace, batch: str, ledger: Ledger) -> list[dict[str, Any]]:
    groups = []
    for job in ws.iter_jobs():
        if job.batch_id != batch or not job.scene.exists():
            continue
        reviews = ledger.reviews(job.key)
        tags = sorted({FIELD_TAG[(f, r["value"])] for f, r in reviews.items() if (f, r["value"]) in FIELD_TAG})
        groups.append({"job": job.key, "job_dir": str(job.root), "title": job.read_meta().get("title"),
                       "qc": job.read_meta().get("qc_status"), "slices": _slices(job), "tags": tags})
    return groups


def manifest_path(ws: Workspace, batch: str) -> Path:
    return ws.root / "reports" / f"审片导出_{batch}.json"


def export(ws: Workspace, batch: str, ledger: Ledger, dataset: str | None = None) -> dict[str, Any]:
    groups = collect(ws, batch, ledger)
    doc = {"schema": "wbs.review_export/1.0", "batch": batch, "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "backend": "manifest", "tags_allowed": sorted(TAGS), "groups": groups,
           "how_to": "在每组的 tags 里填允许的标签后运行 wbs review sync <批次> --reviewer <姓名>；未填写的不做任何标记"}
    try:
        import fiftyone as fo
    except ImportError:
        doc["fiftyone"] = "未安装 FiftyOne（Apache-2.0，可选）：已写清单，审片人可直接编辑 tags"
    else:
        name = dataset or f"wbs_{batch}"
        ds = fo.Dataset(name, overwrite=True)
        ds.persistent = True
        ds.add_group_field("group", default="whitebox")
        samples = []
        for g in groups:
            group = fo.Group()
            for slice_name, path in g["slices"].items():
                if path:
                    sample = fo.Sample(filepath=path, group=group.element(slice_name), tags=list(g["tags"]))
                    sample["job"] = g["job"]
                    samples.append(sample)
        ds.add_samples(samples)
        doc.update(backend="fiftyone", dataset=name, samples=len(samples))
    write_json(manifest_path(ws, batch), doc)
    return {k: doc[k] for k in ("backend", "batch") if k in doc} | {"groups": len(groups), "manifest": str(manifest_path(ws, batch)),
                                                                     **({"dataset": doc["dataset"]} if "dataset" in doc else {})}


def sync(ws: Workspace, batch: str, ledger: Ledger, reviewer: str, dataset: str | None = None) -> dict[str, Any]:
    if not reviewer or not reviewer.strip():
        raise ValueError("reviewer is required (who looked at it)")
    doc = read_json(manifest_path(ws, batch))
    tags: dict[str, set[str]] = {g["job"]: set(g.get("tags", [])) for g in doc["groups"]}
    if doc.get("backend") == "fiftyone":
        import fiftyone as fo

        ds = fo.load_dataset(dataset or doc["dataset"])
        for sample in ds.select_group_slices():
            tags.setdefault(sample["job"], set()).update(sample.tags)
    applied, ignored = [], []
    for g in doc["groups"]:
        job = JobPaths(Path(g["job_dir"]))
        before = set(g.get("tags_synced", []))
        for tag in sorted(tags.get(g["job"], set()) - before):
            if tag not in TAGS:
                ignored.append({"job": g["job"], "tag": tag})
                continue
            field, value = TAGS[tag]
            review.mark(job, ledger, field, value, reviewer, note=f"审片导出同步（{doc.get('backend')}）")
            applied.append({"job": g["job"], "field": field, "value": value})
        g["tags_synced"] = sorted(before | (tags.get(g["job"], set()) & set(TAGS)))
    write_json(manifest_path(ws, batch), doc)
    return {"applied": applied, "ignored": ignored, "reviewer": reviewer.strip()}
