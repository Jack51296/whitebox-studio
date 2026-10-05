"""Pure-Python kinematics shared by the Blender builder and the Python-side QC (stdlib only, no bpy).

``TimeMap``, ``smooth`` and ``sample_response`` are ported from the team's whitebox-world-studio 1.2.0
``制作脚本/camera_energy.py`` (see references/whitebox-world-studio-1.2.0/): every entity is evaluated
at ``TimeMap.source(edit_t)``; cameras run on edit time; camera responses are applied to an unmodified
base transform and never accumulated frame to frame ("no auto-directing"). The stationary-hold
heading rule follows the same package's ``orientation_stability.py``.

Conventions: metres, seconds, degrees; Z up, +Y north; an actor with yaw 0 faces +Y.
"""

from __future__ import annotations

import bisect
import math
import random
from typing import Any


def number(x: Any) -> bool:
    return type(x) in (int, float) and math.isfinite(x)


# Physical plausibility per actor kind: speed m/s, accel/brake/lateral m/s², heading rate deg/s.
# Defaults of qc.dynamic_gate.kinds; the heading clamp below uses the same yaw rates.
DEFAULT_MOTION_LIMITS: dict[str, dict[str, float]] = {
    "pawn": {"speed": 12.0, "accel": 8.0, "brake": 9.0, "lateral": 8.0, "yaw_rate": 720.0},
    "fbx": {"speed": 12.0, "accel": 8.0, "brake": 9.0, "lateral": 8.0, "yaw_rate": 720.0},
    "robot": {"speed": 6.0, "accel": 4.0, "brake": 5.0, "lateral": 4.0, "yaw_rate": 360.0},
    "block_animal": {"speed": 20.0, "accel": 10.0, "brake": 12.0, "lateral": 10.0, "yaw_rate": 540.0},
    "block_bird": {"speed": 30.0, "accel": 15.0, "brake": 15.0, "lateral": 20.0, "yaw_rate": 540.0},
    "block_fish": {"speed": 8.0, "accel": 6.0, "brake": 6.0, "lateral": 8.0, "yaw_rate": 540.0},
    "vehicle": {"speed": 45.0, "accel": 7.0, "brake": 11.0, "lateral": 10.0, "yaw_rate": 120.0},
    "prop": {"speed": 2.0, "accel": 2.0, "brake": 2.0, "lateral": 2.0, "yaw_rate": 360.0},
}


def yaw_rate_limit(actor: dict) -> float:
    """Heading clamp for one actor: explicit ``max_yaw_rate_dps`` or its kind's default."""
    return float(actor.get("max_yaw_rate_dps") or
                 DEFAULT_MOTION_LIMITS.get(actor.get("kind", "pawn"), DEFAULT_MOTION_LIMITS["pawn"])["yaw_rate"])


# --------------------------------------------------------------------------- time map (ported)
class TimeMap:
    def __init__(self, knots: list, interpolation: str = "monotone_cubic") -> None:
        if interpolation not in ("linear", "monotone_cubic"):
            raise ValueError("Unknown time interpolation")
        if not isinstance(knots, list) or len(knots) < 2:
            raise ValueError("At least two time knots required")
        if any(not isinstance(k, (list, tuple)) or len(k) != 2 or not all(number(v) for v in k) for k in knots):
            raise ValueError("Finite edit/source pairs required")
        self.x = [float(k[0]) for k in knots]
        self.y = [float(k[1]) for k in knots]
        self.kind = interpolation
        if any(b <= a for a, b in zip(self.x, self.x[1:])) or any(b <= a for a, b in zip(self.y, self.y[1:])):
            raise ValueError("Both clocks must be strictly increasing")
        self.h = [b - a for a, b in zip(self.x, self.x[1:])]
        self.d = [(b - a) / h for a, b, h in zip(self.y, self.y[1:], self.h)]
        self.m = [self.d[0]]
        for i in range(1, len(self.x) - 1):
            w1 = 2 * self.h[i] + self.h[i - 1]
            w2 = self.h[i] + 2 * self.h[i - 1]
            self.m.append((w1 + w2) / (w1 / self.d[i - 1] + w2 / self.d[i]))
        self.m.append(self.d[-1])

    @classmethod
    def identity(cls, duration: float) -> TimeMap:
        return cls([[0.0, 0.0], [float(duration), float(duration)]], "linear")

    @classmethod
    def from_spec(cls, spec: dict | None, duration: float) -> TimeMap:
        if not spec:
            return cls.identity(duration)
        return cls([list(k) for k in spec["knots"]], spec.get("interpolation", "linear"))

    @property
    def source_end(self) -> float:
        return self.y[-1]

    def _at(self, t: float) -> tuple[int, float]:
        if not number(t) or t < self.x[0] - 1e-6 or t > self.x[-1] + 1e-6:
            raise ValueError("Time outside the authored domain")
        t = max(self.x[0], min(self.x[-1], t))
        i = min(len(self.h) - 1, max(0, bisect.bisect_right(self.x, t) - 1))
        return i, (t - self.x[i]) / self.h[i]

    def source(self, t: float) -> float:
        i, u = self._at(t)
        if self.kind == "linear":
            return self.y[i] + u * (self.y[i + 1] - self.y[i])
        return ((2 * u ** 3 - 3 * u * u + 1) * self.y[i] + (u ** 3 - 2 * u * u + u) * self.h[i] * self.m[i]
                + (-2 * u ** 3 + 3 * u * u) * self.y[i + 1] + (u ** 3 - u * u) * self.h[i] * self.m[i + 1])

    def rate(self, t: float) -> float:
        i, u = self._at(t)
        if self.kind == "linear":
            return self.d[i]
        return ((6 * u * u - 6 * u) * self.y[i] / self.h[i] + (3 * u * u - 4 * u + 1) * self.m[i]
                + (-6 * u * u + 6 * u) * self.y[i + 1] / self.h[i] + (3 * u * u - 2 * u) * self.m[i + 1])

    def edit(self, source_t: float) -> float:
        if not number(source_t) or not self.y[0] <= source_t <= self.y[-1]:
            raise ValueError("Source event outside the time map")
        a, b = self.x[0], self.x[-1]
        for _ in range(56):
            c = (a + b) / 2
            if self.source(c) < source_t:
                a = c
            else:
                b = c
        return (a + b) / 2


def smooth(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def sample_response(edit_t: float, source_t: float, layer: dict, fps: float = 24, source_rate: float = 1):
    """Local camera offset (XYZ metres, XYZ Euler degrees) for one response layer (ported)."""
    clock = layer.get("clock", "edit")
    if clock not in ("edit", "source"):
        raise ValueError("Response clock must be edit or source")
    if not number(edit_t) or not number(source_t):
        raise ValueError("Finite edit/source times required")
    start, end = layer.get("start"), layer.get("end")
    if not number(start) or not number(end) or not start < end:
        raise ValueError("Valid edit response window required")
    interval = layer.get("source_interval") if clock == "source" else [layer["start"], layer["end"]]
    if not interval or len(interval) != 2 or not all(number(x) for x in interval) or interval[1] <= interval[0]:
        raise ValueError("Valid signal interval required")
    a, b = interval
    t = source_t if clock == "source" else edit_t
    xyz = list(layer.get("translation_m", [0, 0, 0]))
    rot = list(layer.get("rotation_deg", [0, 0, 0]))
    band = layer.get("frequency_hz", [3, 7])
    if len(xyz) != 3 or len(rot) != 3 or not all(number(v) for v in xyz + rot):
        raise ValueError("Independent finite axis amplitudes required")
    if len(band) != 2 or not all(number(v) for v in band) or not 0 <= band[0] <= band[1]:
        raise ValueError("Valid frequency band required")
    if not number(fps) or fps <= 0 or not number(source_rate) or source_rate <= 0:
        raise ValueError("Positive sampling rate required")
    if band[1] * (source_rate if clock == "source" else 1) >= fps * 0.45:
        raise ValueError("Response frequency aliases at this edit FPS/rate")
    if type(layer.get("seed")) is not int:
        raise ValueError("A reproducible integer seed is required")
    if edit_t <= start or edit_t >= end or t <= a or t >= b:
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
    attack = layer.get("attack_seconds", (b - a) * 0.15)
    decay = layer.get("decay_seconds", (b - a) * 0.7)
    if not all(number(v) and v > 0 for v in [attack, decay]):
        raise ValueError("Positive envelope times required")
    env = smooth((t - a) / attack) * smooth((b - t) / decay)
    out = []
    for axis, amp in enumerate(xyz + rot):
        rng = random.Random(f'{layer["seed"]}:axis:{axis}')
        signal = 0.0
        weight = 0.0
        for n in range(4):
            frequency = rng.uniform(*band)
            phase = rng.uniform(0, 2 * math.pi)
            w = 1 / (n + 1)
            signal += w * math.sin(2 * math.pi * frequency * t + phase)
            weight += w
        out.append(amp * env * signal / weight)
    return tuple(out[:3]), tuple(out[3:])


# --------------------------------------------------------------------------- timed tracks
def _monotone_slopes(t: list[float], y: list[float]) -> list[float]:
    h = [b - a for a, b in zip(t, t[1:])]
    d = [(b - a) / w for a, b, w in zip(y, y[1:], h)]
    m = [d[0]]
    for i in range(1, len(t) - 1):
        if d[i - 1] * d[i] <= 0:
            m.append(0.0)
        else:
            w1, w2 = 2 * h[i] + h[i - 1], h[i] + 2 * h[i - 1]
            m.append((w1 + w2) / (w1 / d[i - 1] + w2 / d[i]))
    m.append(d[-1])
    return m


def _smooth_slopes(t: list[float], y: list[float]) -> list[float]:
    """Three-point slopes (exact for constant acceleration) with the Fritsch–Carlson monotonicity limiter."""
    h = [b - a for a, b in zip(t, t[1:])]
    d = [(b - a) / w for a, b, w in zip(y, y[1:], h)]
    n = len(t)
    m = [0.0] * n
    for i in range(1, n - 1):
        m[i] = 0.0 if d[i - 1] * d[i] <= 0 else (h[i] * d[i - 1] + h[i - 1] * d[i]) / (h[i - 1] + h[i])

    def end(h0: float, h1: float, d0: float, d1: float) -> float:
        v = ((2 * h0 + h1) * d0 - h0 * d1) / (h0 + h1)
        if v * d0 <= 0:
            return 0.0
        return 3 * d0 if d0 * d1 <= 0 and abs(v) > abs(3 * d0) else v

    m[0], m[-1] = end(h[0], h[1], d[0], d[1]), end(h[-1], h[-2], d[-1], d[-2])
    for k in range(n - 1):
        if d[k] == 0:
            m[k] = m[k + 1] = 0.0
            continue
        a, b = m[k] / d[k], m[k + 1] / d[k]
        if a * a + b * b > 9:
            tau = 3 / math.sqrt(a * a + b * b)
            m[k], m[k + 1] = tau * a * d[k], tau * b * d[k]
    return m


class Track:
    """Timed 3D path from keys ``[[t, x, y, z], ...]``.

    ``cubic`` interpolates each axis with a monotone cubic Hermite (no overshoot between keys; two keys
    with the same position hold still); ``smooth`` is also monotone but uses three-point slopes, which
    reproduce constant acceleration exactly (dense generated paths); ``linear`` is piecewise linear.
    Outside the key range the track holds its first/last position.
    """

    def __init__(self, keys: list, interpolation: str = "cubic") -> None:
        if interpolation not in ("cubic", "smooth", "linear"):
            raise ValueError(f"unknown interpolation {interpolation}")
        if not keys:
            raise ValueError("track needs at least one key")
        rows = sorted((float(k[0]), float(k[1]), float(k[2]), float(k[3])) for k in keys)
        for a, b in zip(rows, rows[1:]):
            if b[0] <= a[0]:
                raise ValueError(f"track key times must strictly increase (t={b[0]})")
        self.t = [r[0] for r in rows]
        self.axes = [[r[i] for r in rows] for i in (1, 2, 3)]
        self.kind = interpolation if len(rows) >= 3 else "linear"
        slopes = {"cubic": _monotone_slopes, "smooth": _smooth_slopes}.get(self.kind)
        self.m = [slopes(self.t, ax) for ax in self.axes] if slopes else None

    @property
    def start(self) -> float:
        return self.t[0]

    @property
    def end(self) -> float:
        return self.t[-1]

    def at(self, t: float) -> tuple[float, float, float]:
        if len(self.t) == 1:
            return (self.axes[0][0], self.axes[1][0], self.axes[2][0])
        t = min(max(t, self.t[0]), self.t[-1])
        i = min(max(bisect.bisect_right(self.t, t) - 1, 0), len(self.t) - 2)
        h = self.t[i + 1] - self.t[i]
        u = (t - self.t[i]) / h
        if self.kind == "linear":
            return tuple(ax[i] + u * (ax[i + 1] - ax[i]) for ax in self.axes)  # type: ignore[return-value]
        h00, h10 = 2 * u ** 3 - 3 * u * u + 1, u ** 3 - 2 * u * u + u
        h01, h11 = -2 * u ** 3 + 3 * u * u, u ** 3 - u * u
        return tuple(h00 * ax[i] + h10 * h * m[i] + h01 * ax[i + 1] + h11 * h * m[i + 1]  # type: ignore[return-value]
                     for ax, m in zip(self.axes, self.m))

    def velocity(self, t: float, dt: float = 1 / 96) -> tuple[float, float, float]:
        a, b = max(t - dt, self.t[0]), min(t + dt, self.t[-1])
        if b - a < 1e-9:
            return (0.0, 0.0, 0.0)
        p, q = self.at(a), self.at(b)
        return tuple((qq - pp) / (b - a) for pp, qq in zip(p, q))  # type: ignore[return-value]


class ScalarTrack:
    """Piecewise-linear scalar keys ``[[t, value], ...]`` (roll, yaw overrides)."""

    def __init__(self, keys: list) -> None:
        rows = sorted((float(k[0]), float(k[1])) for k in keys)
        self.t = [r[0] for r in rows]
        self.v = [r[1] for r in rows]

    def at(self, t: float) -> float:
        if not self.t:
            return 0.0
        if t <= self.t[0]:
            return self.v[0]
        if t >= self.t[-1]:
            return self.v[-1]
        i = bisect.bisect_right(self.t, t) - 1
        u = (t - self.t[i]) / (self.t[i + 1] - self.t[i])
        return self.v[i] + u * (self.v[i + 1] - self.v[i])


def heading_deg(vx: float, vy: float) -> float:
    """Yaw that turns local +Y onto the (vx, vy) direction."""
    return math.degrees(math.atan2(-vx, vy))


def headings(track: Track, times: list[float], min_speed: float = 0.15, max_rate_dps: float = 720.0,
             initial: float | None = None) -> list[float]:
    """Continuous yaw per time: follow the motion direction, hold while (nearly) stationary."""
    if initial is None:
        initial = 0.0
        for t in times:
            vx, vy, _ = track.velocity(t)
            if math.hypot(vx, vy) > min_speed:
                initial = heading_deg(vx, vy)
                break
    out: list[float] = []
    prev, last_t = initial, times[0] if times else 0.0
    for t in times:
        vx, vy, _ = track.velocity(t)
        target = heading_deg(vx, vy) if math.hypot(vx, vy) > min_speed else prev
        target = prev + ((target - prev + 180.0) % 360.0 - 180.0)
        if out:
            step = max_rate_dps * max(t - last_t, 1e-6)
            prev = prev + max(-step, min(step, target - prev))
        else:
            prev = target
        out.append(prev)
        last_t = t
    return out


# --------------------------------------------------------------------------- geometry helpers
def block_aabb(block: dict) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    cx, cy, cz = block["center"]
    sx, sy, sz = block["size"]
    yaw = math.radians((block.get("rotation_deg") or [0, 0, 0])[2])
    c, s = abs(math.cos(yaw)), abs(math.sin(yaw))
    hx, hy = (sx * c + sy * s) / 2, (sx * s + sy * c) / 2
    return (cx - hx, cy - hy, cz - sz / 2), (cx + hx, cy + hy, cz + sz / 2)


def point_aabb_distance(p: tuple[float, float, float], lo: tuple, hi: tuple) -> float:
    """Euclidean distance to the box; negative (depth) when the point is inside."""
    outside = [max(lo[i] - p[i], 0.0, p[i] - hi[i]) for i in range(3)]
    if any(outside):
        return math.sqrt(sum(v * v for v in outside))
    return -min(min(p[i] - lo[i], hi[i] - p[i]) for i in range(3))


def cylinder_box_penetration(center: tuple[float, float, float], radius: float, height: float,
                             lo: tuple, hi: tuple, support_tol: float = 0.05) -> float:
    """Horizontal penetration depth (m) of an upright actor cylinder into a box; 0 when clear.

    Standing on top of a box (feet within ``support_tol`` of its top) is support, not penetration.
    """
    bottom, top = center[2], center[2] + height
    if top <= lo[2] or bottom >= hi[2] - support_tol:
        return 0.0
    nx = min(max(center[0], lo[0]), hi[0])
    ny = min(max(center[1], lo[1]), hi[1])
    dist = math.hypot(center[0] - nx, center[1] - ny)
    if dist > 0:
        return max(radius - dist, 0.0)
    inner = min(center[0] - lo[0], hi[0] - center[0], center[1] - lo[1], hi[1] - center[1])
    return radius + inner


# --------------------------------------------------------------------------- scene evaluation
class SceneEvaluator:
    """Evaluate actors and the active camera of a scene.json at any edit time."""

    def __init__(self, scene: dict, yaw_rate_hz: int = 96) -> None:
        self.scene = scene
        self.duration = float(scene["duration_s"])
        self.fps = int(scene["fps"])
        self.frames = int(round(self.duration * self.fps))
        self.time_map = TimeMap.from_spec(scene.get("time_map"), self.duration)
        self.actors = {a["id"]: a for a in scene.get("actors", [])}
        self.tracks = {aid: Track(a["path"]["keys"], a["path"].get("interpolation", "cubic"))
                       for aid, a in self.actors.items()}
        self._yaw: dict[str, tuple[list[float], list[float]]] = {}
        end = self.time_map.source_end
        n = max(2, int(end * yaw_rate_hz) + 1)
        grid = [end * i / (n - 1) for i in range(n)]
        for aid, actor in self.actors.items():
            if actor.get("yaw_keys"):
                track = ScalarTrack(actor["yaw_keys"])
                self._yaw[aid] = (grid, [track.at(t) for t in grid])
            else:
                self._yaw[aid] = (grid, headings(self.tracks[aid], grid, max_rate_dps=yaw_rate_limit(actor)))
        self.shots = scene["shots"]
        self._cams = [Track(s["camera"]["keys"], s["camera"].get("interpolation", "cubic")) for s in self.shots]
        self._aims = [Track(s["camera"]["aim_keys"], "cubic") if s["camera"].get("aim_keys") else None
                      for s in self.shots]
        self._rolls = [ScalarTrack(s["camera"].get("roll_keys") or []) for s in self.shots]

    def frame_time(self, frame: int) -> float:
        return (frame - 1) / self.fps

    def source_time(self, t_edit: float) -> float:
        return self.time_map.source(float(min(max(t_edit, 0.0), self.duration)))

    def actor_state(self, aid: str, t_edit: float) -> tuple[tuple[float, float, float], float]:
        ts = self.source_time(t_edit)
        grid, values = self._yaw[aid]
        if ts <= grid[0]:
            yaw = values[0]
        elif ts >= grid[-1]:
            yaw = values[-1]
        else:
            i = bisect.bisect_right(grid, ts) - 1
            u = (ts - grid[i]) / (grid[i + 1] - grid[i])
            yaw = values[i] + u * (values[i + 1] - values[i])
        return self.tracks[aid].at(ts), yaw

    def shot_index(self, t_edit: float) -> int:
        """Shot owning the frame whose display interval contains ``t_edit`` (frame-based, like the build)."""
        frame = int(math.floor(float(t_edit) * self.fps + 1e-6)) + 1
        for i in range(len(self.shots)):
            first, last = self.shot_frames(i)
            if first <= frame <= last:
                return i
        return len(self.shots) - 1 if frame > 1 else 0

    def shot_frames(self, index: int) -> tuple[int, int]:
        shot = self.shots[index]
        first = int(round(shot["start_s"] * self.fps)) + 1
        last = int(round(shot["end_s"] * self.fps))
        return first, max(first, last)

    def camera_state(self, t_edit: float, index: int | None = None) -> dict[str, Any]:
        t_edit = float(t_edit)
        i = self.shot_index(t_edit) if index is None else int(index)
        shot, cam = self.shots[i], self.shots[i]["camera"]
        pos = self._cams[i].at(t_edit)
        if self._aims[i] is not None:
            aim = self._aims[i].at(t_edit)
        else:
            actor_pos, _ = self.actor_state(cam["aim_actor"], t_edit)
            off = cam.get("aim_offset") or [0.0, 0.0, 1.2]
            aim = (actor_pos[0] + off[0], actor_pos[1] + off[1], actor_pos[2] + off[2])
        translation, rotation = [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
        ts = self.source_time(t_edit)
        for layer in cam.get("responses") or []:
            tr, rot = sample_response(t_edit, ts, layer, fps=self.fps, source_rate=self.time_map.rate(t_edit))
            translation = [a + b for a, b in zip(translation, tr)]
            rotation = [a + b for a, b in zip(rotation, rot)]
        return {"shot_id": shot["id"], "index": i, "pos": pos, "aim": aim, "roll_deg": self._rolls[i].at(t_edit),
                "lens_mm": float(shot.get("lens_mm", 32.0)), "shake_translation": translation,
                "shake_rotation_deg": rotation}
