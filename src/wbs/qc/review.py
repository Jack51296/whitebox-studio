"""Human review marks (the only way sampled_visual / normal_speed_viewing / curation change)."""

from __future__ import annotations

from typing import Any

from ..jsonio import read_json, write_json
from ..layout import JobPaths
from ..ledger import Ledger

FIELDS = {"sampled_visual": {"passed", "failed", "not_run"},
          "normal_speed_viewing": {"passed", "failed", "not_run"},
          "curation": {"adopted", "rejected", "pending"}}


def mark(job: JobPaths, ledger: Ledger, field: str, value: str, reviewer: str, note: str = "") -> dict[str, Any]:
    if field not in FIELDS:
        raise ValueError(f"field must be one of {sorted(FIELDS)}")
    if value not in FIELDS[field]:
        raise ValueError(f"{field} value must be one of {sorted(FIELDS[field])}")
    if not reviewer.strip():
        raise ValueError("reviewer is required (who looked at it)")
    ledger.add_review(job.key, field, value, reviewer.strip(), note)
    summary_path = job.report("质检汇总.json")
    if summary_path.exists():
        summary = read_json(summary_path)
        summary[field] = value
        summary["reviews"] = ledger.reviews(job.key)
        write_json(summary_path, summary)
    exp_path = job.report("experience-report.json")
    if exp_path.exists():
        exp = read_json(exp_path)
        if field == "normal_speed_viewing":
            exp["normal_speed_review"] = {"status": value, "observed": note or "人工正常速度观看", "reviewer": reviewer,
                                          "evidence_paths": []}
            exp["status"] = f"rendered_test; normal_speed_viewing_{value}"
        elif field == "sampled_visual":
            exp["checks"]["sampled_frame_review"].update(status=value, observed=note or "人工抽帧复核", reviewer=reviewer)
        else:
            exp["user_acceptance"] = value
        write_json(exp_path, exp)
    job.update_meta(**{f"review_{field}": value})
    return ledger.reviews(job.key)
