"""Hierarchical story planning chain: premise → characters → events → space → shots.

Structure follows Dramatron's hierarchical generation (google-deepmind/dramatron, Apache-2.0): each
layer is its own model call with its own contract, later layers see earlier results, and a layer can be
re-run alone. That makes the [D5] review loops concrete: story/rhythm notes re-run from ``events``,
scheduling/picture notes re-run ``shots`` only. A critique → revise loop (FilmAgent's
Critique-Correct-Verify) runs when a real model is configured; it is skipped for mock and dry-run.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import statistics
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..config import Spec, load_settings
from ..jsonio import extract_json
from ..layout import JobPaths
from ..models.scene import Block, Event, Key4, RouteBeat, TimeMapSpec
from ..models.story import (
    RouteSpec,
    StoryCharacter,
    StoryPlan,
    StoryProp,
    StoryShot,
    StorySpace,
    check_event_chain,
)
from ..prompts import render
from ..providers import CallContext, Providers
from ..providers.mock import register_mock
from ..qc.dynamic import MOTION_TYPES
from ..qc.pregate import pregate
from ..taxonomy import ControlSpec
from .motion import RouteInfeasible, route_keys
from .procedural import IDENTITY_COLORS, generate
from .story import STORY_MOVES, mock_beats, mock_events, planning_guides, story_to_scene
from .story_examples import pick_examples

LAYERS = ("premise", "characters", "events", "space", "shots")
SYSTEM = "你是白模预演的编剧兼导演。只输出一个合法 JSON 对象，不输出解释、不输出代码块标记。"


class _Layer(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PremiseLayer(_Layer):
    title: str = Field(min_length=1)
    logline: str = Field(min_length=1)
    world: str = Field(min_length=1)
    lighting: str = ""
    goal: str = Field(min_length=1)
    obstacle: str = Field(min_length=1)
    stakes: str = Field(min_length=1)
    ending: str = Field(min_length=1)


class CharactersLayer(_Layer):
    characters: list[StoryCharacter] = Field(min_length=1, max_length=6)


class EventsLayer(_Layer):
    story: str = Field(min_length=1)
    events: list[Event] = Field(min_length=3)
    twist: int
    setup: list[int] = Field(min_length=1)
    semantic_only: str = ""
    props: list[StoryProp] = Field(default_factory=list)


class SpaceLayer(_Layer):
    spaces: list[StorySpace] = Field(default_factory=list)
    blocks: list[Block] = Field(default_factory=list)
    paths: dict[str, list[Key4]] = Field(default_factory=dict)
    routes: dict[str, RouteSpec] = Field(default_factory=dict)
    beats: list[RouteBeat] = Field(default_factory=list)


class ShotsLayer(_Layer):
    shots: list[StoryShot] = Field(min_length=1)
    time_map: TimeMapSpec | None = None
    notes: str = ""


MODELS: dict[str, type[_Layer]] = {"premise": PremiseLayer, "characters": CharactersLayer, "events": EventsLayer,
                                   "space": SpaceLayer, "shots": ShotsLayer}
USES_EXAMPLES = {"premise", "events"}


def layer_problems(layer: str, data: _Layer, state: dict[str, dict], duration: float) -> list[str]:
    """Checks a layer contract cannot express (cross-layer references, timing)."""
    problems: list[str] = []
    if layer == "characters":
        ids = [c.id for c in data.characters]
        if len(set(ids)) != len(ids):
            problems.append("人物 id 重复")
        colors = [c.color.lower() for c in data.characters]
        if len(set(colors)) != len(colors):
            problems.append("人物身份色重复")
    elif layer == "events":
        problems += check_event_chain(data.events, data.twist, data.setup, duration)
    elif layer == "space":
        kinds = {c["id"]: c.get("kind", "pawn") for c in state["characters"]["characters"]}
        missing = [i for i in kinds if i not in data.paths and i not in data.routes]
        if missing:
            problems.append(f"缺少人物路线：{missing}")
        both = sorted(set(data.paths) & set(data.routes))
        if both:
            problems.append(f"{both} 同时写了 paths 和 routes，每个人物只能二选一")
        built = set(kinds) | {b.id for b in data.blocks} | {b.group for b in data.blocks if b.group}
        for e in state.get("events", {}).get("events", []):
            unknown = [t for t in e.get("targets", []) if t not in built]
            if unknown:
                problems.append(f"事件 {e['id']} 的铺垫目标 {unknown} 没有建出来（blocks 或人物 id 里找不到）")
        gate = load_settings().qc.dynamic_gate
        for aid, route in data.routes.items():
            try:
                route_keys(route, gate.limits(kinds.get(aid, "pawn")), duration, label=aid)
            except RouteInfeasible as exc:
                problems.append(f"路线做不到：{exc}")
        for aid, keys in data.paths.items():
            times = [k[0] for k in keys]
            if any(b <= a for a, b in zip(times, times[1:])):
                problems.append(f"{aid} 的路线关键帧时间必须严格递增")
            if times and (times[0] > 0.05 or times[-1] < duration - 0.5):
                problems.append(f"{aid} 的路线需覆盖 0–{duration:g} 秒")
        for i, b in enumerate(data.beats):
            if not 0 <= b.start_s < b.end_s <= duration + 1e-6:
                problems.append(f"beats[{i}] 时间段无效")
    elif layer == "shots":
        ids = {c["id"] for c in state["characters"]["characters"]}
        shots = data.shots
        if abs(shots[0].start_s) > 0.05:
            problems.append("第一个镜头必须从 0 秒开始")
        if abs(shots[-1].end_s - duration) > 0.25:
            problems.append(f"最后一个镜头必须在 {duration:g} 秒结束")
        for a, b in zip(shots, shots[1:]):
            if abs(a.end_s - b.start_s) > 0.1:
                problems.append(f"{a.id} 与 {b.id} 不首尾相接")
        for s in shots:
            if s.end_s <= s.start_s:
                problems.append(f"{s.id} 时长无效")
            if s.rig is not None and not s.camera_keys:
                target = s.rig.target or s.aim_actor
                if target is not None and target not in ids:
                    problems.append(f"{s.id} 的 rig.target {target} 不是人物 id")
            elif not s.aim_keys and s.aim_actor not in ids:
                problems.append(f"{s.id} 需要有效的 aim_actor 或 aim_keys")
    return problems


def _schema(layer: str) -> str:
    return json.dumps(MODELS[layer].model_json_schema(), ensure_ascii=False)


def _context(state: dict[str, dict]) -> str:
    return json.dumps(state, ensure_ascii=False) if state else "（无）"


def run_layer(layer: str, state: dict[str, dict], *, brief: str, spec: Spec, content_class: str,
              providers: Providers, ctx: CallContext, notes: str, examples: list[dict]) -> tuple[dict, dict]:
    duration = float(spec.duration_s)
    user, ref = render(f"forward.story_chain.{layer}", brief=brief, duration_s=duration, fps=spec.fps,
                       content_class=content_class, context_json=_context(state),
                       examples=examples if layer in USES_EXAMPLES else [], notes=notes, schema_json=_schema(layer),
                       **planning_guides(content_class, duration))
    payload = {"brief": brief, "duration_s": duration, "fps": spec.fps, "resolution": list(spec.resolution),
               "content_class": content_class, "context": state, "notes": notes}
    repairs: list[str] = []
    for _ in range(3):
        result = providers.llm.complete(CallContext(ctx.job_key, f"story_{layer}", ctx.batch_id), system=SYSTEM,
                                        user=user, json_mode=True, task=f"story_{layer}", payload=payload)
        try:
            data = MODELS[layer].model_validate(extract_json(result.text))
            problems = layer_problems(layer, data, state, duration)
            if problems:
                raise ValueError("；".join(problems))
            return data.model_dump(mode="json"), {"layer": layer, "prompt_ref": ref, "model": result.model,
                                                  "simulated": result.simulated, "request_id": result.request_id,
                                                  "repairs": repairs}
        except (ValueError, ValidationError) as exc:
            repairs.append(str(exc)[:600])
            user += f"\n\n上一次输出不合法，请只修正这些问题后重新输出完整 JSON：{str(exc)[:600]}"
    raise ValueError(f"story layer '{layer}' invalid after retries: {repairs[-1]}")


def assemble(state: dict[str, dict], content_class: str, duration: float) -> StoryPlan:
    return StoryPlan.model_validate({**state["premise"], **state["characters"], **state["events"], **state["space"],
                                     **state["shots"], "content_class": content_class, "duration_s": duration})


def plan_to_layers(plan: StoryPlan) -> dict[str, dict]:
    data = plan.model_dump(mode="json")
    return {layer: {k: data[k] for k in MODELS[layer].model_fields} for layer in LAYERS}


def mechanical_checks(plan: StoryPlan) -> list[dict[str, Any]]:
    """Team-rule checks a program can make; warnings only (the contract already enforces hard rules)."""
    d = plan.duration_s
    out: list[dict[str, Any]] = []

    def add(rule: str, ok: bool, detail: str) -> None:
        out.append({"rule": rule, "status": "ok" if ok else "warning", "detail": detail})

    lengths = [s.end_s - s.start_s for s in plan.shots]
    if len(plan.shots) > 1:
        bad = [f"{s.id} {s.end_s - s.start_s:.2f}s" for s in plan.shots if not 1.3 <= s.end_s - s.start_s <= 5.0]
        add("镜长 1.3–5 秒", not bad, "、".join(bad) or f"中位 {statistics.median(lengths):.2f}s")
    add("事件数 4–9", 4 <= len(plan.events) <= 9, f"{len(plan.events)} 个事件")
    twist_t = plan.events[plan.twist].at_s
    add("转折位于全片 20%–85%", 0.2 * d <= twist_t <= 0.85 * d, f"转折在 {twist_t:.2f}s（全片 {d:g}s）")
    gap = twist_t - max(plan.events[i].at_s for i in plan.setup)
    add("最后一个铺垫早于转折至少 1 秒", gap >= 1.0, f"间隔 {gap:.2f}s")
    add("每镜写清行动", all(s.action.strip() for s in plan.shots), "")
    add("路线与空间交接已写", bool(plan.beats), f"{len(plan.beats)} 条")
    add("写明仅在最终 AI 表现的内容", bool(plan.semantic_only.strip()), "")
    setup_seen = all(any(s.start_s <= plan.events[i].at_s < s.end_s for s in plan.shots) for i in plan.setup)
    add("铺垫事件都落在某个镜头内", setup_seen, "")
    return out + scene_checks(plan)


def scene_checks(plan: StoryPlan) -> list[dict[str, Any]]:
    """What the pre-render gate will say about this plan, phrased for the planner (all warnings here)."""
    settings = load_settings()
    base = settings.spec("forward_story")
    out: list[dict[str, Any]] = []

    def add(rule: str, ok: bool, detail: str) -> None:
        out.append({"rule": rule, "status": "ok" if ok else "warning", "detail": detail})

    try:
        scene = story_to_scene(plan, Spec(duration_s=plan.duration_s, fps=base.fps, resolution=base.resolution),
                               "check", gate=settings.qc.dynamic_gate, dressing=settings.forward.dressing).to_json_dict()
    except (ValueError, ValidationError) as exc:
        add("规划能生成场景", False, str(exc)[:300])
        return out
    checked = pregate(scene, settings)
    issues = checked["gate"]["issues"]
    motion = [i for i in issues if i["type"] in (*MOTION_TYPES, "actor_speed")]
    add("运动在各主体种类的物理上限内", not motion, "；".join(i["detail"] for i in motion[:3]))
    clipping = [i for i in issues if i not in motion]
    add("相机与人物不穿模", not clipping, "；".join(f"{i['shot']} {i['type']} {i['detail']}" for i in clipping[:3]))
    weak = [s for s in checked["framing"].get("shots", []) if s.get("visible_rate", 1.0) < settings.qc.framing.min_visible_rate]
    add("主体在画面里且没被体块挡住", not weak,
        "；".join(f"{s['shot']} 可见 {s['visible_rate']:.0%}（被挡 {s.get('occluded_frames', 0)} 帧）" for s in weak))
    report = checked["grammar"]
    for row in report["camera_language"]:
        if row["status"] == "warning":
            add(f"{row['shot']} 景别/角度与声明一致", False, row["detail"])
    for row in [*report["grammar"], report["pacing"]]:
        if row.get("status") in ("ok", "warning"):
            add(row["rule"], row["status"] == "ok", row.get("detail", ""))
    for row in report["setup_readability"]:
        add(f"铺垫 {row['event']} 在镜头里看得清", row["status"] == "ok", row.get("detail", ""))
    return out


def critique_loop(plan: StoryPlan, providers: Providers, ctx: CallContext, rounds: int = 2
                  ) -> tuple[StoryPlan, list[dict[str, Any]]]:
    if not providers.llm.is_real or providers.policy.dry_run:
        return plan, [{"status": "skipped", "reason": "mock 或 dry-run：评审-修正回路不运行（没有真实模型可评审）"}]
    history: list[dict[str, Any]] = []
    for n in range(1, rounds + 1):
        findings = mechanical_checks(plan)
        user, ref = render("forward.story_critique", plan_json=plan.model_dump_json(),
                           findings_json=json.dumps(findings, ensure_ascii=False))
        result = providers.llm.complete(CallContext(ctx.job_key, f"story_critique_{n}", ctx.batch_id), system=SYSTEM,
                                        user=user, json_mode=True, task="story_critique", payload={})
        try:
            verdict = extract_json(result.text)
        except ValueError:
            verdict = {"passed": False, "issues": [{"layer": "unknown", "rule": "评审输出不是 JSON", "detail": result.text[:200],
                                                    "fix": ""}]}
        issues = verdict.get("issues") or []
        entry = {"round": n, "prompt_ref": ref, "model": result.model, "passed": bool(verdict.get("passed")),
                 "issues": issues}
        history.append(entry)
        if entry["passed"] and not issues:
            break
        user, ref = render("forward.story_revise", plan_json=plan.model_dump_json(),
                           issues_json=json.dumps(issues, ensure_ascii=False),
                           schema_json=json.dumps(StoryPlan.model_json_schema(), ensure_ascii=False))
        revised = providers.llm.complete(CallContext(ctx.job_key, f"story_revise_{n}", ctx.batch_id), system=SYSTEM,
                                         user=user, json_mode=True, task="story_revise", payload={})
        try:
            plan = StoryPlan.model_validate(extract_json(revised.text))
            entry["revised"] = True
        except (ValueError, ValidationError) as exc:
            entry["revised"] = False
            entry["revise_error"] = str(exc)[:400]
            break
    return plan, history


def run_chain(brief: str, providers: Providers, ctx: CallContext, spec: Spec, content_class: str = "narrative", *,
              start: str = "premise", previous: dict[str, dict] | None = None, notes: str = "",
              examples: list[dict] | None = None, critique: bool = True) -> tuple[StoryPlan, dict[str, Any]]:
    if start not in LAYERS:
        raise ValueError(f"start must be one of {LAYERS}")
    state = {k: v for k, v in (previous or {}).items() if LAYERS.index(k) < LAYERS.index(start)}
    missing = [k for k in LAYERS[:LAYERS.index(start)] if k not in state]
    if missing:
        raise ValueError(f"re-running from '{start}' needs earlier layers: {missing}")
    examples = examples if examples is not None else [c.as_example() for c in pick_examples()]
    layers_info = []
    for layer in LAYERS[LAYERS.index(start):]:
        state[layer], info = run_layer(layer, state, brief=brief, spec=spec, content_class=content_class,
                                       providers=providers, ctx=ctx, notes=notes, examples=examples)
        layers_info.append(info)
    plan = assemble(state, content_class, float(spec.duration_s))
    history = []
    if critique:
        plan, history = critique_loop(plan, providers, ctx)
    return plan, {"mode": "chain", "start": start, "notes": notes, "layers": layers_info,
                  "examples": [e["title"] for e in examples], "critique": history,
                  "mechanical_checks": mechanical_checks(plan),
                  "simulated": all(i["simulated"] for i in layers_info)}


def revise_plan(old: StoryPlan, brief: str, notes: str, level: str, providers: Providers, ctx: CallContext,
                spec: Spec) -> tuple[StoryPlan, dict[str, Any]]:
    """[D5] 看片回路：story → 回到「构建故事」（事件链起重做）；shots → 回到「分镜与联合调度」（只重做分镜）。"""
    start = {"story": "events", "shots": "shots"}.get(level)
    if start is None:
        raise ValueError("level 必须是 story（故事或节奏需调整）或 shots（调度或画面需调整）")
    spec = Spec(duration_s=old.duration_s, fps=spec.fps, resolution=spec.resolution, lens_mm=spec.lens_mm)
    return run_chain(brief, providers, ctx, spec, old.content_class, start=start, previous=plan_to_layers(old),
                     notes=notes)


VERSIONED = ("scene.json", "story_plan.json", "job.json", "分镜总览.jpg", "剧本与分镜导演卡.txt", "视频续作提示词.txt",
             "reports/故事评审.json", "reports/导演卡对照.json")


def snapshot_version(job: JobPaths) -> Path:
    """Copy the current deliverables to versions/vNN before a revision overwrites them (never deleted)."""
    base = job.root / "versions"
    base.mkdir(exist_ok=True)
    n = 1 + max([int(p.name[1:]) for p in base.glob("v[0-9][0-9]") if p.name[1:].isdigit()], default=0)
    dest = base / f"v{n:02d}"
    dest.mkdir()
    names = list(VERSIONED)
    names += [p.name for p in job.root.glob("*_白模参考.mp4")]
    for name in names:
        src = job.root / name
        if src.exists():
            (dest / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / name)
    (dest / "snapshot.json").write_text(json.dumps({"created_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "files": [
        n for n in names if (job.root / n).exists()]}, ensure_ascii=False, indent=2), encoding="utf-8")
    return dest


# --------------------------------------------------------------------------- offline mocks, one per layer
def _seed(payload: dict[str, Any]) -> int:
    return int(hashlib.sha1(str(payload.get("brief", "")).encode("utf-8")).hexdigest()[:8], 16)


def _mock_scene(payload: dict[str, Any]) -> dict[str, Any]:
    duration, fps = float(payload.get("duration_s", 26)), int(payload.get("fps", 24))
    shots = max(3, min(9, round(duration / 3.2)))
    control = ControlSpec(index=1, seed=_seed(payload), shot_form="multi_shot", shot_count=shots, subject="person",
                          subject_child="duo", color="identity", era="modern", viewpoint="eye_level",
                          camera_move="follow", content_class=str(payload.get("content_class", "narrative")))
    return generate(control, Spec(duration_s=duration, fps=fps, resolution=tuple(payload.get("resolution", (1280, 720)))),
                    job_id="story", moves=STORY_MOVES, max_shot_s=5.0).to_json_dict()


def _note(payload: dict[str, Any]) -> str:
    return f"（已按看片意见修订，模拟：{payload['notes']}）" if payload.get("notes") else ""


@register_mock("story_premise")
def _mock_premise(payload: dict[str, Any]) -> str:
    brief = str(payload.get("brief", "")).strip()
    return json.dumps({"title": (brief.split("，")[0].split(",")[0] or "白模短片")[:14],
                       "logline": f"（模拟规划）{brief[:60]}", "world": "现代城市公共空间（模拟规划默认世界）",
                       "goal": "抵达终点门廊", "obstacle": "直行方向被体块挡住", "stakes": "路线选择决定能否按时抵达",
                       "ending": "两人先后穿过门廊"}, ensure_ascii=False)


@register_mock("story_characters")
def _mock_characters(payload: dict[str, Any]) -> str:
    scene = _mock_scene(payload)
    roles = ["主角：想最先抵达终点", "同伴：跟随主角，也会提醒路线"]
    return json.dumps({"characters": [{"id": a["id"], "role": roles[i % 2], "color": a.get("color") or IDENTITY_COLORS[i],
                                       "head": a["head"], "body": a["body"], "height_m": a["height_m"],
                                       "description": roles[i % 2]} for i, a in enumerate(scene["actors"])]},
                      ensure_ascii=False)


@register_mock("story_events")
def _mock_events(payload: dict[str, Any]) -> str:
    duration = float(payload.get("duration_s", 26))
    story = ("（模拟规划，仅用于离线联调）主角带着同伴朝终点门廊前进，先沿直行方向接近；看清前方被体块挡住后，"
             "他减速观察，转向侧面通道，同伴随即跟上。两人沿侧面通道加速，重新接近终点，先后穿过门廊。") + _note(payload)
    return json.dumps({"story": story, "events": mock_events(duration), "twist": 2, "setup": [0, 1],
                       "semantic_only": "两人的自然步态、回头示意与呼吸只在最终AI中表现；白模只保留整体位移与转向。",
                       "props": []}, ensure_ascii=False)


@register_mock("story_space")
def _mock_space(payload: dict[str, Any]) -> str:
    scene = _mock_scene(payload)
    return json.dumps({"spaces": [{"id": "SP1", "function": scene["meta"]["environment_description"]}],
                       "blocks": scene["blocks"], "paths": {a["id"]: a["path"]["keys"] for a in scene["actors"]},
                       "beats": mock_beats(float(payload.get("duration_s", 26)))}, ensure_ascii=False)


@register_mock("story_shots")
def _mock_shots(payload: dict[str, Any]) -> str:
    scene = _mock_scene(payload)
    return json.dumps({"shots": [{"id": s["id"], "start_s": s["start_s"], "end_s": s["end_s"], "title": s["title"],
                                  "action": s["action"], "framing": s["framing"], "angle": s["angle"], "move": s["move"],
                                  "lens_mm": s["lens_mm"], "camera_keys": s["camera"]["keys"],
                                  "aim_actor": s["camera"].get("aim_actor"), "aim_keys": s["camera"].get("aim_keys", [])}
                                 for s in scene["shots"]],
                       "time_map": None,
                       "notes": "mock 规划：未调用真实模型，仅用于离线联调与格式验证。" + _note(payload)}, ensure_ascii=False)
