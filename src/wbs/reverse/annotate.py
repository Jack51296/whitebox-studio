"""Optional vision annotation of subject boxes ([D2] ② AI 看图). Mock returns the automatic measurement.

With a real vision model configured (and paid calls confirmed) the model's boxes replace the motion-
saliency occupancy; every value records where it came from.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..jsonio import extract_json, write_json
from ..prompts import render
from ..providers import CallContext, Providers
from ..providers.mock import register_mock

ROLES = ("first", "mid", "last")


def _box_entry(box: list[float], frame: int) -> dict[str, Any]:
    x0, y0, x1, y1 = (max(0.0, min(1.0, float(v))) for v in box)
    x0, x1 = min(x0, x1), max(x0, x1)
    y0, y1 = min(y0, y1), max(y0, y1)
    return {"frame": frame, "bbox": [round(v, 4) for v in (x0, y0, x1, y1)], "area": round((x1 - x0) * (y1 - y0), 5),
            "center": [round((x0 + x1) / 2, 4), round((y0 + y1) / 2, 4)], "source": "vlm"}


def annotate(analysis: dict, analysis_dir: Path, providers: Providers, ctx: CallContext) -> dict[str, Any]:
    images: list[Path] = []
    shots = []
    auto = {}
    for s in analysis["shots"]:
        names = []
        for role in ROLES:
            file = s.get("frame_files", {}).get(role)
            if file:
                images.append(analysis_dir / file)
                names.append(f"图{len(images)}")
        shots.append({"id": s["id"], "start_s": s["start_s"], "end_s": s["end_s"], "images": names})
        auto[s["id"]] = {role: (v["bbox"] if v else None) for role, v in s["occupancy"].items()}
    user, ref = render("reverse.annotate_subjects", shots=shots, auto_json=json.dumps(auto, ensure_ascii=False))
    result = providers.llm.complete(ctx, system="只输出 JSON。", user=user, images=images, json_mode=True,
                                    task="reverse_annotate", payload={"auto": auto})
    data = extract_json(result.text)
    source = "motion_saliency (mock echo)" if result.simulated else f"vlm:{result.model}"
    for s in analysis["shots"]:
        boxes = (data.get("shots") or {}).get(s["id"]) or {}
        for role, index in zip(ROLES, s["first_mid_last_frames"]):
            box = boxes.get(role)
            if not result.simulated and box:
                s["occupancy"][role] = _box_entry(box, index)
            elif not result.simulated and box is None:
                s["occupancy"][role] = None
    analysis["annotation"] = {"source": source, "prompt_ref": ref, "simulated": result.simulated,
                              "subject_kind": data.get("subject_kind"), "subject_count": data.get("subject_count")}
    write_json(analysis_dir / "analysis.json", analysis)
    return analysis["annotation"]


@register_mock("reverse_annotate")
def _mock_annotate(payload: dict[str, Any]) -> str:
    return json.dumps({"subject_kind": None, "subject_count": None, "shots": payload.get("auto", {})}, ensure_ascii=False)
