"""Camera rigs for story shots: a high-level description (follow, orbit, crane, mount...) compiled into camera keys.

Distances default from the declared framing, the lens and the subject's size (the same size bands the framing
check measures); heights from the declared angle. A rig that would collide is moved as a whole (other side,
closer, higher) before the procedural per-key clearance fixes what remains; handheld shake is a kinematics
response layer.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any

from ..blender import kinematics as K
from ..camera_language import is_figure
from .procedural import Subject, _actor_distance, _boxes, _clear_camera, _times

SENSOR_W_MM, SENSOR_H_MM = 36.0, 20.25
TARGET_SIZE = {"figure": {"extreme_wide": 0.12, "wide": 0.45, "medium": 1.4, "close": 3.5, "extreme_close": 8.0},
               "object": {"extreme_wide": 0.08, "wide": 0.3, "medium": 0.9, "close": 2.5, "extreme_close": 6.0}}
KEY_STEP_S = 0.2
FIXED_RIGS = ("pov", "mount", "top_down")
RIG_GUIDE = (
    "rig.type：static 固定机位（position 可给坐标，缺省放在目标斜前方）、pan 原地摇、follow 后方跟拍、"
    "lead 前方倒退跟拍、side_track 侧向并行、push 推近、pull 拉远、crane 升降（rise_m 升高米数）、"
    "orbit 环绕（arc_deg 环绕角度）、top_down 垂直俯视、pov 人物主观视角、mount 挂在人物或车上"
    "（distance_m 前后、height_m 高度、lateral_m 左右偏移）。\n"
    "target 写被拍人物 id；distance_m、height_m 不写时按景别、焦距和角度自动推算；side 取 left 或 right；"
    "handheld 取 0–1 表示手持晃动强度。机位会整体避让体块和其他人物，写 camera_keys 则按手写关键帧，不再用 rig。"
)


def default_distance(framing: str, lens_mm: float, actor: dict) -> float:
    """Camera distance that gives the framing's typical subject size with this lens."""
    kind = actor.get("kind", "pawn")
    figure = is_figure(kind)
    size = TARGET_SIZE["figure" if figure else "object"][framing or "medium"]
    extent = actor.get("height_m", 1.7) if figure else 2 * actor.get("radius_m", 0.35)
    sensor = SENSOR_H_MM if figure else SENSOR_W_MM
    return max(1.2, extent * lens_mm / (size * sensor))


def default_height(angle: str, distance: float, actor: dict) -> float:
    h = actor.get("height_m", 1.7)
    eye = h * 0.9 if is_figure(actor.get("kind", "pawn")) else h + 0.3
    return {"low": 0.45, "high": h + 0.6 * distance, "overhead": max(12.0, 1.5 * distance)}.get(angle, eye)


def _grazing(keys: list, boxes: list, bodies: list, carrier: Subject | None, hz: float = 12.0) -> int:
    """Samples of the camera path too close to a block or to an actor other than the carrier."""
    track = K.Track(keys, "smooth" if len(keys) > 2 else "linear")
    t0, t1 = keys[0][0], keys[-1][0]
    n = max(1, int((t1 - t0) * hz))
    others = [b for b in bodies if b is not carrier]
    bad = 0
    for i in range(n + 1):
        t = t0 + (t1 - t0) * i / n
        p = track.at(t)
        if any(K.point_aabb_distance(p, lo, hi) < 0.45 for lo, hi in boxes) or \
                any(_actor_distance(b, t, p) < 0.6 for b in others):
            bad += 1
    return bad


def compile_rig(rig: dict, shot: dict, actors: list[dict], blocks: list[dict]) -> tuple[dict[str, Any], dict[str, Any]]:
    """(camera dict for scene.json, rig summary) for one shot; ``shot`` carries id, start_s, end_s, lens_mm, framing,
    angle, aim_actor."""
    by_id = {a["id"]: a for a in actors}
    target_id = rig.get("target") or shot.get("aim_actor") or (actors[0]["id"] if actors else None)
    if target_id not in by_id:
        raise ValueError(f"{shot['id']}: rig target {target_id!r} is not a character")
    actor = by_id[target_id]
    flyer = actor.get("kind") in ("block_bird", "block_fish")
    bodies = {a["id"]: Subject(a["path"]["keys"], a.get("height_m", 1.7), flyer, radius=a.get("radius_m", 0.35))
              for a in actors}
    subj = bodies[target_id]
    kind = rig["type"]
    t0, t1 = float(shot["start_s"]), float(shot["end_s"])
    lens = float(shot.get("lens_mm", 32.0))
    dist = float(rig.get("distance_m") or default_distance(shot.get("framing", ""), lens, actor))
    height = float(rig["height_m"]) if rig.get("height_m") is not None else default_height(shot.get("angle", ""), dist, actor)
    side = -1.0 if rig.get("side") == "left" else 1.0
    lateral = float(rig["lateral_m"]) if rig.get("lateral_m") is not None else side * dist * 0.25
    times = _times(t0, t1, KEY_STEP_S)
    tm = (t0 + t1) / 2

    def place(t: float, back: float, across: float, up: float) -> tuple[float, float, float]:
        px, py, pz = subj.pos(t)
        dx, dy = subj.dir(t)
        return (px - dx * back + dy * across, py - dy * back - dx * across, pz + up)

    def progress(t: float) -> float:
        return K.smooth((t - t0) / max(t1 - t0, 1e-6))

    def geometry(dist: float, height: float, side: float, lateral: float) -> tuple[list, list, float, float]:
        keys: list[tuple[float, float, float, float]] = []
        aim: list[tuple[float, float, float, float]] = []
        if kind == "static":
            keys = [(t0, *(tuple(rig["position"]) if rig.get("position") else
                           place(t0, dist, side * dist * 0.5, height)))]
        elif kind == "pan":
            px, py, pz = subj.pos(tm)
            dx, dy = subj.dir(tm)
            keys = [(t0, px + dy * side * dist, py - dx * side * dist, pz + height)]
        elif kind == "follow":
            keys = [(t, *place(t, dist, lateral, height)) for t in times]
        elif kind == "lead":
            keys = [(t, *place(t, -dist, lateral, height)) for t in times]
        elif kind == "side_track":
            keys = [(t, *place(t, 0.0, side * dist, height)) for t in times]
        elif kind in ("push", "pull"):
            for t in times:
                factor = 1.8 - progress(t) if kind == "push" else 0.8 + progress(t)
                keys.append((t, *place(t, dist * factor, lateral * factor, height)))
        elif kind == "crane":
            rise = float(rig.get("rise_m", 4.0))
            bx, by, _ = place(tm, dist, lateral, 0.0)
            base_z = subj.pos(tm)[2]
            keys = [(t, bx, by, base_z + height + rise * progress(t)) for t in times]
        elif kind == "orbit":
            arc = math.radians(float(rig.get("arc_deg", 90.0)))
            dx, dy = subj.dir(t0)
            start = math.atan2(-dy, -dx)
            for t in times:
                px, py, pz = subj.pos(t)
                theta = start + side * arc * progress(t)
                keys.append((t, px + dist * math.cos(theta), py + dist * math.sin(theta), pz + height))
        elif kind == "top_down":
            keys = [(t, subj.pos(t)[0], subj.pos(t)[1] - 0.05, subj.pos(t)[2] + max(height, 12.0)) for t in times]
        elif kind == "mount":
            forward = float(rig["distance_m"]) if rig.get("distance_m") is not None else actor.get("radius_m", 0.35) + 1.0
            if rig.get("height_m") is not None:
                up = float(rig["height_m"])
            elif shot.get("angle"):
                up = default_height(shot["angle"], forward, actor)
            else:
                up = 0.8 if actor.get("kind") == "vehicle" else actor.get("height_m", 1.7) + 0.1
            across = float(rig["lateral_m"]) if rig.get("lateral_m") is not None else 0.0
            keys = [(t, *place(t, forward, across, up)) for t in times]
            dist, height = forward, up
        elif kind == "pov":
            eye = actor.get("height_m", 1.7) * 0.93
            reach = actor.get("radius_m", 0.35) + 0.1
            for t in times:
                px, py, pz = subj.pos(t)
                dx, dy = subj.dir(t)
                keys.append((t, px + dx * reach, py + dy * reach, pz + eye))
                qx, qy, _ = subj.pos(min(t + 0.6, subj.track.end))
                aim.append((t, qx + dx * 6.0, qy + dy * 6.0, pz + eye - 0.1))
        else:
            raise ValueError(f"{shot['id']}: unknown rig type {kind!r}")
        return keys, aim, dist, height

    boxes = _boxes(blocks)
    carrier = subj if kind in ("pov", "mount") else None
    variants = [(dist, height, side, lateral)]
    if kind not in FIXED_RIGS and not (kind == "static" and rig.get("position")):
        for factor in (1.0, 0.7, 0.5):
            variants += [(dist * factor, height, s, lat * factor) for s, lat in ((side, lateral), (-side, -lateral))]
        variants.append((dist, height + 4.0, side, lateral))
    best: tuple[int, int, tuple] | None = None
    for n, variant in enumerate(variants):
        built = geometry(*variant)
        bad = _grazing(built[0], boxes, list(bodies.values()), carrier)
        if best is None or bad < best[0]:
            best = (bad, n, built)
        if bad == 0:
            break
    _, chosen, (keys, aim_keys, dist, height) = best
    keys, moved = _clear_camera(keys, boxes, list(bodies.values()), carrier=carrier)
    camera: dict[str, Any] = {"keys": [list(k) for k in keys], "interpolation": "smooth" if len(keys) > 2 else "linear"}
    if aim_keys:
        camera["aim_keys"] = [list(k) for k in aim_keys]
    else:
        camera.update(aim_actor=target_id, aim_offset=[0.0, 0.0, round(actor.get("height_m", 1.7) * 0.6, 3)])
    intensity = float(rig.get("handheld", 0.0))
    if intensity > 0:
        seed = int(hashlib.sha1(f"{shot['id']}:{t0}".encode()).hexdigest()[:6], 16)
        scale = intensity / 0.5
        camera["responses"] = [{"clock": "edit", "start": t0, "end": t1,
                                "translation_m": [round(0.03 * scale, 4), round(0.02 * scale, 4), round(0.015 * scale, 4)],
                                "rotation_deg": [round(0.35 * scale, 3), round(0.55 * scale, 3), round(0.2 * scale, 3)],
                                "frequency_hz": [1.2, 3.0], "seed": seed}]
    return camera, {"type": kind, "target": target_id, "distance_m": round(dist, 2), "height_m": round(height, 2),
                    "placement": "as_planned" if chosen == 0 else f"adjusted_{chosen}",
                    "keys_moved_for_clearance": moved}
