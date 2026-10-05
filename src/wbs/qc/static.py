"""Static check gate ([D2] 阶段 2 ★静态核查闸门): final cameras' first/mid/last frames vs the source.

① layout consistency ② structure-line tilt ≤ 15° ③ mirror (three-image sheet + flow-sign re-check)
④ subject occupancy delta ≤ 10% ⑤ block interpenetration > 0.3 m forbidden.
Layout similarity is a coarse automatic proxy (edge / luminance grids); the three-image sheets are
the evidence a person looks at.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw

from ..blender import kinematics as K
from ..config import StaticGate
from ..media.boards import font
from ..models.common import combine
from ..reverse.lines import match_lines, structure_lines
from ..reverse.video import load_gray


def _grid(values: np.ndarray, grid: tuple[int, int] = (8, 6)) -> np.ndarray:
    return cv2.resize(values.astype(np.float32), grid, interpolation=cv2.INTER_AREA).ravel()


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    if a.std() < 1e-6 or b.std() < 1e-6:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def layout_similarity(src: np.ndarray, ren: np.ndarray) -> dict[str, float]:
    ren = cv2.resize(ren, (src.shape[1], src.shape[0]), interpolation=cv2.INTER_AREA)
    edges = _corr(_grid(cv2.Canny(src, 50, 150)), _grid(cv2.Canny(ren, 50, 150)))
    luma = _corr(_grid(src), _grid(ren))
    return {"edges": round(edges, 4), "luma": round(luma, 4), "score": round(max(edges, luma), 4)}


def interpenetration(scene: dict, max_m: float) -> dict[str, Any]:
    boxes = [(b["id"], *K.block_aabb(b)) for b in scene.get("blocks", [])
             if b.get("collision", True) and b.get("role") not in ("ground", "floor")]
    pairs = []
    for i, (ia, lo_a, hi_a) in enumerate(boxes):
        for ib, lo_b, hi_b in boxes[i + 1:]:
            overlap = [min(hi_a[k], hi_b[k]) - max(lo_a[k], lo_b[k]) for k in range(3)]
            if all(o > 0 for o in overlap) and min(overlap) > max_m:
                pairs.append({"a": ia, "b": ib, "depth_m": round(min(overlap), 3)})
    return {"status": "failed" if pairs else "passed", "pairs": pairs[:30], "count": len(pairs), "threshold_m": max_m}


def _union_box(actors: dict[str, dict]) -> dict[str, Any] | None:
    boxes = [a["bbox"] for a in actors.values() if a.get("visible") and a.get("bbox")]
    if not boxes:
        return None
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    return {"bbox": [x0, y0, x1, y1], "area": (x1 - x0) * (y1 - y0), "center": [(x0 + x1) / 2, (y0 + y1) / 2]}


def three_image_sheet(src: Path, ren: Path, out: Path, label: str) -> Path:
    """Source | white model | mirrored white model (the [D2] 镜像三图对比 evidence)."""
    with Image.open(src) as a, Image.open(ren) as b:
        w = 480
        h = round(a.height * w / a.width)
        left, mid = a.convert("RGB").resize((w, h)), b.convert("RGB").resize((w, h))
    right = mid.transpose(Image.FLIP_LEFT_RIGHT)
    sheet = Image.new("RGB", (w * 3 + 16, h + 40), "#f5f5f5")
    for i, img in enumerate((left, mid, right)):
        sheet.paste(img, (i * (w + 8), 0))
    draw = ImageDraw.Draw(sheet)
    for i, text in enumerate(("原片", "白模（最终相机）", "白模水平镜像（对照）")):
        draw.text((i * (w + 8) + 8, h + 8), f"{label}  {text}", fill="#111111", font=font(16))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, quality=90)
    return out


def static_gate(analysis: dict, analysis_dir: Path, scene: dict, samples: dict, frames_dir: Path,
                evidence_dir: Path, cfg: StaticGate) -> dict[str, Any]:
    by_frame = {row["frame"]: row for row in samples["samples"]}
    shots_out, layout_st, line_st, occ_st, mirror_st = [], [], [], [], []
    for shot in analysis["shots"]:
        result: dict[str, Any] = {"id": shot["id"], "frames": {}}
        for role, index in zip(("first", "mid", "last"), shot["first_mid_last_frames"]):
            frame = index + 1
            src_file = shot.get("frame_files", {}).get(role)
            ren_file = frames_dir / f"f{frame:04d}.png"
            entry: dict[str, Any] = {"frame": frame}
            if src_file and ren_file.exists():
                src_path = analysis_dir / src_file
                src_g, ren_g = load_gray(src_path), load_gray(ren_file)
                entry["layout"] = layout_similarity(src_g, ren_g)
                layout_st.append("passed" if entry["layout"]["score"] >= cfg.layout_min_similarity else "failed")
                lines = match_lines(shot["lines"][role], structure_lines(ren_g))
                if lines.get("status") == "not_applicable":
                    entry["lines"] = lines
                elif lines.get("max_diff_deg") is None:
                    entry["lines"] = lines
                    line_st.append("failed")
                else:
                    ok = lines["max_diff_deg"] <= cfg.structure_line_max_deg
                    entry["lines"] = {**lines, "status": "passed" if ok else "failed"}
                    line_st.append(entry["lines"]["status"])
                entry["evidence"] = three_image_sheet(src_path, ren_file, evidence_dir / f"{shot['id']}_{role}.jpg",
                                                      f"{shot['id']} {role} f{frame}").name
            else:
                entry["layout"] = {"status": "not_run", "reason": "missing source or rendered frame"}
                layout_st.append("not_run")
            src_occ = (shot.get("occupancy") or {}).get(role)
            row = by_frame.get(frame)
            ren_occ = _union_box(row["actors"]) if row else None
            if src_occ and ren_occ:
                delta = abs(src_occ["area"] - ren_occ["area"])
                entry["occupancy"] = {"source": src_occ["area"], "render": round(ren_occ["area"], 5),
                                      "delta": round(delta, 5),
                                      "status": "passed" if delta <= cfg.occupancy_max_delta else "failed"}
                occ_st.append(entry["occupancy"]["status"])
            else:
                entry["occupancy"] = {"status": "not_applicable" if not src_occ else "failed",
                                      "reason": "no subject measured in source" if not src_occ else "subject not visible in render"}
                if src_occ:
                    occ_st.append("failed")
            result["frames"][role] = entry
        result["mirror"] = _mirror(shot, by_frame)
        if result["mirror"]["status"] in ("passed", "failed"):
            mirror_st.append(result["mirror"]["status"])
        shots_out.append(result)
    inter = interpenetration(scene, cfg.interpenetration_max_m)
    checks = {"layout": combine(*layout_st), "structure_lines": combine(*line_st) if line_st else "not_applicable",
              "mirror": combine(*mirror_st) if mirror_st else "not_applicable",
              "occupancy": combine(*occ_st) if occ_st else "not_applicable", "interpenetration": inter["status"]}
    status = combine(*[v for v in checks.values() if v != "not_applicable"])
    return {"schema": "wbs.qc.static/1.0", "status": status, "checks": checks,
            "thresholds": cfg.model_dump(), "shots": shots_out, "interpenetration": inter,
            "note": "布局相似度为粗粒度自动指标；三图对比证据图需人工确认"}


def _mirror(shot: dict, by_frame: dict[int, dict]) -> dict[str, Any]:
    first, _, last = (i + 1 for i in shot["first_mid_last_frames"])
    a, b = by_frame.get(first), by_frame.get(last)
    if not a or not b:
        return {"status": "not_run", "reason": "render samples missing"}
    pan = shot["flow"]["pan_x_total"]
    dh = (b["camera"]["heading_deg"] - a["camera"]["heading_deg"] + 180) % 360 - 180
    checks = []
    if abs(pan) > 0.05 and abs(dh) > 1.0:
        checks.append(("camera_pan_sign", (pan > 0) == (dh > 0)))
    occ = shot.get("occupancy") or {}
    src_first, src_last = occ.get("first"), occ.get("last")
    ren_first, ren_last = _union_box(a["actors"]), _union_box(b["actors"])
    if src_first and src_last and ren_first and ren_last:
        ds = src_last["center"][0] - src_first["center"][0]
        dr = ren_last["center"][0] - ren_first["center"][0]
        if abs(ds) > 0.05 and abs(dr) > 0.02:
            checks.append(("subject_screen_x_sign", (ds > 0) == (dr > 0)))
    if not checks:
        return {"status": "not_applicable", "reason": "no significant horizontal motion to compare"}
    return {"status": "passed" if all(ok for _, ok in checks) else "failed",
            "checks": {name: ok for name, ok in checks}, "source_pan_x": pan, "render_heading_change_deg": round(dh, 3)}
