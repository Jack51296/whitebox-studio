"""Gate-driven corrections ([D2] 闸门不通过时按原因修正). An agent/LLM proposes adjustments (mock by default).

Supported adjustments (applied to scene.json only, never to scripts):
  mirror_x                     — negate X of every camera/aim key, actor key and block (one-shot flip)
  shots[id].yaw_offset_deg     — rotate the whole camera track about the subject (orientation only)
  shots[id].distance_scale     — move the camera along its line to the subject (occupancy mismatch)
"""

from __future__ import annotations

import copy
import json
import math
from typing import Any

from ..blender import kinematics as K
from ..jsonio import extract_json
from ..models.scene import SceneSpec
from ..prompts import render
from ..providers import CallContext, Providers
from ..providers.mock import register_mock

SYSTEM = "你是白模反推修正代理。只输出 JSON：{\"mirror_x\": bool, \"shots\": {\"S01\": {\"yaw_offset_deg\": 数, \"distance_scale\": 数}}, \"notes\": \"...\"}"


def summarize_analysis(analysis: dict) -> str:
    shots = [{"id": s["id"], "start_s": s["start_s"], "end_s": s["end_s"],
              "flow": {k: s["flow"][k] for k in ("zoom_total", "pan_x_total", "pan_y_total", "roll_total_deg")},
              "occupancy": {k: (v and {"area": v["area"], "center": v["center"]}) for k, v in s["occupancy"].items()},
              "lines_mid": [ln["angle_deg"] for ln in s["lines"]["mid"]],
              "camera_motion": {k: s.get("camera_motion", {}).get(k) for k in ("labels", "ambiguous", "basis")}}
             for s in analysis["shots"]]
    return json.dumps({"video": analysis["video"], "cuts_s": analysis["cuts"]["times_s"], "shots": shots},
                      ensure_ascii=False)


def propose(analysis: dict, gate: dict, providers: Providers, ctx: CallContext) -> tuple[dict[str, Any], dict[str, Any]]:
    user, ref = render("reverse.refine_agent", analysis_summary=summarize_analysis(analysis),
                       gate_report=json.dumps(_gate_digest(gate), ensure_ascii=False))
    user += "\n\n本系统只接受修正参数 JSON（见 system 说明），由程序改 scene.json。"
    result = providers.llm.complete(ctx, system=SYSTEM, user=user, json_mode=True, task="reverse_refine",
                                    payload={"gate": _gate_digest(gate)})
    adj = extract_json(result.text)
    return adj, {"prompt_ref": ref, "model": result.model, "simulated": result.simulated}


def _gate_digest(gate: dict) -> dict[str, Any]:
    shots = []
    for s in gate.get("shots", []):
        occ = {role: f.get("occupancy") for role, f in s["frames"].items()}
        shots.append({"id": s["id"], "mirror": s["mirror"].get("status"), "occupancy": occ})
    return {"status": gate.get("status"), "checks": gate.get("checks"), "shots": shots}


@register_mock("reverse_refine")
def _mock_refine(payload: dict[str, Any]) -> str:
    gate = payload.get("gate", {})
    adj: dict[str, Any] = {"mirror_x": (gate.get("checks") or {}).get("mirror") == "failed", "shots": {},
                           "notes": "mock 修正：按闸门数值规则给出，未调用真实模型"}
    for shot in gate.get("shots", []):
        ratios = [o["render"] / o["source"] for o in shot["occupancy"].values()
                  if o and o.get("status") == "failed" and o.get("source") and o.get("render")]
        if ratios:
            ratio = sum(ratios) / len(ratios)
            adj["shots"][shot["id"]] = {"distance_scale": round(max(0.4, min(2.5, math.sqrt(ratio))), 3)}
    return json.dumps(adj, ensure_ascii=False)


def apply(scene: dict, adj: dict[str, Any]) -> dict:
    out = copy.deepcopy(scene)
    if adj.get("mirror_x"):
        for shot in out["shots"]:
            cam = shot["camera"]
            for key in ("keys", "aim_keys"):
                cam[key] = [[k[0], -k[1], k[2], k[3]] for k in cam.get(key, [])]
            cam["roll_keys"] = [[r[0], -r[1]] for r in cam.get("roll_keys", [])]
        for actor in out.get("actors", []):
            actor["path"]["keys"] = [[k[0], -k[1], k[2], k[3]] for k in actor["path"]["keys"]]
            actor["yaw_keys"] = [[y[0], -y[1]] for y in actor.get("yaw_keys", [])]
        for block in out.get("blocks", []):
            block["center"][0] = -block["center"][0]
            rot = block.get("rotation_deg") or [0.0, 0.0, 0.0]
            block["rotation_deg"] = [rot[0], -rot[1], -rot[2]]
    ev = K.SceneEvaluator(out) if out.get("actors") else None
    for shot in out["shots"]:
        params = (adj.get("shots") or {}).get(shot["id"]) or {}
        if not params or ev is None:
            continue
        cam = shot["camera"]
        aid = out["actors"][0]["id"]
        yaw = math.radians(float(params.get("yaw_offset_deg", 0.0)))
        scale = float(params.get("distance_scale", 1.0))
        mid = (shot["start_s"] + shot["end_s"]) / 2
        pivot = ev.actor_state(aid, mid)[0]
        c, s = math.cos(yaw), math.sin(yaw)

        def move(k: list[float], pivot=pivot, c=c, s=s, scale=scale, aid=aid) -> list[float]:
            subj = ev.actor_state(aid, k[0])[0]
            x, y, z = k[1] - pivot[0], k[2] - pivot[1], k[3]
            x, y = x * c - y * s, x * s + y * c
            x, y = x + pivot[0], y + pivot[1]
            return [k[0], subj[0] + (x - subj[0]) * scale, subj[1] + (y - subj[1]) * scale,
                    subj[2] + 1.0 + (z - subj[2] - 1.0) * scale]

        old = {round(k[0], 5): k for k in cam["keys"]}
        cam["keys"] = [[m[0]] + [round(v, 4) for v in m[1:]] for m in (move(k) for k in cam["keys"])]
        new = {round(k[0], 5): k for k in cam["keys"]}
        if cam.get("aim_keys"):
            shifted = []
            for a in cam["aim_keys"]:
                t = round(a[0], 5)
                if t in old:
                    dx, dy, dz = (new[t][i] - old[t][i] for i in (1, 2, 3))
                    rx, ry = a[1] - old[t][1], a[2] - old[t][2]
                    rx, ry = rx * c - ry * s, rx * s + ry * c
                    shifted.append([a[0], round(new[t][1] + rx, 4), round(new[t][2] + ry, 4), round(a[3] + dz, 4)])
                else:
                    shifted.append(a)
            cam["aim_keys"] = shifted
    SceneSpec.model_validate(out)
    return out
