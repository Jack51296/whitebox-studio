"""Mock end-to-end acceptance: every route through the real CLI, real Blender rendering, mock models.

Usage:  python scripts/e2e_mock.py [--quick]
Batch IDs carry a timestamp, so nothing from earlier runs is overwritten. Human review fields are
never filled in by this script (they stay not_run / pending, as the platform requires).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENV = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
ENV.pop("WBS_CONFIRM_PAID", None)


def wbs(*args: str) -> dict | str:
    started = time.time()
    proc = subprocess.run([sys.executable, "-m", "wbs.cli", *args], cwd=REPO, env=ENV, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    took = time.time() - started
    print(f"$ wbs {' '.join(args)}  [{took:.1f}s, exit {proc.returncode}]", flush=True)
    if proc.returncode != 0:
        print(proc.stdout[-3000:], proc.stderr[-3000:], sep="\n", flush=True)
        raise SystemExit(f"step failed: wbs {' '.join(args)}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return proc.stdout


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="2 batch jobs instead of 4")
    args = parser.parse_args()
    stamp = time.strftime("%m%d%H%M")
    summary: dict = {"started_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "steps": {}}

    doctor = wbs("doctor")
    workspace = Path(doctor["workspace"])
    summary["steps"]["doctor"] = {"ok": doctor["ok"], "tools": {k: v["version"] for k, v in doctor["tools"].items()}}

    fwd = f"E2E{stamp}_FWD"
    res = wbs("forward", "batch", "--batch", fwd, "--n", "2" if args.quick else "4", "--seed", "7", "--workers", "2")
    summary["steps"]["forward_batch"] = res
    jobs = sorted((workspace / "batches" / fwd).glob(f"{fwd}_*"))
    first = jobs[0]

    started = time.time()
    resume = wbs("render", fwd, "--no-qc")
    skipped = [j for j in resume["jobs"] if str(j.get("render", "")).startswith("skipped")]
    summary["steps"]["resume_check"] = {"seconds": round(time.time() - started, 1), "jobs": len(resume["jobs"]),
                                        "skipped": len(skipped)}
    if len(skipped) != len(resume["jobs"]):
        raise SystemExit("resume check failed: re-running the same batch re-rendered jobs")

    story = f"E2E{stamp}_STORY"
    summary["steps"]["forward_story"] = wbs("forward", "story", "--batch", story, "--job", "S001",
                                            "--brief", "快递员穿过街区送最后一单，同伴在后面追赶", "--content-class", "motion")
    sjob = workspace / "batches" / story / "S001"
    summary["steps"]["forward_revise"] = wbs("forward", "revise", str(sjob), "--notes", "第二镜更近一些，结尾停留更久",
                                             "--level", "shots")
    if not (sjob / "versions" / "v01" / "scene.json").exists():
        raise SystemExit("revise check failed: previous delivery was not backed up to versions/v01")
    summary["steps"]["card_compare"] = json.loads((sjob / "reports" / "导演卡对照.json").read_text(encoding="utf-8"))
    lt = f"E2E{stamp}_LT"
    summary["steps"]["forward_longtake"] = wbs("forward", "longtake", "--batch", lt, "--job", "L001",
                                               "--brief", "从门厅一路走到终点平台的连续空间", "--zones", "5")

    rev = f"E2E{stamp}_REV"
    video = next(first.glob("*_白模参考.mp4"))
    summary["steps"]["reverse_analyze"] = wbs("reverse", "analyze", "--batch", rev, "--job", "R001", "--video", str(video),
                                              "--license", "自有生成：whitebox-studio 白模渲染（验收用）")
    rjob = workspace / "batches" / rev / "R001"
    summary["steps"]["reverse_solve"] = wbs("reverse", "solve", str(rjob))
    summary["steps"]["reverse_online_package"] = wbs("reverse", "online-package", str(rjob))

    summary["steps"]["v2v_run"] = wbs("v2v", "run", str(first), "--max-images", "4")
    summary["steps"]["v2v_submit"] = wbs("v2v", "submit", str(first))
    summary["steps"]["v2v_run_from_folder"] = wbs("v2v", "run", "--inputs", str(sjob), "--batch", f"E2E{stamp}_V2V",
                                                  "--job", "V001", "--max-images", "3")
    team = REPO / "references" / "whitebox-world-studio-1.2.0" / "01_塔背的出口_单人立体穿梭"
    if (team / "塔背的出口_白模参考.mp4").exists():
        summary["steps"]["v2v_team_regression"] = wbs("v2v", "run", "--inputs", str(team), "--batch", f"E2E{stamp}_V2V",
                                                      "--job", "TOWER", "--max-images", "4")

    for batch in (fwd, story, lt, rev):
        summary["steps"][f"finish_{batch}"] = wbs("batch", "finish", batch)
    summary["steps"]["registry"] = wbs("registry", "scan")
    summary["steps"]["dashboard"] = wbs("dashboard")
    summary["steps"]["cost"] = wbs("cost", "report", "--format", "json")
    summary["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    summary["batches"] = [fwd, story, lt, rev, f"E2E{stamp}_V2V"]
    out = workspace / f"e2e_summary_{stamp}.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("E2E_OK", out, flush=True)


if __name__ == "__main__":
    main()
