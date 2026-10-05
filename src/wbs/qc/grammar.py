"""Camera-language checks on scene.json (warnings, not gates): declared framing/angle against what the cameras
actually frame, shot-grammar rules at each cut, and pacing against the team's director-card samples."""

from __future__ import annotations

import math
import statistics
from typing import Any

import numpy as np

from .. import camera_language as CL
from ..blender import kinematics as K
from ..config import QC, Grammar
from ..forward.story_examples import load_team_cards
from .dynamic import framing_precheck
from .visibility import project, setup_readability

FRAMING_TOLERANCE = math.log(1.2)
ANGLE_TOLERANCE_DEG = 5.0
WIDE = ("extreme_wide", "wide")
CLOSE = ("close", "extreme_close")


def declared_vs_measured(scene: dict, framing: dict) -> list[dict[str, Any]]:
    """Per shot: does the declared framing/angle match the measured subject size and camera pitch?"""
    measured = {s["shot"]: s for s in framing.get("shots", []) if not s.get("pov")}
    kinds = {a["id"]: a.get("kind", "pawn") for a in scene.get("actors", [])}
    out = []
    for shot in scene["shots"]:
        m = measured.get(shot["id"])
        if m is None:
            continue
        kind = kinds.get(m.get("subject"), next(iter(kinds.values()), "pawn"))
        problems = []
        declared = shot.get("framing", "")
        if declared and m.get("measured_framing") and m["measured_framing"] != declared:
            distance = CL.framing_distance(m["subject_size"], declared, kind)
            if distance > FRAMING_TOLERANCE:
                problems.append(f"声明{CL.label('framing', declared)}，实测主体尺寸 {m['subject_size']:.2f} 更像"
                                f"{CL.label('framing', m['measured_framing'])}")
        angle = shot.get("angle", "")
        if angle and angle != m.get("measured_angle"):
            band = CL.vocabulary()["angle"][angle]
            if angle == "dutch":
                problems.append(f"声明斜角，但画面滚转最大只有 {m['roll_max_deg']:.1f}°")
            else:
                lo, hi = band["pitch"]
                if not lo - ANGLE_TOLERANCE_DEG <= m["pitch_deg"] <= hi + ANGLE_TOLERANCE_DEG:
                    problems.append(f"声明{CL.label('angle', angle)}，实测俯仰 {m['pitch_deg']:.1f}° 更像"
                                    f"{CL.label('angle', m['measured_angle'])}")
        out.append({"shot": shot["id"], "declared": {"framing": declared, "angle": angle, "move": shot.get("move", "")},
                    "measured": {"framing": m.get("measured_framing", ""), "subject_size": m.get("subject_size"),
                                 "angle": m.get("measured_angle", ""), "pitch_deg": m.get("pitch_deg")},
                    "status": "warning" if problems else "ok", "detail": "；".join(problems)})
    return out


def _angle(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    return math.degrees(math.acos(max(-1.0, min(1.0, float(a @ b / (na * nb))))))


def _cross(a: np.ndarray, b: np.ndarray) -> float:
    return float(a[0] * b[1] - a[1] * b[0])


def _line_of_action(ev: K.SceneEvaluator, actors: list[dict], t: float) -> np.ndarray | None:
    """Main subject's direction of travel; two static characters define the line between them."""
    main = actors[0]["id"]
    vx, vy, _ = ev.tracks[main].velocity(ev.source_time(t), dt=0.1)
    if math.hypot(vx, vy) >= 0.5:
        return np.array([vx, vy])
    if len(actors) > 1:
        a, b = ev.actor_state(actors[0]["id"], t)[0], ev.actor_state(actors[1]["id"], t)[0]
        line = np.array([b[0] - a[0], b[1] - a[1]])
        return line if np.linalg.norm(line) > 0.3 else None
    return None


def _side(ev: K.SceneEvaluator, actors: list[dict], index: int, t: float, neutral_deg: float) -> int:
    """+1/-1 side of the line of action the camera is on; 0 when neutral (camera near the line) or no line."""
    line = _line_of_action(ev, actors, t)
    if line is None:
        return 0
    p = np.array(ev.actor_state(actors[0]["id"], t)[0][:2])
    c = np.array(ev.camera_state(t, index=index)["pos"][:2])
    rel = c - p
    theta = _angle(rel, line)
    if min(theta, 180.0 - theta) < neutral_deg:
        return 0
    return 1 if _cross(line, rel) > 0 else -1


def _screen_direction(ev: K.SceneEvaluator, scene: dict, actor: dict, index: int, t: float) -> float | None:
    """Share of the subject's travel that crosses the view (+ left→right, − right→left), even when the camera pans.

    None when the subject is barely moving or is not in front of this camera.
    """
    vx, vy, _ = ev.tracks[actor["id"]].velocity(ev.source_time(t), dt=0.1)
    speed = math.hypot(vx, vy)
    if speed < 0.5:
        return None
    cam = ev.camera_state(t, index=index)
    base = np.array(ev.actor_state(actor["id"], t)[0]) + [0.0, 0.0, actor.get("height_m", 1.7) / 2]
    ndc, _ = project(base[None, :], cam, scene["resolution"][0] / scene["resolution"][1])
    if not len(ndc) or abs(ndc[0, 0]) > 1.2:
        return None
    fwd = np.array(cam["aim"][:2], dtype=float) - np.array(cam["pos"][:2], dtype=float)
    if np.linalg.norm(fwd) < 1e-6:
        return None
    right = np.array([fwd[1], -fwd[0]]) / np.linalg.norm(fwd)
    return float(np.array([vx, vy]) @ right / speed)


def grammar_rules(scene: dict, framing: dict, cfg: Grammar) -> list[dict[str, Any]]:
    """Film-grammar checks at each cut: establishing shot, variety, 30° rule, 180° line, screen direction."""
    shots, actors = scene["shots"], scene.get("actors", [])
    measured = {s["shot"]: s.get("measured_framing", "") for s in framing.get("shots", []) if not s.get("pov")}
    out: list[dict[str, Any]] = []

    def add(rule: str, bad: list[str], ok_detail: str = "", bad_detail: str = "") -> None:
        out.append({"rule": rule, "status": "warning" if bad else "ok", "cuts": bad,
                    "detail": bad_detail if bad else ok_detail})

    if len(shots) < 2 or not actors:
        return out
    first = shots[0]
    first_framing = first.get("framing") or measured.get(first["id"], "")
    add("开场建立镜头", [] if first_framing in WIDE or first.get("move") == "pov" else [first["id"]],
        f"首镜{CL.label('framing', first_framing)}", f"首镜是{CL.label('framing', first_framing) or '未知景别'}，观众还没看清空间")
    framings = [measured.get(s["id"]) or s.get("framing", "") for s in shots]
    distinct = {f for f in framings if f}
    add("景别多样性", [] if len(distinct) >= 2 else ["全片"], f"{len(distinct)} 种景别", "全片只有一种景别")
    close_share = sum(f in CLOSE for f in framings) / len(framings)
    add("近景占比", [] if close_share <= cfg.max_close_share else ["全片"], f"{close_share:.0%}",
        f"近景和特写占 {close_share:.0%}，超过 {cfg.max_close_share:.0%}")

    ev = K.SceneEvaluator(scene)
    main = actors[0]
    jumps, crossings, flips = [], [], []
    for k in range(len(shots) - 1):
        a, b = shots[k], shots[k + 1]
        cut = f"{a['id']}→{b['id']}"
        _, a_last = ev.shot_frames(k)
        b_first, b_last = ev.shot_frames(k + 1)
        a_first, _ = ev.shot_frames(k)
        ta, tb = ev.frame_time(a_last), ev.frame_time(b_first)
        p = np.array(ev.actor_state(main["id"], tb)[0][:2])
        va = p - np.array(ev.camera_state(ta, index=k)["pos"][:2])
        vb = p - np.array(ev.camera_state(tb, index=k + 1)["pos"][:2])
        same_subject = a["camera"].get("aim_actor") and a["camera"].get("aim_actor") == b["camera"].get("aim_actor")
        same_size = measured.get(a["id"]) and measured.get(a["id"]) == measured.get(b["id"])
        ratio = np.linalg.norm(vb) / max(np.linalg.norm(va), 1e-6)
        if same_subject and same_size and _angle(va, vb) < cfg.jump_cut_min_angle_deg and 2 / 3 <= ratio <= 1.5:
            jumps.append(f"{cut}（机位只变 {_angle(va, vb):.0f}°，同为{CL.label('framing', measured[a['id']])}）")
        if "pov" in (a.get("move"), b.get("move")):
            continue
        sides_a = {_side(ev, actors, k, ev.frame_time(f), cfg.neutral_axis_deg) for f in (a_first, a_last)}
        sides_b = {_side(ev, actors, k + 1, ev.frame_time(f), cfg.neutral_axis_deg) for f in (b_first, b_last)}
        if 0 not in sides_a | sides_b and len(sides_a) == 1 and len(sides_b) == 1 and sides_a != sides_b:
            crossings.append(cut)
        lateral = math.sin(math.radians(cfg.neutral_axis_deg))
        dxa = _screen_direction(ev, scene, main, k, ta)
        dxb = _screen_direction(ev, scene, main, k + 1, tb)
        if dxa is not None and dxb is not None and min(abs(dxa), abs(dxb)) >= lateral and dxa * dxb < 0:
            flips.append(f"{cut}（{'左→右' if dxa > 0 else '右→左'} 接 {'左→右' if dxb > 0 else '右→左'}）")
    add("30° 规则（跳切）", jumps, "无跳切", "；".join(jumps))
    add("180° 轴线", crossings, "未越轴", "相邻两镜分在运动线两侧且都不是中性镜头：" + "、".join(crossings))
    add("屏幕方向", flips, "主体运动方向在切点前后一致", "；".join(flips))
    return out


def _content_class(scene: dict) -> str | None:
    control = (scene.get("source") or {}).get("control") or {}
    if control.get("content_class"):
        return control["content_class"]
    return {"叙事": "narrative", "运动": "motion"}.get((scene.get("meta") or {}).get("content_class_label"))


def _team_pacing(cls: str | None) -> tuple[list, list[float], list[float]]:
    cards = [c for c in load_team_cards() if c.duration_s and c.shots and (cls is None or c.content_class == cls)]
    rates = [c.shots / c.duration_s * 10 for c in cards]
    medians = [statistics.median(c.shot_lengths) for c in cards if c.shot_lengths]
    return cards, rates, medians


def pacing_text(content_class: str | None, duration_s: float) -> str:
    """The team's pacing range for planner prompts (the same cards the pacing check compares against)."""
    cards, rates, medians = _team_pacing(content_class or None)
    if not cards:
        return ""
    lo, hi = min(rates), max(rates)
    text = (f"团队同类样例（{len(cards)} 张导演卡）每 10 秒 {lo:.1f}–{hi:.1f} 镜（中位 {statistics.median(rates):.1f}），"
            f"本片 {duration_s:g} 秒约 {max(1, math.floor(lo * duration_s / 10))}–{math.ceil(hi * duration_s / 10)} 镜")
    if medians:
        text += f"；单镜时长中位 {min(medians):.1f}–{max(medians):.1f} 秒"
    return text + "。"


def pacing(scene: dict) -> dict[str, Any]:
    """Shots per 10 s and median shot length against the team's director cards of the same content class."""
    shots = scene["shots"]
    if len(shots) < 2:
        return {"rule": "节奏与团队同类样例", "status": "not_applicable", "detail": "一镜到底不比较切镜节奏"}
    cls = _content_class(scene)
    cards, rates, medians = _team_pacing(cls)
    if not cards:
        return {"rule": "节奏与团队同类样例", "status": "not_applicable", "detail": "没有可比较的团队样例"}
    lengths = [s["end_s"] - s["start_s"] for s in shots]
    rate, median = len(shots) / scene["duration_s"] * 10, statistics.median(lengths)
    problems = []
    if not min(rates) <= rate <= max(rates):
        problems.append(f"每 10 秒 {rate:.1f} 镜，团队同类样例 {min(rates):.1f}–{max(rates):.1f}（中位 {statistics.median(rates):.1f}）")
    if medians and not min(medians) <= median <= max(medians):
        problems.append(f"单镜中位 {median:.2f}s，团队同类样例 {min(medians):.2f}–{max(medians):.2f}s")
    return {"rule": "节奏与团队同类样例", "status": "warning" if problems else "ok", "detail": "；".join(problems),
            "ours": {"shots_per_10s": round(rate, 2), "median_shot_s": round(median, 3)},
            "team": {"cards": len(cards), "content_class": cls or "all",
                     "shots_per_10s": {"min": round(min(rates), 2), "median": round(statistics.median(rates), 2),
                                       "max": round(max(rates), 2)},
                     "median_shot_s": {"min": round(min(medians), 3), "max": round(max(medians), 3)} if medians else None}}


def grammar_report(scene: dict, qc: QC, framing: dict | None = None) -> dict[str, Any]:
    """镜头语法与可读性: declared camera language, grammar at cuts, pacing, setup readability (warnings by default)."""
    framing = framing or framing_precheck(scene, cfg=qc.framing)
    declared = declared_vs_measured(scene, framing)
    rules = grammar_rules(scene, framing, qc.grammar)
    pace = pacing(scene)
    setups = setup_readability(scene, qc)
    style = [r for r in declared + rules + [pace] if r.get("status") == "warning"]
    readability = [r for r in setups if r.get("status") == "warning"]
    failed = (qc.grammar.enforce and style) or (qc.readability.enforce and readability)
    return {"schema": "wbs.qc.grammar/1.0",
            "status": "failed" if failed else ("warning" if style or readability else "passed"),
            "warning_count": len(style) + len(readability),
            "enforce": {"grammar": qc.grammar.enforce, "readability": qc.readability.enforce},
            "camera_language": declared, "grammar": rules, "pacing": pace, "setup_readability": setups,
            "scope": "镜头语言与可读性的程序检查（默认只警告）；不替代人工看片"}
