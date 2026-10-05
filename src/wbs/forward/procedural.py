"""Deterministic procedural generator: control spec (structure-tree sample) → scene.json.

The same seed always yields the same scene. Geometry follows the low-precision rules ([D2] 白模控制层,
[D6] 低精度): primitive blocks only, rigid proxies that only translate and turn about Z. Cameras are
placed per shot from the sampled camera move and viewpoint, then pushed out of any block they would
sit inside; anything still wrong is reported by the QC gates rather than hidden.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any

from ..blender import kinematics as K
from ..camera_language import label
from ..config import Spec, load_settings
from ..models.scene import SceneSpec
from ..taxonomy import ControlSpec
from .dressing import dress
from .motion import Waypoint, compile_route

IDENTITY_COLORS = ["#d9363e", "#2f6fdb", "#e0a800", "#2e9e5b", "#8e44ad", "#e67e22"]
COUNTS = {"single": 1, "duo": 2, "trio": 3, "crowd": 5}
HEADS = ["sphere", "cube", "octahedron"]
BODIES = ["capsule", "box", "cylinder", "taper", "ellipsoid", "bipyramid", "cone"]
VIEW_HEIGHT = {"eye_level": 1.6, "low_angle": 0.45, "high_angle": 7.0, "creature": 0.6, "panoramic": 2.2,
               "ultra_wide": 1.8, "normal": 1.7}
FRAMING_BACK = {"wide": 11.0, "medium": 6.0, "close": 3.2}
FRAMING_LENS = {"wide": 24.0, "medium": 32.0, "close": 45.0}
VIEW_ANGLE = {"eye_level": "eye", "low_angle": "low", "high_angle": "high", "creature": "low", "panoramic": "eye",
              "ultra_wide": "eye", "normal": "eye"}
GENERATOR_VERSION = "procedural/1.3"


@dataclass
class Env:
    kind: str
    blocks: list[dict[str, Any]]
    route: list[tuple[float, float]]
    altitude: float = 0.0
    lateral_limit: float = 2.5
    description: str = ""
    extras: dict[str, Any] = field(default_factory=dict)
    camera_room: float = 10.0


def _block(bid: str, shape: str, center: tuple, size: tuple, role: str = "structure", yaw: float = 0.0,
           color: str | None = None, label: str = "") -> dict[str, Any]:
    return {"id": bid, "shape": shape, "center": [round(v, 3) for v in center],
            "size": [round(v, 3) for v in size], "rotation_deg": [0.0, 0.0, round(yaw, 2)], "role": role,
            "color": color, "collision": role not in ("ground", "floor"), "label": label}


# --------------------------------------------------------------------------- routes
def _route(rng: random.Random, length: float, amplitude: float, wavelength: float) -> list[tuple[float, float]]:
    if length <= 0:
        return [(0.0, 0.0)]
    n = max(2, int(length / 3) + 1)
    phase = rng.uniform(0, 2 * math.pi)
    base = amplitude * math.sin(phase)
    return [(amplitude * math.sin(2 * math.pi * y / wavelength + phase) - base, y)
            for y in (length * i / (n - 1) for i in range(n))]


class Polyline:
    def __init__(self, points: list[tuple[float, float]]) -> None:
        self.p = points
        self.s = [0.0]
        for a, b in zip(points, points[1:]):
            self.s.append(self.s[-1] + math.dist(a, b))

    @property
    def length(self) -> float:
        return self.s[-1]

    def at(self, s: float) -> tuple[float, float]:
        if len(self.p) == 1:
            return self.p[0]
        s = min(max(s, 0.0), self.length)
        for i in range(len(self.s) - 1):
            if s <= self.s[i + 1] or i == len(self.s) - 2:
                seg = self.s[i + 1] - self.s[i] or 1.0
                u = (s - self.s[i]) / seg
                (x0, y0), (x1, y1) = self.p[i], self.p[i + 1]
                return (x0 + u * (x1 - x0), y0 + u * (y1 - y0))
        return self.p[-1]

    def x_at_y(self, y: float) -> float:
        for (x0, y0), (x1, y1) in zip(self.p, self.p[1:]):
            if y0 <= y <= y1 and y1 > y0:
                return x0 + (y - y0) / (y1 - y0) * (x1 - x0)
        return self.p[0][0] if y < self.p[0][1] else self.p[-1][0]


# --------------------------------------------------------------------------- environments
def _ground(length: float, width: float) -> dict[str, Any]:
    return _block("G00", "plane", (0.0, length / 2, -0.05), (max(80.0, width), length + 80.0, 0.1), "ground",
                  label="地面")


def _clear_of_route(line: Polyline, x: float, y: float, half: float, margin: float) -> bool:
    return abs(x - line.x_at_y(y)) - half > margin


def _env_plaza(rng: random.Random, length: float, identity: bool) -> Env:
    route = _route(rng, length, amplitude=rng.uniform(1.0, 4.0), wavelength=rng.uniform(25, 45))
    line = Polyline(route)
    blocks = [_ground(length, 60)]
    n = int(max(6, length / 4))
    for _ in range(n * 3):
        if len(blocks) > n:
            break
        y = rng.uniform(-6, length + 6)
        side = rng.choice((-1, 1))
        w, d, h = rng.uniform(0.8, 3.0), rng.uniform(0.8, 3.0), rng.uniform(0.6, 4.0)
        x = line.x_at_y(y) + side * rng.uniform(3.5, 11.0)
        if not _clear_of_route(line, x, y, max(w, d) / 2, 2.0):
            continue
        shape = rng.choice(("box", "box", "cylinder"))
        blocks.append(_block(f"B{len(blocks):02}", shape, (x, y, h / 2), (w, d, h), "obstacle", rng.uniform(0, 90)))
    ex, ey = line.at(length)
    gate_w = 5.0
    for sx in (-1, 1):
        blocks.append(_block(f"B{len(blocks):02}", "box", (ex + sx * gate_w / 2, ey + 6, 2.5), (0.8, 0.8, 5.0),
                             "landmark", label="门柱"))
    blocks.append(_block(f"B{len(blocks):02}", "box", (ex, ey + 6, 5.3), (gate_w + 0.8, 0.8, 0.6), "landmark",
                         label="门楣"))
    return Env("plaza", blocks, route, lateral_limit=2.5,
               description=f"开阔广场：灰色地面，约 {len(blocks) - 4} 个方块/圆柱障碍分布在行进路线两侧，终点有一座门形体块。")


def _env_street(rng: random.Random, length: float, identity: bool) -> Env:
    route = _route(rng, length, amplitude=rng.uniform(0.0, 1.0), wavelength=60)
    blocks = [_ground(length, 80)]
    for side in (-1, 1):
        blocks.append(_block(f"K{len(blocks):02}", "box", (side * 7.6, length / 2, 0.1), (0.4, length + 40, 0.2),
                             "curb", label="路沿"))
        y = -15.0
        while y < length + 15:
            seg = rng.uniform(8, 20)
            depth, height = rng.uniform(8, 14), rng.uniform(6, 30)
            blocks.append(_block(f"H{len(blocks):02}", "box", (side * (9.5 + depth / 2), y + seg / 2, height / 2),
                                 (depth, seg, height), "building", label="楼体"))
            y += seg + rng.uniform(2, 6)
    return Env("street", blocks, route, lateral_limit=4.0,
               description="城市街道：双向街道两侧为高低不一的长方体楼群，路沿为细长体块，楼与楼之间留有巷口。")


def _env_rooms(rng: random.Random, length: float, identity: bool) -> Env:
    route = [(0.0, 0.0), (0.0, max(length, 12.0))]
    total = max(length, 12.0)
    blocks = [_ground(total, 40)]
    width = rng.uniform(6.0, 9.0)
    y = -4.0
    room = 0
    floor_colors = ["#e7e1d8", "#dde6e0", "#e0e2ea", "#ebe4e0"]
    while y < total + 4:
        seg = rng.uniform(8, 12)
        room += 1
        blocks.append(_block(f"F{room:02}", "box", (0.0, y + seg / 2, 0.01), (width, seg, 0.02), "floor",
                             color=floor_colors[room % 4] if identity else None, label=f"房间{room}地面"))
        for side in (-1, 1):
            blocks.append(_block(f"W{len(blocks):02}", "box", (side * width / 2, y + seg / 2, 1.6), (0.2, seg, 3.2),
                                 "wall", label="墙体"))
        door = 1.8
        side_len = (width - door) / 2
        for side in (-1, 1):
            blocks.append(_block(f"W{len(blocks):02}", "box", (side * (door / 2 + side_len / 2), y + seg, 1.6),
                                 (side_len, 0.2, 3.2), "wall", label="隔墙"))
        for _ in range(rng.randint(1, 3)):
            fx = rng.choice((-1, 1)) * rng.uniform(1.8, width / 2 - 0.8)
            fw, fd, fh = rng.uniform(0.6, 1.6), rng.uniform(0.5, 1.2), rng.uniform(0.4, 1.8)
            fy = y + rng.uniform(1.5, seg - 1.5)
            blocks.append(_block(f"P{len(blocks):02}", "box", (fx, fy, fh / 2), (fw, fd, fh), "furniture",
                                 label="家具"))
        y += seg
    return Env("rooms", blocks, route, lateral_limit=1.0,
               description=f"室内房间链：宽约 {width:.1f} 米的房间沿南北方向相连，隔墙中央留 1.8 米门洞，室内有少量家具体块。",
               extras={"camera_room": max(width / 2 - 0.9, 1.0)})


def _env_towers(rng: random.Random, length: float, identity: bool) -> Env:
    route = _route(rng, length, amplitude=rng.uniform(2.0, 5.0), wavelength=rng.uniform(50, 90))
    line = Polyline(route)
    altitude = rng.uniform(10.0, 14.0)
    blocks = [_ground(length, 90)]
    y = -10.0
    while y < length + 10:
        for side in (-1, 1):
            w, h = rng.uniform(4, 9), rng.uniform(18, 60)
            x = line.x_at_y(y) + side * rng.uniform(8, 16)
            blocks.append(_block(f"T{len(blocks):02}", rng.choice(("box", "cylinder")), (x, y, h / 2), (w, w, h),
                                 "tower", label="塔楼"))
        if rng.random() < 0.45:
            below = rng.random() < 0.5
            z = altitude - rng.uniform(4, 6) if below else altitude + rng.uniform(5, 8)
            blocks.append(_block(f"R{len(blocks):02}", "box", (line.x_at_y(y), y, z), (30, 3, 1.6), "bridge",
                                 label="连桥"))
        y += rng.uniform(14, 24)
    return Env("towers", blocks, route, altitude=altitude, lateral_limit=3.0,
               description="高层建筑群：飞行通道两侧是方柱或圆柱塔楼，通道上方或下方不时横跨连桥体块。")


def _env_canyon(rng: random.Random, length: float, identity: bool, altitude: float = 0.0) -> Env:
    route = _route(rng, length, amplitude=rng.uniform(2.0, 5.0), wavelength=rng.uniform(40, 70))
    line = Polyline(route)
    blocks = [_ground(length, 70)]
    y = -15.0
    while y < length + 15:
        seg = rng.uniform(10, 18)
        for side in (-1, 1):
            h = rng.uniform(15, 40)
            x = line.x_at_y(y + seg / 2) + side * rng.uniform(14, 18)
            blocks.append(_block(f"C{len(blocks):02}", "box", (x, y + seg / 2, h / 2), (6, seg, h), "canyon_wall",
                                 rng.uniform(-6, 6), label="谷壁"))
        y += seg
    for _ in range(int(length / 10) + 3):
        yy = rng.uniform(0, length)
        x = line.x_at_y(yy) + rng.choice((-1, 1)) * rng.uniform(4.5, 10.0)
        d, h = rng.uniform(1.5, 4.0), rng.uniform(6.0, 24.0)
        blocks.append(_block(f"P{len(blocks):02}", "cylinder", (x, yy, h / 2), (d, d, h), "pillar", label="石柱"))
    return Env("canyon", blocks, route, altitude=altitude, lateral_limit=2.5,
               description="峡谷：两侧为高低起伏的长方体谷壁，谷底散布粗壮的圆柱石柱，地面合并为一整块平面。")


def _env_seabed(rng: random.Random, length: float, identity: bool) -> Env:
    route = _route(rng, length, amplitude=rng.uniform(1.0, 3.0), wavelength=rng.uniform(20, 40))
    line = Polyline(route)
    blocks = [_ground(length, 50)]
    for _ in range(int(length / 2) + 6):
        yy = rng.uniform(-4, length + 4)
        x = line.x_at_y(yy) + rng.choice((-1, 1)) * rng.uniform(2.5, 9.0)
        if rng.random() < 0.5:
            h = rng.uniform(2.5, 6.0)
            blocks.append(_block(f"K{len(blocks):02}", "cylinder", (x, yy, h / 2), (0.3, 0.3, h), "kelp", label="海草"))
        else:
            s = rng.uniform(0.6, 2.0)
            blocks.append(_block(f"R{len(blocks):02}", "box", (x, yy, s / 3), (s, s * 0.8, s / 1.5), "rock",
                                 rng.uniform(0, 90), label="礁石"))
    return Env("seabed", blocks, route, altitude=rng.uniform(1.0, 1.8), lateral_limit=1.5,
               description="海底：平整的海床上散布礁石体块和细长圆柱海草，主体在低空游动。")


def _env_pedestal(rng: random.Random, length: float, identity: bool) -> Env:
    blocks = [_ground(10, 40), _block("S01", "cylinder", (0.0, 0.0, 0.5), (1.2, 1.2, 1.0), "pedestal", label="展台")]
    for i, (x, y) in enumerate(((-6, 5), (6, 6), (0, 9))):
        h = rng.uniform(2.0, 4.0)
        blocks.append(_block(f"D{i:02}", "box", (x, y, h / 2), (rng.uniform(2, 4), 0.6, h), "backdrop", label="背景板"))
    return Env("pedestal", blocks, [(0.0, 0.0)], altitude=1.0, lateral_limit=0.0,
               description="产品展台：画面中心是一个圆柱展台，后方有几块竖直背景板。")


CAMERA_ROOM = {"plaza": 12.0, "street": 6.0, "towers": 5.0, "canyon": 9.0, "seabed": 6.0, "pedestal": 8.0}


def _environment(control: ControlSpec, rng: random.Random, length: float) -> Env:
    env = _pick_environment(control, rng, length)
    env.camera_room = CAMERA_ROOM.get(env.kind, env.extras.get("camera_room", 10.0))
    return env


def _pick_environment(control: ControlSpec, rng: random.Random, length: float) -> Env:
    s, c, identity = control.subject, control.subject_child, control.color == "identity"
    if s == "none":
        return {"fpv_architecture": _env_towers, "indoor_walkthrough": _env_rooms, "nature": _env_canyon,
                "city": _env_street}[c](rng, length, identity)
    if s == "person":
        return rng.choice((_env_plaza, _env_street))(rng, length, identity)
    if s == "animal":
        if c == "water":
            return _env_seabed(rng, length, identity)
        if c == "air":
            return _env_canyon(rng, length, identity, altitude=rng.uniform(10.0, 16.0))
        return rng.choice((_env_plaza, _env_canyon))(rng, length, identity)
    if c == "vehicle":
        return _env_street(rng, length, identity)
    if c == "product":
        return _env_pedestal(rng, length, identity)
    return rng.choice((_env_plaza, _env_rooms))(rng, length, identity)


# --------------------------------------------------------------------------- subjects
def _speed(control: ControlSpec, rng: random.Random) -> float:
    s, c, motion = control.subject, control.subject_child, control.content_class == "motion"
    if s == "person":
        return rng.uniform(4.5, 6.5) if motion else rng.uniform(1.3, 2.2)
    if s == "animal":
        return {"land": rng.uniform(5, 9), "water": rng.uniform(1.5, 3.0), "air": rng.uniform(8, 12)}[c]
    if s == "object":
        return {"vehicle": rng.uniform(9, 14), "product": 0.0, "robot": rng.uniform(1.0, 1.6)}[c]
    return {"fpv_architecture": rng.uniform(8, 12), "indoor_walkthrough": rng.uniform(1.3, 2.0),
            "nature": rng.uniform(6, 10), "city": rng.uniform(5, 9)}[c]


def _actor_template(control: ControlSpec, rng: random.Random) -> dict[str, Any]:
    s, c = control.subject, control.subject_child
    if s == "person":
        return {"kind": "pawn", "height_m": round(rng.uniform(1.6, 1.85), 2), "radius_m": 0.35}
    if s == "animal":
        return {"land": {"kind": "block_animal", "height_m": 1.0, "radius_m": 0.95},
                "water": {"kind": "block_fish", "height_m": 0.5, "radius_m": 0.65},
                "air": {"kind": "block_bird", "height_m": 0.35, "radius_m": 0.95, "flap_hz": 2.5}}[c]
    return {"vehicle": {"kind": "vehicle", "height_m": 1.5, "radius_m": 2.3},
            "product": {"kind": "prop", "height_m": 0.6, "radius_m": 0.4},
            "robot": {"kind": "robot", "height_m": 2.0, "radius_m": 0.55}}[c]


def _route_waypoints(control: ControlSpec, env: Env, rng: random.Random, duration: float,
                     speed: float) -> tuple[list[Waypoint], float]:
    """Main subject route as waypoints: cruise speed (sprint rhythm for motion films), pauses as stops."""
    line = Polyline(env.route)
    motion = control.content_class == "motion"
    drawn = [] if motion or speed == 0 else sorted((rng.uniform(0.2, 0.7) * duration, rng.uniform(0.8, 1.5))
                                                    for _ in range(rng.randint(0, 2)))
    pauses: list[tuple[float, float]] = []
    for start, length in drawn:
        if pauses and start <= pauses[-1][0] + pauses[-1][1] + 1.0:
            pauses[-1] = (pauses[-1][0], max(pauses[-1][1], start + length - pauses[-1][0]))
        else:
            pauses.append((start, length))
    cruise = min(speed, line.length / max(duration - sum(p for _, p in pauses), 1e-6) * 0.98)
    stops, clock_lost = [], 0.0
    for start, length in pauses:
        stops.append((max(0.5, cruise * (start - clock_lost)), length))
        clock_lost += length

    def z_at(s: float) -> float:
        z = env.altitude
        if control.subject_child == "air":
            z += 1.2 * math.sin(2 * math.pi * s / max(4.0 * cruise, 1e-6))
        elif control.subject_child == "water":
            z += 0.3 * math.sin(2 * math.pi * s / max(3.0 * cruise, 1e-6))
        return z

    def speed_at(s: float) -> float:
        if not motion:
            return cruise
        return cruise * (1.0 + 0.25 * math.sin(2 * math.pi * s / max(cruise * duration / 2, 1.0)))

    marks = sorted([(s, 0.0) for s in line.s] + [(s, stop) for s, stop in stops if s < line.length])
    wps = []
    for s, stop in marks:
        x, y = line.at(s)
        wps.append(Waypoint(x, y, z_at(s), stop_s=stop, speed_mps=speed_at(s)))
    return wps, cruise


def _offset_waypoints(wps: list[Waypoint], lateral: float, lead_in: float) -> list[Waypoint]:
    """Parallel offset (right of travel for positive ``lateral``) with a straight lead-in behind the start."""
    out = []
    for i, w in enumerate(wps):
        a, b = wps[max(i - 1, 0)], wps[min(i + 1, len(wps) - 1)]
        dx, dy = _norm(b.x - a.x, b.y - a.y)
        out.append(Waypoint(w.x + dy * lateral, w.y - dx * lateral, w.z, stop_s=w.stop_s, speed_mps=w.speed_mps))
    if lead_in > 0 and len(out) > 1:
        dx, dy = _norm(out[1].x - out[0].x, out[1].y - out[0].y)
        out.insert(0, Waypoint(out[0].x - dx * lead_in, out[0].y - dy * lead_in, out[0].z, speed_mps=out[0].speed_mps))
    return out


def _actors(control: ControlSpec, env: Env, rng: random.Random, duration: float, speed: float) -> list[dict]:
    if control.subject == "none":
        return []
    count = COUNTS.get(control.subject_child, 1)
    main, cruise = _route_waypoints(control, env, rng, duration, speed)
    template = _actor_template(control, rng)
    limits = load_settings().qc.dynamic_gate.limits(template["kind"])
    actors = []
    gap = min(1.8, env.lateral_limit / 2 if count > 3 else env.lateral_limit)
    offsets = [0.0, gap, -gap, 2 * gap, -2 * gap]
    for i in range(count):
        aid = "ABCDEFGH"[i]
        delay = 0.0 if i == 0 else min(0.9, 2.5 / max(speed, 0.1)) * i
        lateral = max(-env.lateral_limit, min(env.lateral_limit, offsets[i])) if i else 0.0
        actor = {"id": aid, "label": f"主体{aid}", "role": "主体" if i == 0 else "跟随者", **template,
                 "head": rng.choice(HEADS), "body": rng.choice(BODIES),
                 "color": IDENTITY_COLORS[i] if control.color == "identity" else None, "facing_marker": True}
        if control.subject_child == "product" or cruise <= 0 or len(main) < 2:
            actor["path"] = {"keys": [[0.0, 0.0, 0.0, 1.0]], "interpolation": "linear"}
            actor["yaw_keys"] = [[0.0, 0.0], [duration, 120.0]]
        else:
            route = _offset_waypoints(main, lateral, cruise * delay) if i else main
            keys = compile_route(route, limits, duration, default_speed=cruise, start_moving=True, label=aid)
            actor["path"] = {"keys": keys, "interpolation": "smooth"}
        actors.append(actor)
    return actors


# --------------------------------------------------------------------------- cameras
def _cuts(rng: random.Random, duration: float, count: int, fps: int, min_len: float = 2.0,
          max_len: float | None = None) -> list[tuple[float, float]]:
    if count <= 1:
        return [(0.0, duration)]
    bounds: list[float] = []
    for _ in range(300):
        points = sorted(round(rng.uniform(min_len, duration - min_len) * fps) / fps for _ in range(count - 1))
        bounds = [0.0, *points, duration]
        if all(min_len - 1e-9 <= b - a <= (max_len or duration) + 1e-9 for a, b in zip(bounds, bounds[1:])):
            break
    else:
        bounds = [round(duration * i / count * fps) / fps for i in range(count)] + [duration]
    return list(zip(bounds, bounds[1:]))


def _norm(x: float, y: float) -> tuple[float, float]:
    length = math.hypot(x, y)
    return (x / length, y / length) if length > 1e-6 else (0.0, 1.0)


class Subject:
    """Position / direction of the thing the camera cares about (an actor, a group, or the route itself)."""

    def __init__(self, keys: list, height: float, flyer: bool, extent: float = 0.0, radius: float = 0.35) -> None:
        self.track = K.Track(keys, "cubic")
        self.height = height
        self.flyer = flyer
        self.extent = extent
        self.radius = radius

    @classmethod
    def group(cls, members: list[Subject]) -> Subject:
        times = sorted({round(t, 4) for m in members for t in m.track.t})
        keys, extent = [], 0.0
        for t in times:
            pts = [m.pos(t) for m in members]
            c = tuple(sum(p[i] for p in pts) / len(pts) for i in range(3))
            extent = max(extent, max(math.dist(p[:2], c[:2]) for p in pts))
            keys.append((t, *c))
        return cls(keys, max(m.height for m in members), members[0].flyer, extent, max(m.radius for m in members))

    def pos(self, t: float) -> tuple[float, float, float]:
        return self.track.at(t)

    def dir(self, t: float) -> tuple[float, float]:
        vx, vy, _ = self.track.velocity(t, dt=0.4)
        if math.hypot(vx, vy) < 0.2:
            vx, vy, _ = self.track.velocity(min(t + 1.0, self.track.end), dt=0.6)
        return _norm(vx, vy)


def _times(t0: float, t1: float, step: float = 0.5) -> list[float]:
    out = [t0 + i * step for i in range(int((t1 - t0) / step) + 1)]
    if t1 - out[-1] > 1e-6:
        out.append(t1)
    return [round(t, 4) for t in out]


def _design(move: str, view: str, framing: str, side: int, t0: float, t1: float, subj: Subject,
            second: Subject | None, has_actor: bool, rng: random.Random, lead: Subject | None = None,
            room: float = 10.0) -> dict[str, Any]:
    """Camera keys for one shot. ``subj`` is what to frame (a group centroid when there are several
    actors); ``lead`` is actor A (POV carrier, focus-shift start); ``room`` caps sideways offsets."""
    lead = lead or subj
    group = has_actor and subj is not lead
    h = VIEW_HEIGHT[view]
    size = max(1.0, subj.height / 1.7) if has_actor else 1.0
    back = FRAMING_BACK[framing] * size + subj.extent * 1.5 + (subj.radius if has_actor else 0.0)

    def lat(value: float) -> float:
        return max(-room, min(room, value))
    lens = {"panoramic": 14.0, "ultra_wide": 18.0}.get(view, FRAMING_LENS[framing])
    if group and subj.extent > 1.5:
        lens = min(lens, 24.0)
    times = _times(t0, t1)
    keys, aim_keys, roll = [], [], []
    aim_actor = "A" if has_actor else None
    responses: list[dict[str, Any]] = []

    def place(t: float, dist: float, lateral: float, height: float) -> tuple[float, float, float]:
        px, py, pz = subj.pos(t)
        dx, dy = subj.dir(t)
        rx, ry = dy, -dx
        return (px - dx * dist + rx * lateral, py - dy * dist + ry * lateral, pz + height)

    if not has_actor:
        def ahead(t: float, dist: float = 8.0) -> tuple[float, float, float]:
            p = subj.pos(t)
            q = subj.pos(min(t + 1.5, subj.track.end))
            if math.dist(p[:2], q[:2]) < 3.0:
                dx, dy = subj.dir(t)
                q = (p[0] + dx * dist, p[1] + dy * dist, p[2])
            return q

        for t in times:
            u = (t - t0) / max(t1 - t0, 1e-6)
            if move == "pull":
                tt = t0 + t1 - t
                p, a = subj.pos(tt), ahead(tt)
            elif move == "pan":
                tm = (t0 + t1) / 2
                pm = subj.pos(tm)
                dx, dy = subj.dir(tm)
                off = lat(side * 6.0)
                p = (pm[0] + dy * off, pm[1] - dx * off, pm[2])
                a = subj.pos(t0 + (t1 - t0) * u)
            elif move == "truck":
                dx, dy = subj.dir(t)
                p0 = subj.pos(t)
                off = lat(side * 4.0)
                p = (p0[0] + dy * off, p0[1] - dx * off, p0[2])
                a = (p0[0] - dy * side * 5.0, p0[1] + dx * side * 5.0, p0[2])
            elif move == "top_down":
                p0 = subj.pos(t)
                p, a = (p0[0], p0[1] - 0.05, p0[2] + 30.0), (p0[0], p0[1], 0.0)
            else:
                p, a = subj.pos(t), ahead(t)
            base_h = 0.0 if move == "top_down" else (h if not subj.flyer else 0.0)
            keys.append((t, p[0], p[1], p[2] + base_h + (1.5 * math.sin(math.pi * u) if move == "one_take" else 0)))
            aim_keys.append((t, a[0], a[1], a[2] + (0 if move == "top_down" else base_h * 0.9)))
            if move == "fpv":
                dx0, dy0 = subj.dir(max(t - 0.5, 0))
                dx1, dy1 = subj.dir(min(t + 0.5, t1))
                turn = math.degrees(math.atan2(dx0 * dy1 - dy0 * dx1, dx0 * dx1 + dy0 * dy1))
                roll.append((t, max(-10.0, min(10.0, -turn * 0.8))))
        aim_actor = None
    else:
        for t in times:
            u = (t - t0) / max(t1 - t0, 1e-6)
            if move in ("follow", "handheld"):
                keys.append((t, *place(t, back, lat(side * back * 0.35), h)))
            elif move == "push":
                keys.append((t, *place(t, back * (2.2 - 1.5 * K.smooth(u)), lat(side * back * 0.3), h)))
            elif move == "pull":
                keys.append((t, *place(t, back * (0.7 + 1.7 * K.smooth(u)), lat(side * back * 0.3), h)))
            elif move == "pan":
                tm = (t0 + t1) / 2
                px, py, pz = subj.pos(tm)
                dx, dy = subj.dir(tm)
                d = lat(side * max(8.0, back * 1.6))
                keys.append((t, px + dy * d, py - dx * d, pz + h))
            elif move == "truck":
                keys.append((t, *place(t, 0.0, lat(side * max(6.0, back * 1.2)), h)))
            elif move == "fpv":
                keys.append((t, *place(t, back * 0.6, side * 0.6 * math.sin(2 * math.pi * u), 1.4)))
                roll.append((t, 6.0 * math.sin(2 * math.pi * u) * side))
            elif move == "top_down":
                px, py, pz = subj.pos(t)
                keys.append((t, px, py - 0.05, pz + 12.0 + back))
            elif move == "one_take":
                theta = math.radians(180 if u < 0.35 else 180 - 120 * K.smooth((u - 0.35) / 0.35) if u < 0.7
                                     else 60 - 40 * K.smooth((u - 0.7) / 0.3))
                px, py, pz = subj.pos(t)
                dx, dy = subj.dir(t)
                radius = back * (1.0 + 0.3 * math.sin(math.pi * u))
                along = math.cos(theta) * radius
                across = max(-room, min(room, math.sin(theta) * radius))
                rx, ry = -dy, dx
                keys.append((t, px + dx * along + rx * across, py + dy * along + ry * across,
                             pz + h + 4.0 * K.smooth((u - 0.7) / 0.3)))
            elif move == "focus_shift" and second is not None:
                tm = (t0 + t1) / 2
                px, py, pz = subj.pos(tm)
                dx, dy = subj.dir(tm)
                d = lat(side * (9.0 + subj.extent))
                keys.append((t, px + dy * d - dx * 4.0, py - dx * d - dy * 4.0, pz + h))
                a, b = lead.pos(t), second.pos(t)
                w = K.smooth((u - 0.3) / 0.4)
                aim_keys.append((t, a[0] + (b[0] - a[0]) * w, a[1] + (b[1] - a[1]) * w,
                                 a[2] + (b[2] - a[2]) * w + lead.height * 0.6))
            elif move == "pov":
                px, py, pz = lead.pos(t)
                dx, dy = lead.dir(t)
                eye = pz + lead.height * 0.93
                keys.append((t, px + dx * 0.45, py + dy * 0.45, eye))
                qx, qy, _ = lead.pos(min(t + 0.6, lead.track.end))
                aim_keys.append((t, qx + dx * 6.0, qy + dy * 6.0, eye - 0.1))
            else:
                keys.append((t, *place(t, back, side * back * 0.35, h)))
            if group and move not in ("pov", "focus_shift"):
                gx, gy, gz = subj.pos(t)
                aim_keys.append((t, gx, gy, gz + subj.height * 0.6))
        if aim_keys:
            aim_actor = None
        if move == "pov":
            lens = 22.0
    if move == "handheld":
        responses.append({"clock": "edit", "start": t0, "end": t1, "translation_m": [0.03, 0.02, 0.015],
                          "rotation_deg": [0.35, 0.55, 0.2], "frequency_hz": [1.2, 3.0],
                          "seed": rng.randrange(1, 10**6)})
    if move == "top_down":
        lens = 28.0
    if move == "fpv":
        lens = 20.0
    return {"keys": keys, "aim_keys": aim_keys, "aim_actor": aim_actor, "roll": roll, "lens": lens,
            "responses": responses}


def _boxes(blocks: list[dict]) -> list[tuple]:
    return [K.block_aabb(b) for b in blocks if b.get("collision", True) and b["role"] not in ("ground", "floor")]


REMOVABLE_ROLES = {"obstacle", "pillar", "rock", "kelp", "furniture"}


def _clear_props(env: Env, cam_tracks: list[K.Track], actors: list[dict], hz: float = 24.0) -> list[str]:
    """Like a set dresser: take loose props out of the camera corridor and the actors' paths."""
    cam_points = []
    for track in cam_tracks:
        n = max(1, int((track.end - track.start) * hz))
        cam_points += [track.at(track.start + (track.end - track.start) * i / n) for i in range(n + 1)]
    actor_samples = []
    for actor in actors:
        track = K.Track(actor["path"]["keys"], actor["path"].get("interpolation", "cubic"))
        n = max(1, int((track.end - track.start) * 8))
        actor_samples += [(track.at(track.start + (track.end - track.start) * i / n), actor["radius_m"] + 0.3,
                           actor["height_m"]) for i in range(n + 1)]
    removed, kept = [], []
    for block in env.blocks:
        if block["role"] in REMOVABLE_ROLES:
            lo, hi = K.block_aabb(block)
            near_cam = any(K.point_aabb_distance(p, lo, hi) < 0.8 for p in cam_points)
            near_actor = any(K.cylinder_box_penetration(c, r, h, lo, hi) > 0 for c, r, h in actor_samples)
            if near_cam or near_actor:
                removed.append(block["id"])
                continue
        kept.append(block)
    env.blocks = kept
    return removed


def _clear_camera(keys: list[tuple], boxes: list[tuple], bodies: list[Subject], carrier: Subject | None = None,
                  hz: float = 24.0, rounds: int = 6) -> tuple[list[tuple], int]:
    """Resolve keys, then add resolved keys wherever the interpolated path still grazes a block or actor.

    ``carrier`` is the actor a POV camera rides on; it is exempt from the actor distance rule."""
    others = [b for b in bodies if b is not carrier]
    keys, moved = _resolve_clearance(keys, boxes, others)

    def too_close(t: float, p: tuple) -> bool:
        return (any(K.point_aabb_distance(p, lo, hi) < 0.45 for lo, hi in boxes)
                or any(_actor_distance(b, t, p) < 0.6 for b in others))

    for _ in range(rounds):
        track = K.Track(keys, "cubic")
        t0, t1 = keys[0][0], keys[-1][0]
        n = max(1, int((t1 - t0) * hz))
        existing = {round(k[0], 4) for k in keys}
        bad = [t for t in (round(t0 + (t1 - t0) * i / n, 4) for i in range(n + 1))
               if t not in existing and too_close(t, track.at(t))]
        if not bad:
            break
        extra, fixed = _resolve_clearance([(t, *track.at(t)) for t in bad], boxes, others)
        moved += fixed
        keys = sorted(keys + extra)
    return keys, moved


def _actor_distance(body: Subject, t: float, p: tuple) -> float:
    """Distance from a point to the actor's upright cylinder (0 inside)."""
    sx, sy, sz = body.pos(t)
    dh = max(math.hypot(p[0] - sx, p[1] - sy) - body.radius, 0.0)
    dz = max(sz - p[2], 0.0, p[2] - (sz + body.height))
    return math.hypot(dh, dz)


def _resolve_clearance(keys: list[tuple], boxes: list[tuple], bodies: list[Subject],
                       margin: float = 0.6, actor_margin: float = 0.9) -> tuple[list[tuple], int]:
    fixed, moved = [], 0
    for t, x, y, z in keys:
        p = [x, y, max(z, 0.35)]
        for _ in range(4):
            changed = False
            for _ in range(25):
                hit = next(((lo, hi) for lo, hi in boxes if K.point_aabb_distance(tuple(p), lo, hi) < margin), None)
                if hit is None:
                    break
                moved += 1
                changed = True
                lo, hi = hit
                if hi[2] < 10.0:
                    p[2] = hi[2] + margin + 0.3
                    continue
                exits = [(p[0] - lo[0], (-1, 0)), (hi[0] - p[0], (1, 0)), (p[1] - lo[1], (0, -1)),
                         (hi[1] - p[1], (0, 1))]
                dist, (ux, uy) = min(exits, key=lambda e: e[0])
                p[0] += ux * (max(dist, 0) + margin + 0.1)
                p[1] += uy * (max(dist, 0) + margin + 0.1)
            for body in bodies:
                if _actor_distance(body, t, tuple(p)) >= actor_margin:
                    continue
                sx, sy, sz = body.pos(t)
                dx, dy = p[0] - sx, p[1] - sy
                horiz = math.hypot(dx, dy)
                if horiz < 1e-3:
                    dx, dy, horiz = 0.0, -1.0, 1.0
                reach = body.radius + actor_margin + 0.05
                p = [sx + dx / horiz * reach, sy + dy / horiz * reach, p[2]]
                moved += 1
                changed = True
            if not changed:
                break
        fixed.append((t, round(p[0], 3), round(p[1], 3), round(p[2], 3)))
    return fixed, moved


# --------------------------------------------------------------------------- scene
def generate(control: ControlSpec, spec: Spec, job_id: str, title: str | None = None,
             moves: list[str] | None = None, max_shot_s: float | None = None) -> SceneSpec:
    """``moves`` optionally overrides the camera move per shot (cycled), e.g. for story films."""
    rng = random.Random(control.seed)
    duration, fps = float(spec.duration_s), int(spec.fps)
    speed = _speed(control, rng)
    env = _environment(control, rng, length=max(speed * duration, 20.0) if speed else 0.0)
    actors = _actors(control, env, rng, duration, speed)

    flyer = control.subject_child in ("air", "water")
    bodies = [Subject(a["path"]["keys"], a["height_m"], flyer, radius=a["radius_m"]) for a in actors]
    if bodies:
        lead = bodies[0]
        subj = Subject.group(bodies) if len(bodies) > 1 else lead
        second = bodies[1] if len(bodies) > 1 else None
    else:
        line = Polyline(env.route)
        cam_z = env.altitude if control.subject_child == "fpv_architecture" else 0.0
        keys = [(t, *line.at(line.length * t / duration), cam_z) for t in _times(0.0, duration, 0.5)]
        subj = lead = Subject(keys, 1.7, control.subject_child == "fpv_architecture")
        second = None

    windows = _cuts(rng, duration, control.shot_count if control.shot_form == "multi_shot" else 1, fps,
                    max_len=max_shot_s)
    framings = ["medium"] if len(windows) == 1 else (["wide", "medium", "close", "medium", "wide"] * 2)[: len(windows)]
    labels = control.labels()
    shot_moves = [moves[i % len(moves)] if moves else control.camera_move for i in range(len(windows))]
    designs = [_design(shot_moves[i], control.viewpoint, framings[i], 1 if i % 2 == 0 else -1, t0, t1,
                       subj, second, bool(actors), rng, lead=lead, room=env.camera_room)
               for i, (t0, t1) in enumerate(windows)]
    removed = _clear_props(env, [K.Track(d["keys"], "cubic") for d in designs], actors)
    boxes = _boxes(env.blocks)
    shots, moved_total = [], 0
    for i, ((t0, t1), design) in enumerate(zip(windows, designs)):
        carrier = lead if shot_moves[i] == "pov" and bodies else None
        keys, moved = _clear_camera(design["keys"], boxes, bodies, carrier=carrier)
        moved_total += moved
        camera: dict[str, Any] = {"keys": [list(k) for k in keys], "interpolation": "cubic",
                                  "aim_keys": [list(k) for k in design["aim_keys"]],
                                  "aim_actor": design["aim_actor"], "roll_keys": [list(r) for r in design["roll"]],
                                  "responses": design["responses"]}
        if design["aim_actor"]:
            camera["aim_offset"] = [0.0, 0.0, round(actors[0]["height_m"] * 0.6, 3)]
        shots.append({
            "id": f"S{i + 1:02}", "start_s": t0, "end_s": t1, "lens_mm": design["lens"], "camera": camera,
            "title": f"{label('framing', framings[i])}·{label('move', shot_moves[i])}", "framing": framings[i],
            "move": shot_moves[i], "angle": "overhead" if shot_moves[i] == "top_down" else VIEW_ANGLE[control.viewpoint],
            "action": _action_text(control, actors, t0, t1),
        })

    events = [{"id": "E01", "at_s": 0.0, "mechanism": "起点", "choice": "", "consequence": "主体进入画面"},
              {"id": "E02", "at_s": round(duration / 2, 3), "mechanism": "行进中段", "choice": "",
               "consequence": "空间关系与运动节奏延续"},
              {"id": "E03", "at_s": round(max(duration - 1.0, 0.0), 3), "mechanism": "终点", "choice": "",
               "consequence": "到达路线终点"}]
    data = {
        "schema": "wbs.scene/1.0", "id": job_id,
        "title": title or f"{labels['subject_label']}·{labels['camera_move_label'].split('·')[-1]}",
        "fps": fps, "duration_s": duration, "resolution": list(spec.resolution), "precision": "low",
        "palette": {"white": "white", "grey": "grey", "identity": "identity"}[control.color],
        "time_map": None, "blocks": env.blocks, "actors": actors, "shots": shots, "events": events,
        "render": {"engine": "workbench"},
        "source": {"kind": "forward_batch", "control": control.as_dict(), "generator": GENERATOR_VERSION},
        "meta": {"labels": labels, "environment": env.kind, "environment_description": env.description,
                 "speed_mps": round(speed, 3), "camera_keys_moved_for_clearance": moved_total,
                 "props_removed_for_clearance": removed},
    }
    if env.kind == "street":
        dress(data, load_settings().forward.dressing)
    if actors:
        _label_from_measurement(data)
    return SceneSpec.model_validate(data)


def _label_from_measurement(data: dict[str, Any]) -> None:
    """Shot framing/angle labels from what the camera actually frames (the design presets are distances, not bands)."""
    from ..qc.dynamic import framing_precheck

    measured = {s["shot"]: s for s in framing_precheck(data, cfg=load_settings().qc.framing).get("shots", [])
                if s.get("measured_framing")}
    intent = {}
    for shot in data["shots"]:
        m = measured.get(shot["id"])
        if m is None:
            continue
        intent[shot["id"]] = {"framing": shot["framing"], "angle": shot["angle"]}
        shot["framing"] = m["measured_framing"]
        if shot["move"] != "top_down":
            shot["angle"] = m["measured_angle"]
        shot["title"] = f"{label('framing', shot['framing'])}·{label('move', shot['move'])}"
    data["meta"]["framing_intent"] = intent


def _action_text(control: ControlSpec, actors: list[dict], t0: float, t1: float) -> str:
    if not actors:
        return "无主体：相机沿空间路线推进，交代空间结构"
    lead = "主体A"
    if len(actors) == 1:
        verb = {"pawn": "行进", "block_animal": "奔跑", "block_bird": "飞行", "block_fish": "游动", "vehicle": "行驶",
                "robot": "行走", "prop": "静置旋转展示"}[actors[0]["kind"]]
        return f"{lead}{verb}（{t0:.2f}–{t1:.2f} 秒）"
    others = "、".join(f"主体{a['id']}" for a in actors[1:])
    return f"{lead}领先行进，{others}依次跟随（{t0:.2f}–{t1:.2f} 秒）"
