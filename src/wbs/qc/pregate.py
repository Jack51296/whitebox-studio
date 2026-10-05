"""Pre-render gate shared by the forward routes: motion/clearance gate, occlusion-aware framing, camera-language
and readability report. Grammar and readability only fail the gate when configured to (qc.grammar/readability.enforce).
"""

from __future__ import annotations

from typing import Any, Literal

from ..config import Settings
from ..jsonio import write_json
from ..layout import JobPaths
from .dynamic import dynamic_gate, framing_precheck
from .grammar import grammar_report

GRAMMAR_REPORT = "镜头语法与可读性.json"


def pregate(scene: dict, settings: Settings, motion: Literal["enforce", "warn"] = "enforce") -> dict[str, Any]:
    gate = dynamic_gate(scene, settings.qc.dynamic_gate, motion=motion)
    framing = framing_precheck(scene, cfg=settings.qc.framing)
    grammar = grammar_report(scene, settings.qc, framing)
    passed = gate["status"] == "passed" and framing["status"] != "failed" and grammar["status"] != "failed"
    return {"status": "passed" if passed else "failed", "gate": gate, "framing": framing, "grammar": grammar}


def write_pregate(job: JobPaths, result: dict[str, Any], **extra: Any) -> None:
    write_json(job.report("制作数据检查.json"), {**result["gate"], "stage": "pre_render",
                                                "framing_precheck": result["framing"], **extra})
    write_json(job.report(GRAMMAR_REPORT), {**result["grammar"], "stage": "pre_render"})
