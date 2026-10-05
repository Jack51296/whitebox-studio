"""Planned long take ([D7] 长镜头规划模式): zones → connected rooms → one continuous camera move.

Zones are axis-aligned rooms placed one after another (straight or turning 90°). Walls of all zones
are merged per wall line (no duplicate coplanar walls), then door gaps are cut where the route passes.
The camera (and an optional walking subject ahead of it) follows the route at constant speed.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from typing import Any

from pydantic import ValidationError

from ..blender import kinematics as K
from ..config import Spec, load_settings
from ..jsonio import extract_json
from ..models.longtake import LongTakePlan, Zone
from ..models.scene import SceneSpec
from ..prompts import get_prompt
from ..providers import CallContext, Providers
from ..providers.mock import register_mock
from .motion import Waypoint, compile_route

WALL_T, LEAD_M = 0.2, 3.0
ZONE_VOCAB = [("门厅", "进入与定向"), ("长廊", "纵深推进"), ("大厅", "空间开阔、交代全貌"), ("转角展厅", "方向改变"),
              ("庭院", "内外转换"), ("窄道", "压缩节奏"), ("阶梯平台", "高差感（平地示意）"), ("终点平台", "抵达收束")]


def _left(d: tuple[int, int]) -> tuple[int, int]:
    return (-d[1], d[0])


def _right(d: tuple[int, int]) -> tuple[int, int]:
    return (d[1], -d[0])


def _rect(center: tuple[float, float], d: tuple[int, int], width: float, depth: float) -> tuple[float, float, float, float]:
    hx = (depth if d[0] else width) / 2
    hy = (width if d[0] else depth) / 2
    return (center[0] - hx, center[1] - hy, center[0] + hx, center[1] + hy)


def _overlap(a: tuple, b: tuple, eps: float = 0.05) -> bool:
    return a[0] < b[2] - eps and b[0] < a[2] - eps and a[1] < b[3] - eps and b[1] < a[3] - eps


def layout(plan: LongTakePlan) -> dict[str, Any]:
    """Place zones; return rectangles, door points and route polyline."""
    d = (0, 1)
    entry = (0.0, 0.0)
    rects, doors, route, dirs = [], [], [], []
    for i, zone in enumerate(plan.zones):
        center = (entry[0] + d[0] * zone.depth_m / 2, entry[1] + d[1] * zone.depth_m / 2)
        rect = _rect(center, d, zone.width_m, zone.depth_m)
        rects.append(rect)
        dirs.append(d)
        if i == 0:
            route.append((entry[0] + d[0] * 1.5, entry[1] + d[1] * 1.5))
        if i == len(plan.zones) - 1:
            route.append((center[0] + d[0] * (zone.depth_m / 2 - 2.0), center[1] + d[1] * (zone.depth_m / 2 - 2.0)))
            break
        turn = zone.turn
        for candidate in (turn, "straight", "left", "right"):
            nd = d if candidate == "straight" else (_left(d) if candidate == "left" else _right(d))
            if candidate == "straight":
                exit_pt = (entry[0] + d[0] * zone.depth_m, entry[1] + d[1] * zone.depth_m)
            else:
                side = zone.width_m / 2
                exit_pt = (center[0] + nd[0] * side, center[1] + nd[1] * side)
            nxt = plan.zones[i + 1]
            nc = (exit_pt[0] + nd[0] * nxt.depth_m / 2, exit_pt[1] + nd[1] * nxt.depth_m / 2)
            nrect = _rect(nc, nd, nxt.width_m, nxt.depth_m)
            if not any(_overlap(nrect, r) for r in rects):
                break
        else:
            raise ValueError(f"zone {plan.zones[i + 1].id} cannot be placed without overlapping earlier zones")
        if candidate != "straight":
            route.append(center)
        route.append((exit_pt[0] - nd[0] * 1.5, exit_pt[1] - nd[1] * 1.5))
        route.append(exit_pt)
        route.append((exit_pt[0] + nd[0] * 1.5, exit_pt[1] + nd[1] * 1.5))
        doors.append({"point": exit_pt, "dir": nd, "from": zone.id, "to": plan.zones[i + 1].id})
        entry, d = exit_pt, nd
    return {"rects": rects, "doors": doors, "route": route, "dirs": dirs}


def _walls(rects: list[tuple], doors: list[dict], door_w: float) -> list[tuple[str, float, float, float]]:
    """Merged wall segments as (axis, line_coord, a, b): axis 'x' = wall runs along X at y=line_coord."""
    lines: dict[tuple[str, float], list[list[float]]] = {}
    for x0, y0, x1, y1 in rects:
        for axis, c, a, b in (("x", y0, x0, x1), ("x", y1, x0, x1), ("y", x0, y0, y1), ("y", x1, y0, y1)):
            lines.setdefault((axis, round(c, 3)), []).append([a, b])
    gaps: dict[tuple[str, float], list[tuple[float, float]]] = {}
    for door in doors:
        (px, py), (_, dy) = door["point"], door["dir"]
        key = ("x", round(py, 3)) if dy else ("y", round(px, 3))
        center = px if dy else py
        gaps.setdefault(key, []).append((center - door_w / 2, center + door_w / 2))
    out = []
    for key, spans in lines.items():
        spans.sort()
        merged = [spans[0][:]]
        for a, b in spans[1:]:
            if a <= merged[-1][1] + 1e-6:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        for a, b in merged:
            pieces = [(a, b)]
            for g0, g1 in gaps.get(key, []):
                nxt = []
                for p0, p1 in pieces:
                    if g1 <= p0 or g0 >= p1:
                        nxt.append((p0, p1))
                        continue
                    if g0 > p0:
                        nxt.append((p0, g0))
                    if g1 < p1:
                        nxt.append((g1, p1))
                pieces = nxt
            out.extend((key[0], key[1], p0, p1) for p0, p1 in pieces if p1 - p0 > 0.05)
    return out


def _polyline_track(points: list[tuple[float, float]], duration: float, z: float, stop_short: float = 0.0,
                    lead: float = 0.0) -> list[list[float]]:
    seg = [0.0]
    for a, b in zip(points, points[1:]):
        seg.append(seg[-1] + math.dist(a, b))
    total = seg[-1]

    def at(s: float) -> tuple[float, float]:
        s = min(max(s, 0.0), total)
        for i in range(len(seg) - 1):
            if s <= seg[i + 1] or i == len(seg) - 2:
                u = (s - seg[i]) / max(seg[i + 1] - seg[i], 1e-9)
                (x0, y0), (x1, y1) = points[i], points[i + 1]
                return (x0 + u * (x1 - x0), y0 + u * (y1 - y0))
        return points[-1]

    travel = max(total - stop_short, 1.0)
    n = max(8, int(duration * 4))
    keys = []
    for k in range(n + 1):
        t = duration * k / n
        s = min(travel * _progress(t / duration) + lead, total)
        x, y = at(s)
        keys.append([round(t, 4), round(x, 3), round(y, 3), z])
    return keys


def _progress(u: float) -> float:
    return 0.15 * K.smooth(u) + 0.85 * u


def _trim_start(points: list[tuple[float, float]], lead: float) -> list[tuple[float, float]]:
    """The polyline from arc length ``lead`` onward."""
    done = 0.0
    for i, (a, b) in enumerate(zip(points, points[1:])):
        seg = math.dist(a, b)
        if done + seg > lead:
            u = (lead - done) / seg
            return [(a[0] + u * (b[0] - a[0]), a[1] + u * (b[1] - a[1]))] + list(points[i + 1:])
        done += seg
    return list(points[-2:])


def _trail(start: tuple[float, float], keys: list[list[float]], z: float) -> list[list[float]]:
    """Camera keys that stay the walker's lead-in distance behind it along the walker's own path."""
    pts = [start] + [(k[1], k[2]) for k in keys]
    arc = [0.0]
    for a, b in zip(pts, pts[1:]):
        arc.append(arc[-1] + math.dist(a, b))
    lead = arc[1]
    out = []
    for i, k in enumerate(keys):
        target = max(arc[i + 1] - lead, 0.0)
        j = max(0, min(len(arc) - 2, next((n for n in range(len(arc) - 1) if arc[n + 1] >= target), len(arc) - 2)))
        u = (target - arc[j]) / max(arc[j + 1] - arc[j], 1e-9)
        (x0, y0), (x1, y1) = pts[j], pts[j + 1]
        out.append([k[0], round(x0 + u * (x1 - x0), 3), round(y0 + u * (y1 - y0), 3), z])
    return out


def plan_to_scene(plan: LongTakePlan, spec: Spec, job_id: str, brief: str = "", seed: int = 0) -> SceneSpec:
    geo = layout(plan)
    rng = random.Random(seed)
    blocks: list[dict[str, Any]] = []
    xs = [v for r in geo["rects"] for v in (r[0], r[2])]
    ys = [v for r in geo["rects"] for v in (r[1], r[3])]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    blocks.append({"id": "G00", "shape": "plane", "center": [cx, cy, -0.05],
                   "size": [max(xs) - min(xs) + 20, max(ys) - min(ys) + 20, 0.1], "role": "ground", "label": "地面",
                   "collision": False})
    for zone, (x0, y0, x1, y1) in zip(plan.zones, geo["rects"]):
        blocks.append({"id": f"F_{zone.id}", "shape": "box", "center": [(x0 + x1) / 2, (y0 + y1) / 2, 0.01],
                       "size": [x1 - x0, y1 - y0, 0.02], "role": "floor", "label": f"{zone.name}地面",
                       "collision": False})
    height = max(z.height_m for z in plan.zones)
    for i, (axis, c, a, b) in enumerate(_walls(geo["rects"], geo["doors"], plan.door_width_m)):
        mid, length = (a + b) / 2, b - a
        center = [mid, c, height / 2] if axis == "x" else [c, mid, height / 2]
        size = [length + WALL_T, WALL_T, height] if axis == "x" else [WALL_T, length + WALL_T, height]
        blocks.append({"id": f"W{i:03}", "shape": "box", "center": [round(v, 3) for v in center],
                       "size": [round(v, 3) for v in size], "role": "wall", "label": "墙体"})
    route = geo["route"]
    samples = []
    for a, b in zip(route, route[1:]):
        n = max(1, int(math.dist(a, b) / 0.5))
        samples += [(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n) for k in range(n + 1)]
    for zone, (x0, y0, x1, y1) in zip(plan.zones, geo["rects"]):
        placed = 0
        for _ in range(zone.props * 12):
            if placed >= zone.props:
                break
            w, dpt, h = rng.uniform(0.6, 1.8), rng.uniform(0.5, 1.4), rng.uniform(0.5, 2.2)
            px, py = rng.uniform(x0 + 0.8, x1 - 0.8), rng.uniform(y0 + 0.8, y1 - 0.8)
            half = max(w, dpt) / 2
            if min(math.hypot(px - sx, py - sy) for sx, sy in samples) < half + 1.6:
                continue
            blocks.append({"id": f"P_{zone.id}_{placed}", "shape": "box", "center": [round(px, 3), round(py, 3), h / 2],
                           "size": [round(w, 3), round(dpt, 3), round(h, 3)], "role": "furniture",
                           "label": f"{zone.name}道具"})
            placed += 1

    duration = float(plan.duration_s)
    actors: list[dict[str, Any]] = []
    if plan.with_subject:
        walk = [Waypoint(x, y) for x, y in _trim_start(route, LEAD_M)]
        walk[-1].at_s = duration
        actor_keys = compile_route(walk, load_settings().qc.dynamic_gate.limits("pawn"), duration, start_moving=True,
                                   label="A")
        actors.append({"id": "A", "kind": "pawn", "label": "穿行者", "role": "主体", "height_m": 1.72, "radius_m": 0.35,
                       "head": "sphere", "body": "capsule", "color": "#d9363e",
                       "path": {"keys": actor_keys, "interpolation": "smooth"}})
        camera = {"keys": _trail(route[0], actor_keys, plan.camera_height_m), "interpolation": "smooth",
                  "aim_actor": "A", "aim_offset": [0.0, 0.0, 1.1]}
    else:
        camera = {"keys": _polyline_track(route, duration, plan.camera_height_m), "interpolation": "cubic"}
        ahead = _polyline_track(route, duration, plan.camera_height_m, lead=4.0)
        camera["aim_keys"] = [[k[0], k[1], k[2], plan.camera_height_m - 0.1] for k in ahead]
    names = {z.id: z.name for z in plan.zones}
    events = []
    for i, door in enumerate(geo["doors"]):
        t = min(camera["keys"], key=lambda k: math.dist(k[1:3], door["point"]))[0]
        events.append({"id": f"E{i + 1:02}", "at_s": round(min(t, duration - 0.05), 3),
                       "mechanism": f"镜头穿过门洞进入{names[door['to']]}", "choice": "",
                       "consequence": "空间转换，镜头不切"})
    spaces = [f"{z.name}（{z.id}）：{z.function}" for z in plan.zones]
    return SceneSpec.model_validate({
        "schema": "wbs.scene/1.0", "id": job_id, "title": plan.title, "fps": int(spec.fps), "duration_s": duration,
        "resolution": list(spec.resolution), "precision": "low", "palette": "grey", "time_map": None,
        "blocks": blocks, "actors": actors,
        "shots": [{"id": "S01", "start_s": 0.0, "end_s": duration, "lens_mm": plan.lens_mm, "camera": camera,
                   "title": "一镜到底穿越" + "→".join(z.name for z in plan.zones), "move": "one_take",
                   "framing": "medium", "action": "相机连续穿过各区域，不切镜"}],
        "events": events, "render": {"engine": "workbench"},
        "source": {"kind": "forward_longtake", "brief": brief, "plan": plan.model_dump()},
        "meta": {"story": {"logline": plan.logline, "story": plan.logline,
                           "world": "按长镜头规划的连续空间", "intent": "一镜到底：" + "→".join(z.name for z in plan.zones),
                           "semantic_only": "空间材质、陈设细节与人物表演只在最终AI中表现。",
                           "cast": "穿行者＝A" if plan.with_subject else "无主体"},
                 "spaces": spaces, "environment": "longtake", "content_class_label": "叙事",
                 "environment_description": "；".join(spaces)},
    })


def plan_longtake(brief: str, providers: Providers, ctx: CallContext, spec: Spec, zones: int = 5,
                  with_subject: bool = True) -> tuple[LongTakePlan, dict[str, Any]]:
    guide_prompt = get_prompt("forward.longtake.planning_mode")
    guide = guide_prompt.body
    schema = json.dumps(LongTakePlan.model_json_schema(), ensure_ascii=False)
    user = (f"{guide}\n\n需求：{brief}\n时长：{spec.duration_s} 秒；区域数：{zones}；"
            f"{'有' if with_subject else '无'}穿行主体。\n只输出符合下列 JSON Schema 的对象：\n{schema}")
    payload = {"brief": brief, "duration_s": spec.duration_s, "zones": zones, "with_subject": with_subject,
               "lens_mm": spec.lens_mm or 22.0}
    errors: list[str] = []
    for _ in range(3):
        result = providers.llm.complete(ctx, system="只输出 JSON。", user=user, json_mode=True, task="longtake_plan",
                                        payload=payload)
        try:
            plan = LongTakePlan.model_validate(extract_json(result.text))
            layout(plan)
            return plan, {"prompt_ref": guide_prompt.ref, "model": result.model, "simulated": result.simulated,
                          "repairs": errors}
        except (ValueError, ValidationError) as exc:
            errors.append(str(exc)[:600])
            user += f"\n\n上一次输出不合法，请修正：{str(exc)[:600]}"
    raise ValueError(f"long-take plan invalid after retries: {errors[-1]}")


@register_mock("longtake_plan")
def _mock_longtake(payload: dict[str, Any]) -> str:
    brief = str(payload.get("brief", ""))
    rng = random.Random(int(hashlib.sha1(brief.encode("utf-8")).hexdigest()[:8], 16))
    count = int(payload.get("zones", 5))
    names = [ZONE_VOCAB[0]] + rng.sample(ZONE_VOCAB[1:-1], k=min(count - 2, len(ZONE_VOCAB) - 2)) + [ZONE_VOCAB[-1]]
    zones = []
    last_turn = "straight"
    for i, (name, function) in enumerate(names[:count]):
        turn = rng.choice(["straight", "left", "right"])
        if turn == last_turn and turn != "straight":
            turn = "straight"
        last_turn = turn
        wide = name in ("大厅", "庭院")
        zones.append(Zone(id=f"Z{i + 1}", name=name, function=function,
                          width_m=round(rng.uniform(10, 14) if wide else rng.uniform(5.5, 8), 1),
                          depth_m=round(rng.uniform(10, 14) if wide else rng.uniform(7, 11), 1),
                          turn=turn, props=rng.randint(1, 3)).model_dump())
    plan = {"title": (brief.strip()[:12] or "长镜头穿越"), "logline": f"（模拟规划）{brief.strip()[:60]}",
            "duration_s": float(payload.get("duration_s", 15)), "zones": zones,
            "with_subject": bool(payload.get("with_subject", True)), "lens_mm": float(payload.get("lens_mm", 22.0)),
            "camera_height_m": 1.6, "door_width_m": 2.6, "notes": "mock 规划：未调用真实模型。"}
    return json.dumps(plan, ensure_ascii=False)
