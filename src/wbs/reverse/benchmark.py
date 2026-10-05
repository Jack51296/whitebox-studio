"""Reverse-pipeline benchmark on self-rendered videos whose truth is known (scene.json + Blender audit).

Measures, per backend: cut F1 (±1 frame), subject box area error / IoU / detection rate on the true shot
ranges, and per-shot camera accuracy (ATE after Sim(3) and after SE(3) alignment, scale ratio,
camera-relative rotation error). White-box renders are an easy, synthetic domain: the numbers rank
backends on the same inputs; they are not a claim about real footage.
"""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any

import numpy as np

from ..blender.kinematics import SceneEvaluator
from ..config import Spec, load_settings
from ..forward.procedural import generate
from ..jsonio import read_json, write_json
from ..layout import JobPaths, Workspace
from ..taxonomy import ControlSpec
from . import cuts as C
from .analyze import analyze
from .geometry import load_track, solve_geometry
from .solve import solve
from .subject import sample_frames, shot_samples

TRUTH_SET = (
    ("pan truck push follow", ["pan", "truck", "push", "follow"], "eye_level"),
    ("truck pull pan handheld", ["truck", "pull", "pan", "handheld"], "eye_level"),
    ("follow push truck pan", ["follow", "push", "truck", "pan"], "eye_level"),
    ("handheld pan pull truck", ["handheld", "pan", "pull", "truck"], "eye_level"),
    ("high angle pan truck", ["pan", "truck", "follow", "push"], "high_angle"),
)


def truth_jobs(ws: Workspace, batch: str, seed: int = 11, duration: float = 12.0) -> list[JobPaths]:
    """Write the truth scenes (multi-shot, forced camera moves); rendering is done by the caller."""
    jobs = []
    for n, (_, moves, viewpoint) in enumerate(TRUTH_SET, 1):
        job = ws.job(batch, f"T{n:02}")
        job.root.mkdir(parents=True, exist_ok=True)
        if not job.scene.exists():
            control = ControlSpec(index=n, seed=seed + n, shot_form="multi_shot", shot_count=4, subject="person",
                                  subject_child="duo", color="identity", era="modern", viewpoint=viewpoint,
                                  camera_move=moves[0], content_class="motion")
            scene = generate(control, Spec(duration_s=duration, fps=24, resolution=(1280, 720)), job_id=job.job_id,
                             title=f"反推评测真值 {n}", moves=moves, max_shot_s=5.0)
            write_json(job.scene, scene.to_json_dict())
            job.update_meta(job_id=job.job_id, batch_id=batch, route="benchmark_truth", title=scene.title, moves=moves,
                            viewpoint=viewpoint)
        jobs.append(job)
    return jobs


# ---------------------------------------------------------------- metrics
def cut_counts(found: list[int], truth: list[int], tolerance: int = 1) -> dict[str, int]:
    unmatched = list(truth)
    tp = 0
    for c in sorted(found):
        near = [t for t in unmatched if abs(t - c) <= tolerance]
        if near:
            unmatched.remove(min(near, key=lambda t: abs(t - c)))
            tp += 1
    return {"tp": tp, "fp": len(found) - tp, "fn": len(unmatched)}


def f1(counts: dict[str, int]) -> float:
    tp, fp, fn = counts["tp"], counts["fp"], counts["fn"]
    return 1.0 if tp + fp + fn == 0 else round(2 * tp / max(2 * tp + fp + fn, 1), 4)


def iou(a: list[float], b: list[float]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def umeyama(src: np.ndarray, dst: np.ndarray, with_scale: bool = True) -> tuple[float, np.ndarray, np.ndarray]:
    mu_s, mu_d = src.mean(0), dst.mean(0)
    xs, xd = src - mu_s, dst - mu_d
    u, d, vt = np.linalg.svd(xd.T @ xs / len(src))
    s_fix = np.eye(3)
    if np.linalg.det(u) * np.linalg.det(vt) < 0:
        s_fix[2, 2] = -1
    r = u @ s_fix @ vt
    var = float((xs ** 2).sum() / len(src))
    scale = float(np.trace(np.diag(d) @ s_fix) / var) if with_scale and var > 1e-12 else 1.0
    return scale, r, mu_d - scale * r @ mu_s


def _look(forward: np.ndarray) -> np.ndarray:
    f = forward / max(np.linalg.norm(forward), 1e-12)
    right = np.cross(f, [0.0, 0.0, 1.0])
    right = right / max(np.linalg.norm(right), 1e-12)
    return np.stack([right, np.cross(right, f), f], 1)


def camera_errors(solved: dict, audit: dict, truth_scene: dict) -> list[dict[str, Any]]:
    """Per truth shot: ATE (Sim3 / SE3), scale ratio, camera-relative rotation error (deg)."""
    ev = SceneEvaluator(solved)
    fps = int(truth_scene["fps"])
    rows = audit["samples"]
    out = []
    for shot in truth_scene["shots"]:
        sel = [r for r in rows if r["shot"] == shot["id"]]
        if len(sel) < 2:
            continue
        truth_pos = np.array([r["camera"]["pos"] for r in sel], dtype=float)
        truth_rot = [_look(np.array(r["camera"]["forward"], dtype=float)) for r in sel]
        states = [ev.camera_state((r["frame"] - 1) / fps) for r in sel]
        pos = np.array([s["pos"] for s in states], dtype=float)
        rot = [_look(np.array(s["aim"], dtype=float) - np.array(s["pos"], dtype=float)) for s in states]
        rel_err = []
        for i in range(len(sel)):
            a = rot[0].T @ rot[i]
            b = truth_rot[0].T @ truth_rot[i]
            cos = (np.trace(a.T @ b) - 1) / 2
            rel_err.append(math.degrees(math.acos(max(-1.0, min(1.0, cos)))))
        path = float(np.linalg.norm(np.diff(truth_pos, axis=0), axis=1).sum())
        row: dict[str, Any] = {"shot": shot["id"], "move": shot.get("move") or "story", "frames": len(sel),
                               "truth_path_m": round(path, 3), "rotation_error_deg": round(float(np.mean(rel_err)), 3)}
        if path > 0.5:
            s, r, t = umeyama(pos, truth_pos)
            row["ate_sim3_m"] = round(float(np.sqrt(((pos @ r.T * s + t - truth_pos) ** 2).sum(1).mean())), 4)
            row["scale_ratio"] = round(1.0 / s, 4) if s > 0 else None
            _, r1, t1 = umeyama(pos, truth_pos, with_scale=False)
            row["ate_se3_m"] = round(float(np.sqrt(((pos @ r1.T + t1 - truth_pos) ** 2).sum(1).mean())), 4)
        else:
            row["solved_path_m"] = round(float(np.linalg.norm(np.diff(pos, axis=0), axis=1).sum()), 3)
        out.append(row)
    return out


def subject_errors(occ: list[dict[int, dict[str, Any]]], audit: dict, requested: set[int]) -> dict[str, Any]:
    """Box area error / IoU against the union of fully visible actors; detection rate over requested frames."""
    truth = {}
    for r in audit["samples"]:
        boxes = [a["bbox"] for a in r["actors"].values() if a.get("visible") and not a.get("partial")]
        if boxes:
            truth[r["frame"] - 1] = [min(b[0] for b in boxes), min(b[1] for b in boxes),
                                     max(b[2] for b in boxes), max(b[3] for b in boxes)]
    area_err, ious = [], []
    for shot in occ:
        for f, v in shot.items():
            if f not in truth:
                continue
            b, tb = v["bbox"], truth[f]
            area_err.append(abs((b[2] - b[0]) * (b[3] - b[1]) - (tb[2] - tb[0]) * (tb[3] - tb[1])))
            ious.append(iou(b, tb))
    wanted = len(requested & set(truth))
    return {"requested_with_truth": wanted, "matched": len(ious),
            "detection_rate": round(len(ious) / wanted, 4) if wanted else None,
            "area_abs_error": round(float(np.mean(area_err)), 5) if area_err else None,
            "iou": round(float(np.mean(ious)), 4) if ious else None}


# ---------------------------------------------------------------- driver
def _summary(rows: list[dict[str, Any]], key: str) -> float | None:
    vals = [r[key] for r in rows if r.get(key) is not None]
    return round(float(np.mean(vals)), 4) if vals else None


def run(jobs: list[JobPaths], out_dir: Path, *, cut_backends=("builtin", "pyscenedetect", "transnetv2"),
        subject_backends=("motion", "grounding_dino"), geometry: bool = True, prompt: str = "figure.") -> dict[str, Any]:
    from ..reverse.video import decode
    from ..vision import resolve
    from ..vision.worker import VisionUnavailable
    from ..vision.worker import run as worker

    report: dict[str, Any] = {"schema": "wbs.reverse.benchmark/1.0", "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                              "videos": [], "scope": __doc__.strip().splitlines()[0]}
    cut_totals = {b: {"tp": 0, "fp": 0, "fn": 0, "seconds": 0.0, "used": set()} for b in cut_backends}
    subj_rows: dict[str, list[dict[str, Any]]] = {b: [] for b in subject_backends}
    cam_rows: dict[str, list[dict[str, Any]]] = {}
    for job in jobs:
        video = next(job.root.glob("*_白模参考.mp4"))
        truth_scene, audit = read_json(job.scene), read_json(job.samples)
        fps = int(truth_scene["fps"])
        truth_cuts = [int(round(s["start_s"] * fps)) for s in truth_scene["shots"][1:]]
        grays = decode(video, 320, keep=set()).grays
        entry: dict[str, Any] = {"job": job.key, "frames": len(grays), "truth_cuts": truth_cuts, "cuts": {}, "subject": {},
                                 "camera": {}}
        for b in cut_backends:
            started = time.time()
            found, _, info = C.find_cuts(video, grays, fps, b)
            counts = cut_counts(found, truth_cuts)
            for k in ("tp", "fp", "fn"):
                cut_totals[b][k] += counts[k]
            cut_totals[b]["seconds"] += time.time() - started
            cut_totals[b]["used"].add(info["backend"])
            entry["cuts"][b] = {"found": found, **counts, "used": info["backend"]}
        ranges = [(int(round(s["start_s"] * fps)), int(round(s["end_s"] * fps)) - 1) for s in truth_scene["shots"]]
        requested = {f for a, b in ranges for f in sample_frames(a, b, 6)}
        for b in subject_backends:
            started = time.time()
            occ, info = shot_samples(video, grays, ranges, 6, backend=b, prompt=prompt, masks=True)
            row = {**subject_errors(occ, audit, requested), "used": info["backend"], "seconds": round(time.time() - started, 2)}
            subj_rows[b].append(row)
            entry["subject"][b] = row
        if geometry:
            work = out_dir / "work" / job.job_id
            analysis = analyze(video, work, cuts="builtin", subject_backend="motion")
            variants: dict[str, dict] = {}
            started = time.time()
            variants["heuristic"] = solve(analysis, job.job_id, "bench", lens_mm=28.0).to_json_dict()
            seconds = {"heuristic": round(time.time() - started, 2)}
            if resolve("geometry", "da3").used == "da3":
                track_path = work / "camera_track.npz"
                started = time.time()
                try:
                    worker("geometry_da3", {"video": str(video), "out": str(track_path), "width": analysis["video"]["width"],
                                            "height": analysis["video"]["height"], "model": "da3-base",
                                            "metric_model": "da3metric-large",
                                            "shots": [{"start": s["start_frame"], "end": s["end_frame"]} for s in analysis["shots"]]})
                    track = load_track(track_path, analysis)
                    t_track = time.time() - started
                    for scale in ("camera_height", "metric"):
                        t0 = time.time()
                        scene, _ = solve_geometry(analysis, track, job.job_id, "bench", scale_source=scale)
                        variants[f"da3/{scale}"] = scene.to_json_dict()
                        seconds[f"da3/{scale}"] = round(t_track + time.time() - t0, 2)
                except (VisionUnavailable, ValueError) as exc:
                    entry["camera"]["da3_error"] = str(exc)
            for name, scene in variants.items():
                rows = camera_errors(scene, audit, truth_scene)
                cam_rows.setdefault(name, []).extend(rows)
                entry["camera"][name] = {"seconds": seconds[name], "shots": rows}
        report["videos"].append(entry)
    report["cuts"] = {b: {"tp": v["tp"], "fp": v["fp"], "fn": v["fn"], "f1": f1(v), "seconds": round(v["seconds"], 2),
                          "used": sorted(v["used"])} for b, v in cut_totals.items()}
    report["subject"] = {b: {"area_abs_error": _summary(rows, "area_abs_error"), "iou": _summary(rows, "iou"),
                             "detection_rate": round(sum(r["matched"] for r in rows) / max(sum(r["requested_with_truth"] for r in rows), 1), 4),
                             "seconds": round(sum(r["seconds"] for r in rows), 2), "used": sorted({r["used"] for r in rows})}
                         for b, rows in subj_rows.items()}
    report["camera"] = {name: {"ate_sim3_m": _summary(rows, "ate_sim3_m"), "ate_se3_m": _summary(rows, "ate_se3_m"),
                               "scale_ratio": _summary(rows, "scale_ratio"),
                               "rotation_error_deg": _summary(rows, "rotation_error_deg"),
                               "moving_shots": sum("ate_sim3_m" in r for r in rows), "shots": len(rows)}
                        for name, rows in cam_rows.items()}
    report["camera_by_move"] = {
        name: {move: {"ate_sim3_m": _summary(sub, "ate_sim3_m"), "scale_ratio": _summary(sub, "scale_ratio"),
                      "rotation_error_deg": _summary(sub, "rotation_error_deg"), "shots": len(sub)}
               for move in sorted({r["move"] for r in rows}) for sub in [[r for r in rows if r["move"] == move]]}
        for name, rows in cam_rows.items()}
    report["recommendation"] = recommend(report)
    return report


def recommend(report: dict[str, Any]) -> dict[str, str]:
    """Switch a default only when the new backend is clearly better on this benchmark (all new models stay optional)."""
    rec = {}
    cuts = report.get("cuts", {})
    base = cuts.get("builtin", {}).get("f1", 0)
    better = [b for b, v in cuts.items() if b != "builtin" and v.get("f1", 0) > base + 0.02 and b in v.get("used", [])]
    rec["cuts"] = (f"{better[0]}（F1 {cuts[better[0]]['f1']} > builtin {base}）" if better else
                   f"保持 builtin（新后端 F1 未明显更高：{ {b: v.get('f1') for b, v in cuts.items()} }）")
    subj = report.get("subject", {})
    m, g = subj.get("motion", {}), subj.get("grounding_dino", {})
    if g.get("iou") and m.get("iou") and g["iou"] > m["iou"] + 0.05 and "grounding_dino" in g.get("used", []):
        rec["subject"] = f"有 GPU 工作环境时建议 grounding_dino（IoU {g['iou']} > motion {m['iou']}）；默认仍为 motion"
    else:
        rec["subject"] = f"保持 motion（IoU motion={m.get('iou')} grounding_dino={g.get('iou')}）"
    cam = report.get("camera", {})
    h = cam.get("heuristic", {})
    best = min((k for k in cam if k != "heuristic" and cam[k].get("ate_sim3_m") is not None),
               key=lambda k: cam[k]["ate_sim3_m"], default=None)
    if best and h.get("ate_sim3_m") and cam[best]["ate_sim3_m"] < 0.7 * h["ate_sim3_m"]:
        rec["geometry"] = (f"有 GPU 工作环境时建议 {best}（ATE {cam[best]['ate_sim3_m']} m < 启发式 {h['ate_sim3_m']} m，"
                           f"旋转误差 {cam[best]['rotation_error_deg']}° vs {h.get('rotation_error_deg')}°）；默认仍为 heuristic")
    else:
        rec["geometry"] = f"保持 heuristic（{ {k: v.get('ate_sim3_m') for k, v in cam.items()} }）"
    by_move = report.get("camera_by_move", {})
    if best and best in by_move and "heuristic" in by_move:
        wins = [m for m, v in by_move[best].items() if v["ate_sim3_m"] is not None and
                (by_move["heuristic"].get(m, {}).get("ate_sim3_m") or 0) > 1.3 * v["ate_sim3_m"]]
        losses = [m for m, v in by_move[best].items() if v["ate_sim3_m"] is not None and
                  v["ate_sim3_m"] > 1.3 * (by_move["heuristic"].get(m, {}).get("ate_sim3_m") or float("inf"))]
        rec["geometry_by_move"] = f"{best} 明显更好的运镜：{wins or '无'}；明显更差的运镜：{losses or '无'}"
    return rec


def markdown(report: dict[str, Any]) -> str:
    lines = ["# 反推评测（真值已知的自渲染视频）", "", f"生成时间：{report['generated_at']}；视频数：{len(report['videos'])}。",
             "白模渲染是简单的合成域：结果用于在同一输入上比较后端，不代表真实素材上的表现。", "", "## 切镜（±1 帧）", "",
             "| 后端 | TP | FP | FN | F1 | 耗时 s |", "|---|---|---|---|---|---|"]
    for b, v in report["cuts"].items():
        lines.append(f"| {b}（实际 {','.join(v['used'])}） | {v['tp']} | {v['fp']} | {v['fn']} | {v['f1']} | {v['seconds']} |")
    lines += ["", "## 主体占幅（真值镜头区间内的采样帧）", "", "| 后端 | 面积绝对误差 | IoU | 检出率 | 耗时 s |", "|---|---|---|---|---|"]
    for b, v in report["subject"].items():
        lines.append(f"| {b}（实际 {','.join(v['used'])}） | {v['area_abs_error']} | {v['iou']} | {v['detection_rate']} | {v['seconds']} |")
    if report.get("camera"):
        lines += ["", "## 相机（逐镜头；ATE 经 Sim3 / SE3 对齐）", "",
                  "| 方案 | ATE Sim3 m | ATE SE3 m | 尺度比 | 相对旋转误差 ° | 运动镜头/镜头 |", "|---|---|---|---|---|---|"]
        for k, v in report["camera"].items():
            lines.append(f"| {k} | {v['ate_sim3_m']} | {v['ate_se3_m']} | {v['scale_ratio']} | {v['rotation_error_deg']} | "
                         f"{v['moving_shots']}/{v['shots']} |")
        by_move = report.get("camera_by_move", {})
        names = list(by_move)
        moves = sorted({m for v in by_move.values() for m in v})
        if names:
            lines += ["", "### 按运镜类型（ATE Sim3 m / 相对旋转误差 °）", "",
                      "| 运镜 | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
            for m in moves:
                cells = [f"{by_move[n].get(m, {}).get('ate_sim3_m')} / {by_move[n].get(m, {}).get('rotation_error_deg')}"
                         for n in names]
                lines.append(f"| {m} | " + " | ".join(cells) + " |")
    lines += ["", "## 默认设置建议", ""] + [f"- {k}：{v}" for k, v in report["recommendation"].items()]
    return "\n".join(lines) + "\n"


def default_settings() -> dict[str, Any]:
    return load_settings().vision.model_dump()
