"""Stage-A comparison: single-shot planning vs the layered chain, measured against the team director cards.

  python scripts/story_mode_compare.py            # mock planner (structure only, no model quality claim)
  python scripts/story_mode_compare.py --confirm-paid   # with a real planner configured in configs/providers.yaml

Writes workspace/reports/story_mode_compare_<stamp>.{json,md}.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wbs.config import load_settings  # noqa: E402
from wbs.forward import texts  # noqa: E402
from wbs.forward.story import plan_story, story_to_scene  # noqa: E402
from wbs.forward.story_chain import mechanical_checks, run_chain  # noqa: E402
from wbs.forward.story_examples import compare_card  # noqa: E402
from wbs.jsonio import write_json  # noqa: E402
from wbs.layout import Workspace  # noqa: E402
from wbs.ledger import Ledger  # noqa: E402
from wbs.providers import CallContext, get_providers  # noqa: E402

BRIEFS = [("narrative", "快递员把最后一单交错了人，两人在楼道里互相追问"),
          ("narrative", "两个孩子在天台争一只风筝，风筝线缠住了旧天线"),
          ("narrative", "修车工替陌生人挡住了追债的人，却发现对方是自己的债主"),
          ("motion", "两人抢一只滚动的箱子，最后一起抬走"),
          ("motion", "滑板少年在窄巷里甩开追赶者，最后借坡飞上平台"),
          ("motion", "两名跑者在环形跑道上交替领先，最后一圈同时冲线")]


def measure(card: str, content_class: str, plan) -> dict:
    report = compare_card(card, content_class)
    gen = report["generated"]
    lengths = gen["shot_lengths"] or [0.0]
    checks = mechanical_checks(plan)
    return {"shots": gen["shots"], "median_shot_s": round(statistics.median(lengths), 3), "max_shot_s": round(max(lengths), 3),
            "events": len(gen["events"]), "setup_twist_marked": "（转折）" in card and "（铺垫）" in card,
            "route_beats": "路线与空间交接" in card, "sections": len(gen["sections"]),
            "notes_vs_team": len(report["notes"]), "mechanical_warnings": sum(c["status"] != "ok" for c in checks)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--confirm-paid", action="store_true")
    args = parser.parse_args()
    settings = load_settings()
    ws = Workspace(settings.workspace).ensure()
    providers = get_providers(settings, Ledger(ws.ledger_path), confirmed=args.confirm_paid)
    spec = settings.spec("forward_story")
    rows = []
    for n, (cls, brief) in enumerate(BRIEFS, 1):
        for mode in ("single", "chain"):
            ctx = CallContext(f"STORY_COMPARE/{mode}_{n}", "story_plan")
            plan, info = (plan_story(brief, providers, ctx, spec, cls) if mode == "single"
                          else run_chain(brief, providers, ctx, spec, cls))
            scene = story_to_scene(plan, spec, f"{mode}_{n}", brief).to_json_dict()
            rows.append({"brief": brief, "class": cls, "mode": mode, "simulated": info.get("simulated"),
                         **measure(texts.director_card(scene, None), cls, plan)})
    summary = {}
    for mode in ("single", "chain"):
        sub = [r for r in rows if r["mode"] == mode]
        summary[mode] = {k: round(statistics.mean(float(r[k]) for r in sub), 3)
                         for k in ("shots", "median_shot_s", "max_shot_s", "events", "setup_twist_marked", "route_beats",
                                   "notes_vs_team", "mechanical_warnings")}
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = ws.root / "reports" / f"story_mode_compare_{stamp}"
    doc = {"schema": "wbs.story_mode_compare/1.0", "simulated": any(r["simulated"] for r in rows), "rows": rows, "summary": summary,
           "team_reference": {c: compare_card("", c)["team_reference"] for c in ("narrative", "motion")}}
    write_json(out.with_suffix(".json"), doc)
    lines = ["# 阶段 A：单次规划 vs 分层链（导演卡结构对照）", "",
             ("mock 规划：只比较结构，不代表真实模型的故事质量。" if doc["simulated"] else "真实规划模型输出。"), "",
             "| 模式 | 镜头数 | 镜长中位 s | 最长镜 s | 事件数 | 标出铺垫/转折 | 路线交接段 | 与团队样例的差异条数 | 机械检查警告 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for mode, s in summary.items():
        lines.append(f"| {mode} | {s['shots']} | {s['median_shot_s']} | {s['max_shot_s']} | {s['events']} | "
                     f"{s['setup_twist_marked']:.0%} | {s['route_beats']:.0%} | {s['notes_vs_team']} | {s['mechanical_warnings']} |")
    ref = doc["team_reference"]
    if ref["narrative"]["shot_length_s"] and ref["motion"]["shot_length_s"]:
        lines += ["", f"团队样例：叙事类镜长中位 {ref['narrative']['shot_length_s']['median']} s（最长 {ref['narrative']['shot_length_s']['max']}），"
                  f"运动类镜长中位 {ref['motion']['shot_length_s']['median']} s（最长 {ref['motion']['shot_length_s']['max']}）。"]
    else:
        lines += ["", "没有找到团队样例（references/samples 缺失），未与团队镜长对照。"]
    out.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("REPORT", out.with_suffix(".md"))


if __name__ == "__main__":
    main()
