"""Reverse benchmark on self-rendered truth videos: cuts, subject occupancy, camera trajectory per backend.

  python scripts/reverse_benchmark.py                       # render the truth set once (BENCH_TRUTH), evaluate all backends
  python scripts/reverse_benchmark.py --extra <job_dir> ... # add rendered jobs that have scene.json + audit/samples.json
  python scripts/reverse_benchmark.py --no-geometry --subjects motion

Writes workspace/reports/reverse_benchmark_<stamp>.json / .md. Optional backends that are not installed are
reported as falling back (their rows then show the built-in result).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wbs.config import load_settings  # noqa: E402
from wbs.jsonio import write_json  # noqa: E402
from wbs.layout import Workspace, job_from_path  # noqa: E402
from wbs.orchestrate import process_jobs  # noqa: E402
from wbs.reverse import benchmark  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--batch", default="BENCH_TRUTH")
    parser.add_argument("--extra", nargs="*", default=[], help="extra rendered job directories")
    parser.add_argument("--cuts", default="builtin,pyscenedetect,transnetv2")
    parser.add_argument("--subjects", default="motion,grounding_dino")
    parser.add_argument("--no-geometry", action="store_true")
    parser.add_argument("--prompt", default="figure.", help="text prompt for grounding_dino on white-box figures")
    args = parser.parse_args()

    settings = load_settings()
    ws = Workspace(settings.workspace).ensure()
    jobs = benchmark.truth_jobs(ws, args.batch)
    missing = [j for j in jobs if not j.samples.exists() or not list(j.root.glob("*_白模参考.mp4"))]
    if missing:
        print(f"rendering {len(missing)} truth videos …", flush=True)
        process_jobs(ws, settings, missing, qc=False, workers=2, force=True)
    jobs += [job_from_path(Path(p)) for p in args.extra]
    out_dir = ws.root / "reports"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    report = benchmark.run(jobs, out_dir / f"reverse_benchmark_{stamp}", cut_backends=tuple(args.cuts.split(",")),
                           subject_backends=tuple(args.subjects.split(",")), geometry=not args.no_geometry,
                           prompt=args.prompt)
    write_json(out_dir / f"reverse_benchmark_{stamp}.json", report)
    (out_dir / f"reverse_benchmark_{stamp}.md").write_text(benchmark.markdown(report), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("cuts", "subject", "camera", "recommendation")}, ensure_ascii=False, indent=2))
    print("REPORT", out_dir / f"reverse_benchmark_{stamp}.md", flush=True)


if __name__ == "__main__":
    main()
