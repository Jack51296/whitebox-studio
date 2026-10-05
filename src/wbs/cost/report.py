"""Spend reports per batch / job / model, from the ledger usage table."""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from typing import Any

from ..ledger import Ledger
from .pricing import usd_to_cny


def summarize(ledger: Ledger, *, batch_id: str | None = None, job_key: str | None = None) -> dict[str, Any]:
    rows = ledger.usage_rows(batch_id=batch_id, job_key=job_key)
    rate = usd_to_cny()

    def bucket() -> dict[str, Any]:
        return {"calls": 0, "input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "images": 0,
                "video_seconds": 0.0, "cost_usd": 0.0, "unknown_cost_calls": 0}

    totals, by_job, by_model = bucket(), defaultdict(bucket), defaultdict(bucket)
    dry = bucket()
    for row in rows:
        targets = [dry] if row["dry_run"] else [totals, by_job[row["job_key"]], by_model[row["model"] or row["provider"]]]
        for target in targets:
            target["calls"] += 1
            for key in ("input_tokens", "cached_input_tokens", "output_tokens", "images"):
                target[key] += int(row[key] or 0)
            target["video_seconds"] += float(row["video_seconds"] or 0)
            if row["cost_usd"] is None:
                target["unknown_cost_calls"] += 1
            else:
                target["cost_usd"] += float(row["cost_usd"])
    for group in [totals, dry, *by_job.values(), *by_model.values()]:
        group["cost_usd"] = round(group["cost_usd"], 4)
        group["cost_cny"] = round(group["cost_usd"] * rate, 2)
    return {"usd_to_cny": rate, "totals": totals, "dry_run_estimates": dry,
            "by_job": dict(by_job), "by_model": dict(by_model)}


def to_markdown(summary: dict[str, Any], title: str = "成本报表") -> str:
    t = summary["totals"]
    lines = [f"# {title}", "",
             f"- 实际调用：{t['calls']} 次，金额约 ¥{t['cost_cny']}（${t['cost_usd']}，汇率 {summary['usd_to_cny']}）",
             f"- 单价未知的调用：{t['unknown_cost_calls']} 次（金额未计入，请补全 configs/pricing.yaml）",
             f"- dry-run 预估：{summary['dry_run_estimates']['calls']} 次，约 ¥{summary['dry_run_estimates']['cost_cny']}",
             "", "## 按模型", "", "| 模型 | 调用 | 输入 token | 缓存输入 | 输出 token | 图片 | 视频秒 | 金额（¥） | 单价未知 |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for model, b in sorted(summary["by_model"].items()):
        lines.append(f"| {model} | {b['calls']} | {b['input_tokens']} | {b['cached_input_tokens']} | {b['output_tokens']}"
                     f" | {b['images']} | {b['video_seconds']:.1f} | {b['cost_cny']} | {b['unknown_cost_calls']} |")
    lines += ["", "## 按任务", "", "| 任务 | 调用 | 金额（¥） | 单价未知 |", "| --- | --- | --- | --- |"]
    for job, b in sorted(summary["by_job"].items()):
        lines.append(f"| {job} | {b['calls']} | {b['cost_cny']} | {b['unknown_cost_calls']} |")
    return "\n".join(lines) + "\n"


def to_csv(ledger: Ledger, path: Path, *, batch_id: str | None = None) -> Path:
    rows = ledger.usage_rows(batch_id=batch_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        fields = ["created_at", "batch_id", "job_key", "step", "provider", "model", "request_id", "input_tokens",
                  "cached_input_tokens", "output_tokens", "images", "video_seconds", "cost_usd", "dry_run"]
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path
