"""Story-driven white model ([D5] 白膜制作流程): brief → StoryPlan (LLM; mock by default) → scene.json."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import ValidationError

from ..camera_language import prompt_vocabulary
from ..config import Dressing, DynamicGate, Spec, load_settings
from ..jsonio import extract_json
from ..models.scene import SceneSpec
from ..models.story import StoryPlan
from ..prompts import render
from ..providers import CallContext, Providers
from ..providers.mock import register_mock
from ..qc.grammar import pacing_text
from ..taxonomy import ControlSpec
from .dressing import dress
from .motion import interpolation_for, limits_text, route_keys
from .procedural import IDENTITY_COLORS, generate
from .rigs import RIG_GUIDE, compile_rig

SYSTEM = "你是白模预演的编剧兼导演。只输出一个合法 JSON 对象，不输出解释、不输出代码块标记。"
STORY_MOVES = ["follow", "truck", "push", "pan", "pull", "follow", "truck"]


def planning_guides(content_class: str, duration_s: float, gate: DynamicGate | None = None) -> dict[str, str]:
    """Rule text for planner prompts, from the same sources the checks use: motion limits, camera vocabulary and
    rigs, team pacing."""
    gate = gate or load_settings().qc.dynamic_gate
    return {"motion_limits": limits_text(gate.kinds), "camera_vocabulary": prompt_vocabulary(),
            "rig_guide": RIG_GUIDE, "pacing": pacing_text(content_class, float(duration_s))}


def plan_story(brief: str, providers: Providers, ctx: CallContext, spec: Spec,
               content_class: str = "narrative") -> tuple[StoryPlan, dict[str, Any]]:
    schema = json.dumps(StoryPlan.model_json_schema(), ensure_ascii=False)
    user, ref = render("forward.story_planner", brief=brief, duration_s=spec.duration_s, fps=spec.fps,
                       content_class=content_class, schema_json=schema,
                       **planning_guides(content_class, spec.duration_s))
    payload = {"brief": brief, "duration_s": spec.duration_s, "fps": spec.fps, "content_class": content_class,
               "resolution": list(spec.resolution)}
    errors: list[str] = []
    for _ in range(3):
        result = providers.llm.complete(ctx, system=SYSTEM, user=user, json_mode=True, task="story_plan",
                                        payload=payload)
        try:
            plan = StoryPlan.model_validate(extract_json(result.text))
            return plan, {"prompt_ref": ref, "model": result.model, "simulated": result.simulated,
                          "request_id": result.request_id, "repairs": errors}
        except (ValueError, ValidationError) as exc:
            errors.append(str(exc)[:600])
            user += f"\n\n上一次输出不合法，请修正后只输出 JSON：{str(exc)[:600]}"
    raise ValueError(f"story plan invalid after retries: {errors[-1]}")


def _snap(t: float, fps: int) -> float:
    return round(round(t * fps) / fps, 6)


def character_keys(plan: StoryPlan, gate: DynamicGate | None = None) -> dict[str, list[list[float]]]:
    """Authored paths as given; routes compiled within each character kind's motion limits."""
    gate = gate or DynamicGate()
    out = {}
    for c in plan.characters:
        if c.id in plan.routes:
            out[c.id] = route_keys(plan.routes[c.id], gate.limits(c.kind), float(plan.duration_s), label=c.id)
        else:
            out[c.id] = [list(k) for k in plan.paths[c.id]]
    return out


def story_to_scene(plan: StoryPlan, spec: Spec, job_id: str, brief: str = "",
                   gate: DynamicGate | None = None, dressing: Dressing | None = None) -> SceneSpec:
    """``dressing`` adds scale/speed cues (forward/dressing.py) unless the plan turns them off."""
    fps, duration = int(spec.fps), float(plan.duration_s)
    keys = character_keys(plan, gate)
    actors = [{"id": c.id, "kind": c.kind, "label": c.role, "role": c.role, "height_m": c.height_m,
               "radius_m": c.radius, "head": c.head, "body": c.body, "color": c.color,
               "path": {"keys": keys[c.id], "interpolation": interpolation_for(keys[c.id])}}
              for c in plan.characters]
    shots, snapped, rigs = [], False, {}
    blocks = [b.model_dump() for b in plan.blocks]
    bounds = [0.0] + [_snap(s.end_s, fps) for s in plan.shots[:-1]] + [duration]
    for shot, start, end in zip(plan.shots, bounds, bounds[1:]):
        snapped |= abs(start - shot.start_s) > 1e-6 or abs(end - shot.end_s) > 1e-6
        move = shot.move
        if shot.camera_keys:
            camera = {"keys": [list(k) for k in shot.camera_keys], "interpolation": "cubic",
                      "aim_keys": [list(k) for k in shot.aim_keys], "aim_actor": shot.aim_actor}
        else:
            info = {"id": shot.id, "start_s": start, "end_s": end, "lens_mm": shot.lens_mm, "framing": shot.framing,
                    "angle": shot.angle, "aim_actor": shot.aim_actor}
            camera, rigs[shot.id] = compile_rig(shot.rig.model_dump(), info, actors, blocks)
            move = "pov" if shot.rig.type == "pov" else (move or shot.rig.type)
        shots.append({"id": shot.id, "start_s": start, "end_s": end, "lens_mm": shot.lens_mm, "camera": camera,
                      "title": shot.title, "action": shot.action, "framing": shot.framing, "angle": shot.angle,
                      "move": move})
    cast = "；".join(f"{c.role}＝{c.id}" for c in plan.characters)
    data = {
        "schema": "wbs.scene/1.0", "id": job_id, "title": plan.title, "fps": fps, "duration_s": duration,
        "resolution": list(spec.resolution), "precision": "low", "palette": "identity",
        "time_map": plan.time_map.model_dump() if plan.time_map else None,
        "blocks": blocks, "actors": actors, "shots": shots,
        "events": plan.events_with_roles(), "beats": [b.model_dump() for b in plan.beats],
        "render": {"engine": "workbench"},
        "source": {"kind": "forward_story", "brief": brief, "shots_snapped_to_frames": snapped},
        "meta": {"story": {"logline": plan.logline, "story": plan.story, "world": plan.world,
                           "intent": plan.notes or "；".join(f"{s.id} {s.title}" for s in plan.shots),
                           "semantic_only": plan.semantic_only or "自然肢体动作、服装、表情与手持物只在最终AI中表现。",
                           "cast": cast, "goal": plan.goal, "obstacle": plan.obstacle, "stakes": plan.stakes,
                           "ending": plan.ending, "twist": plan.twist, "setup": plan.setup},
                 "spaces": [f"{s.id}：{s.function}" for s in plan.spaces],
                 "content_class_label": "叙事" if plan.content_class == "narrative" else "运动",
                 "environment": "story", "environment_description": plan.world, "world": plan.world,
                 "lighting": plan.lighting, "rigs": rigs},
    }
    if dressing is not None and plan.dressing:
        dress(data, dressing)
    return SceneSpec.model_validate(data)


def mock_events(duration: float) -> list[dict[str, Any]]:
    """Offline event chain with a real setup → twist → consequence structure (index 2 is the twist)."""
    d = float(duration)
    rows = [(0.6, "目标建立", "主角望向终点门廊并起步", "观众明确目的地"),
            (d * 0.25, "障碍显露", "主角看清直行方向被体块挡住", "原路线不可行"),
            (d * 0.45, "判断与再选择", "主角减速观察后转向侧面通道", "同伴跟随改道"),
            (d * 0.7, "新路线成立", "两人沿侧面通道加速", "重新接近终点"),
            (max(d - 1.5, d * 0.85), "抵达确认", "两人先后穿过门廊", "改道的选择得到回报")]
    return [{"id": f"E{i + 1:02}", "at_s": round(t, 3), "mechanism": m, "choice": c, "consequence": q}
            for i, (t, m, c, q) in enumerate(rows)]


def mock_beats(duration: float) -> list[dict[str, Any]]:
    d = float(duration)
    return [{"start_s": 0.0, "end_s": round(d * 0.45, 3), "condition": "目标明确但路线未知",
             "action": "沿直行方向接近", "change": "发现障碍，停下观察"},
            {"start_s": round(d * 0.45, 3), "end_s": round(d * 0.7, 3), "condition": "侧面通道可以通行",
             "action": "转向并穿过侧面", "change": "绕开障碍"},
            {"start_s": round(d * 0.7, 3), "end_s": round(d, 3), "condition": "终点在望",
             "action": "加速抵达门廊", "change": "完成改道"}]


@register_mock("story_plan")
def _mock_story_plan(payload: dict[str, Any]) -> str:
    """Offline stand-in for the planning model: a coherent two-person plan built procedurally."""
    brief = str(payload.get("brief", ""))
    duration, fps = float(payload.get("duration_s", 26)), int(payload.get("fps", 24))
    resolution = tuple(payload.get("resolution", (1280, 720)))
    content = str(payload.get("content_class", "narrative"))
    seed = int(hashlib.sha1(brief.encode("utf-8")).hexdigest()[:8], 16)
    shots = max(3, min(7, int(duration // 4)))
    control = ControlSpec(index=1, seed=seed, shot_form="multi_shot", shot_count=shots, subject="person",
                          subject_child="duo", color="identity", era="modern", viewpoint="eye_level",
                          camera_move="follow", content_class=content)
    scene = generate(control, Spec(duration_s=duration, fps=fps, resolution=resolution), job_id="story",
                     moves=STORY_MOVES).to_json_dict()
    roles = ["主角", "同伴"]
    title = (brief.strip().split("，")[0].split(",")[0] or "白模短片")[:14]
    plan = {
        "title": title,
        "logline": f"（模拟规划）{brief.strip()[:60]}",
        "content_class": content, "duration_s": duration,
        "world": "现代城市公共空间（模拟规划默认世界，真实规划由模型按需求给出）",
        "story": f"（模拟规划，仅用于离线联调）{brief.strip()}。主角带着同伴穿过场地，途中减速观察、调整路线，最终抵达终点门廊。",
        "goal": "抵达终点门廊", "obstacle": "场地中的障碍与转向", "stakes": "路线选择决定能否按时抵达",
        "ending": "两人先后穿过门廊",
        "characters": [{"id": a["id"], "role": roles[i % 2], "color": a.get("color") or IDENTITY_COLORS[i],
                        "head": a["head"], "body": a["body"], "height_m": a["height_m"],
                        "description": f"{roles[i % 2]}，身份色区分"} for i, a in enumerate(scene["actors"])],
        "props": [], "spaces": [{"id": "SP1", "function": scene["meta"]["environment_description"]}],
        "blocks": scene["blocks"], "paths": {a["id"]: a["path"]["keys"] for a in scene["actors"]},
        "events": mock_events(duration), "twist": 2, "setup": [0, 1], "beats": mock_beats(duration),
        "semantic_only": "两人的自然步态、回头示意与呼吸只在最终AI中表现；白模只保留整体位移与转向。",
        "shots": [{"id": s["id"], "start_s": s["start_s"], "end_s": s["end_s"], "title": s["title"],
                   "action": s["action"], "framing": s["framing"], "angle": s["angle"], "move": s["move"],
                   "lens_mm": s["lens_mm"], "camera_keys": s["camera"]["keys"],
                   "aim_actor": s["camera"].get("aim_actor"), "aim_keys": s["camera"].get("aim_keys", [])}
                  for s in scene["shots"]],
        "time_map": None,
        "notes": "mock 规划：未调用真实模型，仅用于离线联调与格式验证。",
    }
    return json.dumps(plan, ensure_ascii=False)
