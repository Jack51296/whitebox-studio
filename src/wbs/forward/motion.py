"""Physically plausible actor paths: route waypoints → dense path keys within per-kind motion limits.

Corners get circular fillets sized for the planned speed (r ≥ v² / lateral limit, clipped to half the
adjacent segments); the speed profile is capped on curves by the lateral and heading-rate limits and
shaped by forward/backward passes with the acceleration and braking limits. Timed waypoints solve for
the cruise speed that meets the arrival time; a leg that cannot be driven within the limits raises
``RouteInfeasible`` with the numbers needed to fix it (the story chain feeds this back to the planner).
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from typing import Any, Protocol

MARGIN = 0.9  # plan below the gate limits so interpolation and smoothing stay inside them
KEY_STEP_S = 0.1


class Limits(Protocol):
    speed: float
    accel: float
    brake: float
    lateral: float
    yaw_rate: float


class RouteInfeasible(ValueError):
    """A route leg cannot meet its timing within the actor's motion limits."""


@dataclass
class Waypoint:
    x: float
    y: float
    z: float = 0.0
    at_s: float | None = None
    stop_s: float = 0.0
    speed_mps: float | None = None


@dataclass
class _Path:
    s: list[float]
    xyz: list[tuple[float, float, float]]
    curvature: list[float]
    anchor_s: list[float]


def _unit(dx: float, dy: float) -> tuple[float, float]:
    n = math.hypot(dx, dy)
    return (dx / n, dy / n) if n > 1e-9 else (0.0, 1.0)


def _build_path(wps: list[Waypoint], radii: list[float], step: float = 0.25) -> _Path:
    """Dense samples through the waypoints with filleted corners; ``radii[i]`` is the wanted corner radius."""
    pts = [(w.x, w.y, w.z) for w in wps]
    n = len(pts)
    seg_len = [math.dist(pts[i][:2], pts[i + 1][:2]) for i in range(n - 1)]
    tangent = [0.0] * n
    radius = [0.0] * n
    turn = [0.0] * n
    for i in range(1, n - 1):
        if wps[i].stop_s > 0 or seg_len[i - 1] < 1e-6 or seg_len[i] < 1e-6:
            continue
        u1 = _unit(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
        u2 = _unit(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
        cross, dot = u1[0] * u2[1] - u1[1] * u2[0], u1[0] * u2[0] + u1[1] * u2[1]
        theta = math.atan2(abs(cross), dot)
        if theta < 1e-3:
            continue
        d = min(radii[i] * math.tan(theta / 2), 0.5 * seg_len[i - 1], 0.5 * seg_len[i])
        tangent[i], turn[i] = d, math.copysign(theta, cross)
        radius[i] = d / math.tan(theta / 2)

    s_out, xyz, curv, anchor = [0.0], [pts[0]], [0.0], [0.0] * n

    def add(p: tuple[float, float, float], k: float) -> None:
        s_out.append(s_out[-1] + math.dist(p, xyz[-1]))
        xyz.append(p)
        curv.append(k)

    for i in range(n - 1):
        a, b = pts[i], pts[i + 1]
        u = _unit(b[0] - a[0], b[1] - a[1])
        start = (a[0] + u[0] * tangent[i], a[1] + u[1] * tangent[i])
        end = (b[0] - u[0] * tangent[i + 1], b[1] - u[1] * tangent[i + 1])
        length = math.dist(start, end)

        def z_at(x: float, y: float, a=a, b=b, i=i) -> float:
            f = math.dist((x, y), a[:2]) / seg_len[i] if seg_len[i] > 1e-9 else 0.0
            return a[2] + (b[2] - a[2]) * min(max(f, 0.0), 1.0)

        steps = max(4, int(math.ceil(length / step)))
        for k in range(1, steps + 1):
            x, y = start[0] + (end[0] - start[0]) * k / steps, start[1] + (end[1] - start[1]) * k / steps
            add((x, y, z_at(x, y)), 0.0)
        j = i + 1
        if j < n - 1 and tangent[j] > 0:
            r, th = radius[j], turn[j]
            normal = (-u[1], u[0]) if th > 0 else (u[1], -u[0])
            cx, cy = end[0] + normal[0] * r, end[1] + normal[1] * r
            vx, vy = end[0] - cx, end[1] - cy
            arc_steps = max(4, int(math.ceil(r * abs(th) / step)))
            z0 = z_at(*end)
            z1 = pts[j][2] + (pts[j + 1][2] - pts[j][2]) * tangent[j] / seg_len[j]
            for k in range(1, arc_steps + 1):
                phi = th * k / arc_steps
                c, s = math.cos(phi), math.sin(phi)
                x, y = cx + vx * c - vy * s, cy + vx * s + vy * c
                add((x, y, z0 + (z1 - z0) * k / arc_steps), 1.0 / r)
                if k == arc_steps // 2:
                    anchor[j] = s_out[-1]
        else:
            anchor[j] = s_out[-1]
    return _Path(s_out, xyz, curv, anchor)


def _caps(path: _Path, lim: Limits) -> list[float]:
    omega = math.radians(lim.yaw_rate) * MARGIN
    out = []
    for k in path.curvature:
        cap = lim.speed * MARGIN
        if k > 1e-9:
            r = 1.0 / k
            cap = min(cap, math.sqrt(lim.lateral * MARGIN * r), omega * r)
        out.append(cap)
    return out


def _profile(s: list[float], cap: list[float], target: list[float], v0: float | None, v1: float | None,
             lim: Limits) -> list[float]:
    acc, brk = lim.accel * MARGIN, lim.brake * MARGIN
    v = [min(c, t) for c, t in zip(cap, target)]
    if v0 is not None:
        v[0] = min(v[0], v0) if v0 > 0 else 0.0
    for i in range(1, len(v)):
        v[i] = min(v[i], math.sqrt(v[i - 1] ** 2 + 2 * acc * (s[i] - s[i - 1])))
    if v1 is not None:
        v[-1] = min(v[-1], v1)
    for i in range(len(v) - 2, -1, -1):
        v[i] = min(v[i], math.sqrt(v[i + 1] ** 2 + 2 * brk * (s[i + 1] - s[i])))
    return v


def _leg_time(s: list[float], v: list[float], lim: Limits) -> list[float]:
    """Trapezoidal travel time; a stretch that starts and ends at rest is timed as speed-up then brake."""
    a = min(lim.accel, lim.brake) * MARGIN
    t = [0.0]
    for i in range(1, len(s)):
        ds, avg = s[i] - s[i - 1], (v[i] + v[i - 1]) / 2
        t.append(t[-1] + (ds / avg if avg > 1e-6 else 2 * math.sqrt(max(ds, 0.0) / a)))
    return t


def _leg_duration(k: float, s: list[float], cap: list[float], target: list[float], v0: float | None,
                  v1: float | None, lim: Limits) -> float:
    """Travel time of one leg when every target speed is scaled by ``k``."""
    return _leg_time(s, _profile(s, cap, [t * k for t in target], v0, v1, lim), lim)[-1]


def compile_route(waypoints: list[Waypoint], lim: Limits, end_s: float, start_s: float = 0.0,
                  default_speed: float | None = None, corner_radius: float | None = None,
                  start_moving: bool = False, label: str = "route") -> list[list[float]]:
    """Keys ``[t, x, y, z]`` covering 0..end_s; before ``start_s`` and after the last waypoint the actor holds."""
    wps: list[Waypoint] = []
    for w in waypoints:
        if wps and math.dist((w.x, w.y, w.z), (wps[-1].x, wps[-1].y, wps[-1].z)) <= 1e-6:
            prev = wps[-1]
            wps[-1] = Waypoint(prev.x, prev.y, prev.z, prev.at_s if prev.at_s is not None else w.at_s,
                               max(prev.stop_s, w.stop_s), prev.speed_mps or w.speed_mps)
        else:
            wps.append(Waypoint(w.x, w.y, w.z, w.at_s, w.stop_s, w.speed_mps))
    if len(wps) < 2:
        p = wps[0]
        return [[0.0, p.x, p.y, p.z], [end_s, p.x, p.y, p.z]]
    default = default_speed or lim.speed * 0.5
    anchors = [i for i, w in enumerate(wps) if i in (0, len(wps) - 1) or w.at_s is not None or w.stop_s > 0]
    # corner radius follows the speed expected around each vertex
    rough = _build_path(wps, [0.0] * len(wps))
    expected = []
    for i, w in enumerate(wps):
        speed = w.speed_mps or (wps[i + 1].speed_mps if i + 1 < len(wps) else None)
        if speed is None:
            a = max(x for x in anchors if x <= i) if i else 0
            b = min((x for x in anchors if x > i), default=len(wps) - 1)
            if wps[b].at_s is not None:
                t_a = (wps[a].at_s if wps[a].at_s is not None else start_s) + wps[a].stop_s
                span = wps[b].at_s - t_a
                speed = (rough.anchor_s[b] - rough.anchor_s[a]) / span if span > 1e-6 else default
            else:
                speed = default
        expected.append(min(speed, lim.speed * MARGIN))
    radii = [max(corner_radius or 0.0, v * v / (lim.lateral * MARGIN)) for v in expected]
    path = _build_path(wps, radii)
    cap = _caps(path, lim)
    seg_of = [bisect.bisect_right(path.anchor_s, s_) for s_ in path.s]
    target_speed = [wps[min(k, len(wps) - 1)].speed_mps or default for k in seg_of]

    legs = list(zip(anchors, anchors[1:]))
    factor = [1.0] * len(legs)
    bounds: list[float | None] = [None] * len(anchors)
    bounds[0] = None if start_moving else 0.0
    results: list[tuple[list[float], list[float], list[float]]] = []
    for _ in range(8):
        results = []
        clock = start_s + wps[0].stop_s
        for n_leg, (a, b) in enumerate(legs):
            lo_i = bisect.bisect_left(path.s, path.anchor_s[a] - 1e-9)
            hi_i = bisect.bisect_right(path.s, path.anchor_s[b] + 1e-9)
            s_leg = path.s[lo_i:hi_i]
            cap_leg, tgt_leg = cap[lo_i:hi_i], target_speed[lo_i:hi_i]
            v_start = bounds[n_leg]
            last = b == len(wps) - 1
            v_end = 0.0 if wps[b].stop_s > 0 or last and (wps[b].at_s is None or wps[b].at_s < end_s - 1e-3) \
                else bounds[n_leg + 1]
            if wps[b].at_s is not None:
                need = wps[b].at_s - clock
                if need <= 1e-6:
                    raise RouteInfeasible(f"{label}: waypoint {b} at {wps[b].at_s:.2f}s is not after the previous "
                                          f"departure at {clock:.2f}s")

                leg = (s_leg, cap_leg, tgt_leg, v_start, v_end, lim)
                fastest = _leg_duration(1e3, *leg)
                if fastest > need + 0.05:
                    dist = s_leg[-1] - s_leg[0]
                    raise RouteInfeasible(
                        f"{label}: leg {a}→{b} is {dist:.1f} m in {need:.2f}s (average {dist / need:.1f} m/s); the fastest "
                        f"within limits (speed {lim.speed:g} m/s, accel {lim.accel:g}, brake {lim.brake:g}, lateral "
                        f"{lim.lateral:g} m/s²) takes {fastest:.2f}s — move the waypoint later, shorten the leg or widen "
                        f"the corners")
                lo, hi = 1e-3, 1e3
                if _leg_duration(lo, *leg) < need:
                    raise RouteInfeasible(f"{label}: leg {a}→{b} would have to crawl to fill {need:.2f}s; add stop_s at "
                                          f"waypoint {a} instead")
                for _ in range(60):
                    mid = math.sqrt(lo * hi)
                    lo, hi = (mid, hi) if _leg_duration(mid, *leg) > need else (lo, mid)
                factor[n_leg] = hi
            v = _profile(s_leg, cap_leg, [t * factor[n_leg] for t in tgt_leg], v_start, v_end, lim)
            t = _leg_time(s_leg, v, lim)
            results.append((s_leg, v, [clock + x for x in t]))
            clock = results[-1][2][-1] + wps[b].stop_s
        new_bounds: list[float | None] = [bounds[0]]
        for k in range(1, len(anchors)):
            if wps[anchors[k]].stop_s > 0:
                new_bounds.append(0.0)
            elif k < len(anchors) - 1:
                new_bounds.append(min(results[k - 1][1][-1], results[k][1][0]))
            else:
                new_bounds.append(None)
        converged = all((p is None and q is None) or (p is not None and q is not None and abs(p - q) < 0.02)
                        for p, q in zip(new_bounds, bounds))
        bounds = new_bounds
        if converged:
            break

    times, dist, vel = [0.0], [0.0], [0.0 if not start_moving else (results[0][1][0] if results else 0.0)]
    if start_s + wps[0].stop_s > 0:
        times.append(start_s + wps[0].stop_s)
        dist.append(0.0)
        vel.append(vel[-1])
    for (s_leg, v_leg, t_leg), (_, b) in zip(results, legs):
        for s_, v_, t_ in zip(s_leg, v_leg, t_leg):
            if t_ > times[-1] + 1e-9:
                times.append(t_)
                dist.append(s_)
                vel.append(v_)
        if wps[b].stop_s > 0:
            times.append(times[-1] + wps[b].stop_s)
            dist.append(dist[-1])
            vel.append(0.0)
    if times[-1] < end_s - 1e-6:
        times.append(end_s)
        dist.append(dist[-1])
        vel.append(0.0)

    def travelled(t: float) -> float:
        """Arc length at time t, constant acceleration inside each profile interval."""
        j = min(max(bisect.bisect_right(times, t) - 1, 0), len(times) - 2)
        h = times[j + 1] - times[j]
        tau = min(max(t - times[j], 0.0), h)
        a = (vel[j + 1] - vel[j]) / h if h > 1e-9 else 0.0
        return min(max(dist[j] + vel[j] * tau + 0.5 * a * tau * tau, min(dist[j], dist[j + 1])),
                   max(dist[j], dist[j + 1]))

    def position(s_: float) -> tuple[float, float, float]:
        i = min(max(bisect.bisect_right(path.s, s_) - 1, 0), len(path.s) - 2)
        f = (s_ - path.s[i]) / max(path.s[i + 1] - path.s[i], 1e-9)
        return tuple(p + (q - p) * min(max(f, 0.0), 1.0) for p, q in zip(path.xyz[i], path.xyz[i + 1]))  # type: ignore

    holds = [i for i in range(1, len(times)) if abs(dist[i] - dist[i - 1]) < 1e-9]
    edges = sorted({0.0, end_s} | {min(times[i], end_s) for i in holds} | {min(times[i - 1], end_s) for i in holds})
    sample_t: list[float] = []
    for a, b in zip(edges, edges[1:]):
        if b - a < 1e-9:
            continue
        if abs(travelled(b) - travelled(a)) < 1e-9:
            sample_t += [a, b]
            continue
        steps = max(1, int(math.ceil((b - a) / KEY_STEP_S - 0.4)))
        sample_t += [a + (b - a) * k / steps for k in range(steps + 1)]
    keys: list[list[float]] = []
    for t in sorted(set(round(t, 6) for t in sample_t)):
        x, y, z = position(travelled(t))
        if keys and t - keys[-1][0] < 1e-4:
            continue
        keys.append([round(t, 4), round(x, 3), round(y, 3), round(z, 3)])
    return keys


def route_keys(route: Any, lim: Limits, end_s: float, label: str = "route") -> list[list[float]]:
    """Compile a RouteSpec (model or dict) into keys."""
    get = route.get if isinstance(route, dict) else (lambda k, d=None: getattr(route, k, d))
    return compile_route(waypoints_from(route), lim, end_s, start_s=float(get("start_s", 0.0) or 0.0),
                         corner_radius=get("corner_radius_m"), start_moving=bool(get("start_moving", False)),
                         label=label)


def interpolation_for(keys: list) -> str:
    """Dense generated keys use ``smooth`` (exact for constant acceleration); sparse authored keys keep ``cubic``."""
    if len(keys) < 20:
        return "cubic"
    gaps = sorted(b[0] - a[0] for a, b in zip(keys, keys[1:]))
    return "smooth" if gaps[len(gaps) // 2] <= 0.2 else "cubic"


def waypoints_from(spec: Any) -> list[Waypoint]:
    """Waypoints from a RouteSpec-like object or dict."""
    items = spec["waypoints"] if isinstance(spec, dict) else spec.waypoints
    out = []
    for w in items:
        d = w if isinstance(w, dict) else w.model_dump()
        out.append(Waypoint(float(d["x"]), float(d["y"]), float(d.get("z", 0.0)), d.get("at_s"),
                            float(d.get("stop_s", 0.0)), d.get("speed_mps")))
    return out


KIND_LABELS = {"pawn": "人物", "vehicle": "车辆", "robot": "机器人", "block_animal": "动物", "block_bird": "鸟",
               "block_fish": "鱼", "prop": "道具"}


def limits_text(kinds: dict[str, Limits]) -> str:
    """Per-kind limits as planner prompt lines (the same numbers the dynamic gate enforces)."""
    return "\n".join(f"- {kind}（{name}）：最高 {lim.speed:g} m/s，加速 ≤ {lim.accel:g} m/s²，制动 ≤ {lim.brake:g} m/s²，"
                     f"转弯横向 ≤ {lim.lateral:g} m/s²（半径 r 的弯道最高约 √({lim.lateral:g}·r) m/s），"
                     f"转向 ≤ {lim.yaw_rate:g}°/s"
                     for kind, name in KIND_LABELS.items() if (lim := kinds.get(kind)) is not None)
