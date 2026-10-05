"""Per-shot camera-motion labels in the CameraBench (NeurIPS 2025) primitive vocabulary.

``rules`` (default): from background flow; pan↔truck, tilt↔pedestal and dolly↔zoom cannot be told apart
in 2D and are listed as ambiguous. With a geometry track (real 6-DoF cameras) the labels come from the
measured rotation / translation / focal change and the ambiguity disappears. ``llm`` asks the configured
vision-language model with the same evidence plus first/mid/last frames (mock echoes the rules).
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from ..jsonio import extract_json
from ..prompts import render
from ..providers import CallContext, Providers
from ..providers.mock import register_mock

TAXONOMY = {
    "static": "固定", "pan_left": "左摇", "pan_right": "右摇", "tilt_up": "上摇", "tilt_down": "下摇",
    "roll_cw": "顺时针滚转", "roll_ccw": "逆时针滚转", "zoom_in": "变焦推", "zoom_out": "变焦拉",
    "dolly_in": "前推（移）", "dolly_out": "后拉（移）", "truck_left": "左移", "truck_right": "右移",
    "pedestal_up": "升", "pedestal_down": "降", "arc_left": "左环绕", "arc_right": "右环绕", "shaky": "手持抖动",
}
AMBIGUOUS_2D = {"pan": "摇↔横移（单目二维光流无法区分，需几何求解或看图）", "tilt": "俯仰摇↔升降",
                "scale": "推移↔变焦"}


def flow_labels(flow: dict[str, Any], min_pan: float = 0.05, min_scale: float = 0.05, min_roll: float = 2.0,
                shaky: float = 0.004) -> dict[str, Any]:
    labels, ambiguous = [], []
    if abs(flow["pan_x_total"]) >= min_pan:
        labels.append("pan_left" if flow["pan_x_total"] > 0 else "pan_right")
        ambiguous.append(AMBIGUOUS_2D["pan"])
    if abs(flow["pan_y_total"]) >= min_pan:
        labels.append("tilt_up" if flow["pan_y_total"] > 0 else "tilt_down")
        ambiguous.append(AMBIGUOUS_2D["tilt"])
    if abs(flow["zoom_total"] - 1.0) >= min_scale:
        labels.append("dolly_in" if flow["zoom_total"] > 1 else "dolly_out")
        ambiguous.append(AMBIGUOUS_2D["scale"])
    if abs(flow["roll_total_deg"]) >= min_roll:
        labels.append("roll_cw" if flow["roll_total_deg"] > 0 else "roll_ccw")
    dx = np.array([p["dx"] for p in flow.get("per_frame", []) if p.get("ok")], dtype=float)
    jitter = 0.0
    if len(dx) >= 7:
        smooth = np.convolve(dx, np.ones(5) / 5, mode="same")
        jitter = float(np.std((dx - smooth)[2:-2]))
        if jitter >= shaky:
            labels.append("shaky")
    return {"labels": labels or ["static"], "ambiguous": ambiguous, "basis": "flow",
            "evidence": {k: flow[k] for k in ("pan_x_total", "pan_y_total", "zoom_total", "roll_total_deg")} |
            {"jitter": round(jitter, 5)}}


def _euler_yxz(r: np.ndarray) -> tuple[float, float, float]:
    """Camera-frame rotation (OpenCV axes) → yaw about y (pan), pitch about x (tilt), roll about z, degrees."""
    yaw = math.degrees(math.atan2(r[0, 2], r[2, 2]))
    pitch = math.degrees(math.asin(max(-1.0, min(1.0, -r[1, 2]))))
    roll = math.degrees(math.atan2(r[1, 0], r[1, 1]))
    return yaw, pitch, roll


def geometry_labels(c2w: np.ndarray, K: np.ndarray, depth_median: float, min_rot: float = 3.0,
                    min_move: float = 0.05, min_zoom: float = 0.05) -> dict[str, Any]:
    """Labels from real cameras (OpenCV convention c2w, first frame as reference)."""
    r0, c0 = c2w[0, :3, :3], c2w[0, :3, 3]
    rel = r0.T @ c2w[-1, :3, :3]
    yaw, pitch, roll = _euler_yxz(rel)
    move = r0.T @ (c2w[-1, :3, 3] - c0) / max(depth_median, 1e-6)
    zoom = float(K[-1, 0, 0] / K[0, 0, 0])
    labels = []
    if abs(yaw) >= min_rot:
        labels.append("pan_right" if yaw > 0 else "pan_left")
    if abs(pitch) >= min_rot:
        labels.append("tilt_up" if pitch > 0 else "tilt_down")
    if abs(roll) >= min_rot:
        labels.append("roll_cw" if roll > 0 else "roll_ccw")
    if abs(move[0]) >= min_move:
        labels.append("truck_right" if move[0] > 0 else "truck_left")
    if abs(move[1]) >= min_move:
        labels.append("pedestal_down" if move[1] > 0 else "pedestal_up")
    if abs(move[2]) >= min_move:
        labels.append("dolly_in" if move[2] > 0 else "dolly_out")
    if abs(zoom - 1.0) >= min_zoom:
        labels.append("zoom_in" if zoom > 1 else "zoom_out")
    if {"truck_right", "pan_left"} <= set(labels):
        labels.append("arc_right")
    if {"truck_left", "pan_right"} <= set(labels):
        labels.append("arc_left")
    return {"labels": labels or ["static"], "ambiguous": [], "basis": "geometry",
            "evidence": {"yaw_deg": round(yaw, 2), "pitch_deg": round(pitch, 2), "roll_deg": round(roll, 2),
                         "move_over_depth": [round(float(v), 3) for v in move], "focal_ratio": round(zoom, 4)}}


def label_shots(analysis: dict, analysis_dir: Path, providers: Providers | None, ctx: CallContext, *,
                backend: str | None = None, track: dict[str, np.ndarray] | None = None) -> dict[str, Any]:
    from ..vision import resolve

    choice = resolve("camera_motion", backend)
    for n, shot in enumerate(analysis["shots"]):
        rows = np.nonzero(track["shot"] == n)[0] if track is not None else []
        if len(rows) >= 2:
            depth = float(np.median(track["depth"][rows].astype(np.float32)))
            shot["camera_motion"] = geometry_labels(track["c2w"][rows], track["K"][rows], depth)
        else:
            shot["camera_motion"] = flow_labels(shot["flow"])
        shot["camera_motion"]["source"] = "rules"
    info: dict[str, Any] = {"backend": choice.used, "requested": choice.requested,
                            "taxonomy": "CameraBench (NeurIPS 2025) camera-motion primitives", "simulated": None}
    if choice.used == "llm" and providers is not None:
        images, digest = [], []
        for shot in analysis["shots"]:
            names = []
            for role in ("first", "mid", "last"):
                file = shot.get("frame_files", {}).get(role)
                if file:
                    images.append(analysis_dir / file)
                    names.append(f"图{len(images)}")
            digest.append({"id": shot["id"], "start_s": shot["start_s"], "end_s": shot["end_s"], "images": names,
                           "rules": shot["camera_motion"]})
        user, ref = render("reverse.camera_motion", shots=digest, taxonomy=TAXONOMY)
        result = providers.llm.complete(ctx, system="只输出 JSON。", user=user, images=images, json_mode=True,
                                        task="camera_motion", payload={"shots": digest})
        data = extract_json(result.text).get("shots") or {}
        for shot in analysis["shots"]:
            answer = data.get(shot["id"]) or {}
            labels = [x for x in answer.get("labels", []) if x in TAXONOMY]
            if labels and not result.simulated:
                shot["camera_motion"] = {**shot["camera_motion"], "rules_labels": shot["camera_motion"]["labels"],
                                         "labels": labels, "notes": answer.get("notes", ""), "source": f"vlm:{result.model}"}
        info.update(simulated=result.simulated, prompt_ref=ref)
    elif choice.used == "llm":
        info.update(backend="rules", error="没有可用的模型提供方，改用规则")
    for shot in analysis["shots"]:
        shot["camera_motion"]["labels_zh"] = [TAXONOMY[x] for x in shot["camera_motion"]["labels"]]
    analysis["camera_motion"] = info
    return info


@register_mock("camera_motion")
def _mock_camera_motion(payload: dict[str, Any]) -> str:
    return json.dumps({"shots": {s["id"]: {"labels": s["rules"]["labels"], "notes": "mock：沿用规则标签"}
                                 for s in payload.get("shots", [])}}, ensure_ascii=False)
