"""Node 1 — read the four files and write both prompt kinds in one planning call (SP输出_v3.json)."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import REPO_ROOT
from ..errors import WbsError
from ..jsonio import extract_json, read_json, write_json, write_text
from ..media.ffmpeg import probe
from ..prompts import render
from ..providers import CallContext, Providers
from ..providers.mock import register_mock
from . import SKILL_DIR_NAME, VIDEO_PROMPT_PREFIX
from .contract import validate

SKILL_DIR = REPO_ROOT / "skills" / SKILL_DIR_NAME
SHOT_HEADER = re.compile(r"^S(\d+)[A-Z]?｜([\d.]+)[–-]([\d.]+)s｜(.+)$")


@dataclass
class V2VInputs:
    job_id: str
    video: Path
    overview: Path
    director_card: Path
    continuation: Path
    storyboard: list[dict[str, Any]] = field(default_factory=list)
    base_dir: Path | None = None


def inputs_from_dir(folder: Path, job_id: str) -> V2VInputs:
    """Locate the four files in a delivery folder (team naming: *_白模参考.mp4, 分镜总览/故事板拼版, 导演卡, 续作提示词)."""
    folder = Path(folder)
    videos = sorted(folder.glob("*.mp4"))
    boards = [p for p in folder.iterdir() if p.suffix.lower() in (".jpg", ".png") and ("分镜总览" in p.stem or "故事板拼版" in p.stem)]
    cards = sorted(folder.glob("*导演卡*.txt"))
    conts = sorted(folder.glob("*续作提示词*.txt"))
    missing = [name for name, found in (("白模视频", videos), ("分镜总览", boards), ("剧本与分镜导演卡", cards),
                                        ("视频续作提示词", conts)) if not found]
    if missing:
        raise WbsError(f"{folder} 缺少：{'、'.join(missing)}（Skill v3 只需要这四份同版素材）")
    storyboard = []
    for candidate in (folder / "reports" / "storyboard-frames.json", folder / "storyboard-frames.json"):
        if candidate.exists():
            data = read_json(candidate)
            storyboard = normalize_storyboard(data["frames"] if isinstance(data, dict) and "frames" in data else data)
            break
    return V2VInputs(job_id, videos[0], boards[0], cards[0], conts[0], storyboard, folder)


def normalize_storyboard(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Accept both this platform's and the team package's storyboard-frames.json."""
    out = []
    for e in entries:
        out.append({"id": e["id"], "shot_id": e.get("shot_id", e["id"]),
                    "role": e["role"] if e.get("role") in ("shot_mid", "phase") else "shot_mid",
                    "frame": int(e["frame"]), "time": float(e.get("time", e.get("edit_time", 0.0))),
                    "file": str(e["file"]).replace("\\", "/")})
    return out


def parse_shots(director_card: str) -> list[dict[str, Any]]:
    shots = []
    lines = director_card.splitlines()
    for i, line in enumerate(lines):
        m = SHOT_HEADER.match(line.strip())
        if not m:
            continue
        action = ""
        for nxt in lines[i + 1:i + 4]:
            if nxt.startswith("动作与可见后果："):
                action = nxt.split("：", 1)[1].strip()
                break
        shots.append({"n": len(shots) + 1, "id": f"S{m.group(1)}", "start": float(m.group(2)), "end": float(m.group(3)),
                      "title": m.group(4).strip(), "action": action})
    return shots


def system_prompt() -> tuple[str, str]:
    sp = (SKILL_DIR / "references" / "planner-sp.md").read_text(encoding="utf-8")
    schema = (SKILL_DIR / "references" / "output-schema.md").read_text(encoding="utf-8")
    text = sp.strip() + "\n\n---\n\n" + schema.strip()
    return text, f"skill:{SKILL_DIR_NAME}/planner-sp+output-schema#{hashlib.sha256(text.encode('utf-8')).hexdigest()[:12]}"


def _story_lines(card: str) -> dict[str, str]:
    out = {}
    for line in card.splitlines():
        for key in ("一句话", "完整剧本", "世界与空间", "摄影意图"):
            if line.startswith(key + "："):
                out[key] = line.split("：", 1)[1].strip()
    return out


def plan(inputs: V2VInputs, out_dir: Path, providers: Providers, ctx: CallContext,
         options: dict[str, Any], external: Path | None = None) -> dict[str, Any]:
    """``external``: an SP输出_v3.json written outside the platform; it replaces the model call but not the contract check."""
    card = inputs.director_card.read_text(encoding="utf-8-sig")
    continuation = inputs.continuation.read_text(encoding="utf-8-sig")
    shots = parse_shots(card)
    if not shots:
        raise WbsError("导演卡中没有找到 'Sxx｜起–止s｜标题' 格式的镜头行；无法沿用已有分镜")
    info = probe(inputs.video)
    sp, sp_ref = system_prompt()
    video = {"name": inputs.video.name, "width": info["width"], "height": info["height"], "fps": round(info["fps"], 3),
             "duration_s": info["duration_s"], "frames": info["frames"]}
    user, user_ref = render("v2v.planner_user", job_id=inputs.job_id, video=video, overview_name=inputs.overview.name,
                            director_card=card, continuation=continuation, options=options)
    frames = []
    images = [inputs.overview]
    base = inputs.base_dir or inputs.video.parent
    by_shot = {s["id"]: s["n"] for s in shots}
    for f in inputs.storyboard:
        path = base / f["file"]
        if path.exists() and f.get("role", "shot_mid") == "shot_mid" and f.get("shot_id", f.get("id")) in by_shot:
            images.append(path)
            frames.append({"image_no": len(images), "shot_n": by_shot[f.get("shot_id", f.get("id"))], "time_s": f["time"],
                           "frame_1based": f["frame"]})
    if frames:
        user += "\n\n附带的白模原帧（同一视频，成片时间；可直接作为 anchors 定位）：\n" + "\n".join(
            f"- 附图{f['image_no']}：镜头{f['shot_n']}，time_s={f['time_s']}，frame_number={f['frame_1based']}（1基）" for f in frames)
    payload = {"job_id": inputs.job_id, "shots": shots, "frames": frames, "options": options,
               "story": _story_lines(card), "title": card.splitlines()[0].strip("《》 ").replace("》剧本与分镜导演卡", "")}
    attempts = []
    plan_obj: Any = None
    if external is not None:
        try:
            plan_obj = read_json(external)
            errors = validate(plan_obj, len(shots))
        except ValueError as exc:
            errors = [f"not JSON: {exc}"]
        attempts.append({"model": "external", "simulated": False, "request_id": None, "source": external.name,
                         "contract_errors": errors})
    for _ in range(0 if external is not None else 2):
        result = providers.llm.complete(ctx, system=sp, user=user, images=images, json_mode=True, task="v2v_plan",
                                        payload=payload)
        try:
            plan_obj = extract_json(result.text)
            errors = validate(plan_obj, len(shots))
        except ValueError as exc:
            errors = [f"not JSON: {exc}"]
        attempts.append({"model": result.model, "simulated": result.simulated, "request_id": result.request_id,
                         "contract_errors": errors})
        if not errors:
            break
        user += "\n\n上一次输出未通过数据契约机械校验，请只修正这些问题后重新输出完整 JSON：\n- " + "\n- ".join(errors[:20])
    out_dir.mkdir(parents=True, exist_ok=True)
    record = {"job_id": inputs.job_id, "system_prompt_ref": sp_ref, "user_prompt_ref": user_ref, "attempts": attempts,
              "shots": len(shots), "attached_images": [str(p.name) for p in images],
              "contract_valid": not attempts[-1]["contract_errors"], "simulated": attempts[-1]["simulated"]}
    write_json(out_dir / "规划记录.json", record)
    if attempts[-1]["contract_errors"]:
        write_json(out_dir / "SP输出_v3.invalid.json", plan_obj)
        raise WbsError("规划结果未通过 v3 数据契约：" + "; ".join(attempts[-1]["contract_errors"][:5]))
    write_json(out_dir / "SP输出_v3.json", plan_obj)
    write_text(out_dir / "视频渲染提示词.txt", plan_obj["video_prompt"] + "\n")
    write_text(out_dir / "生图提示词.txt", "\n\n".join(
        f"{it['ref']}｜{it['id']}｜{it['name']}｜{it['generation_mode']}\n{it['prompt']}" for it in plan_obj["image_prompts"]) + "\n")
    materials = out_dir / "四文件"
    materials.mkdir(exist_ok=True)
    for src in (inputs.overview, inputs.director_card, inputs.continuation):
        shutil.copyfile(src, materials / src.name)
    return {"plan": plan_obj, "record": record}


@register_mock("v2v_plan")
def _mock_v2v_plan(payload: dict[str, Any]) -> str:
    """Offline planner: a contract-valid plan (overview text_to_image + local image_to_image per chosen shot)."""
    shots = payload["shots"]
    frames = {f["shot_n"]: f for f in payload.get("frames", [])}
    options = payload.get("options", {})
    story = payload.get("story", {})
    max_images = max(1, int(options.get("max_images", 4)))
    world = story.get("世界与空间", "按导演卡设定的世界")
    style = options.get("style", "真实电影实拍")
    boundary = "白模画面只提供空间关系和动作成立的逻辑，不提供最终外观；重新设计完整真实环境，不在白模几何上贴材质或只换颜色。"
    overview_prompt = (f"{style}质感的场景全景：{world}。完整呈现本片主要空间的真实建筑、地形、材质与自然光照，"
                       "结构清楚、材料有差异、细节随景别增加；远景可有少量合理的小比例路人与日常设施，不出现人物近景。"
                       "不要文字、标志、水印，不要白模、灰模或低模质感。")
    images = [{
        "id": "img_01", "ref": "@图1", "scene_ids": ["main_space"], "name": "主场景全景", "purpose": "统一全片环境设计、材质与光色",
        "shot_ids": [s["n"] for s in shots], "image_type": "scene_overview", "generation_mode": "text_to_image",
        "space_source": {"kind": "scene_description", "anchors": [], "preserve": ["主要通路与空间方位关系"],
                         "design_freedom": ["建筑与地形造型", "材质", "光照与时段氛围"]},
        "appearance_sources": [], "background_constraints": {"required": ["本片主要空间的完整环境"], "forbidden": ["文字招牌", "水印"]},
        "state_scope": {"shown": "无剧情状态的环境全貌", "video_rule": "只继承环境设计与光色，不带入全景机位"},
        "video_usage": [{"shot_id": s["n"], "inherit": ["环境设计", "材质与光色"], "exclude": ["全景机位与构图"]} for s in shots],
        "prompt_constraints": ["不出现人物近景", "不要文字与水印", "不要白模质感"], "prompt": overview_prompt,
        "reference_image_ids": [],
    }]
    candidates = [s for s in shots if s["n"] in frames] or shots
    step = max(1, len(candidates) // max(1, max_images - 1))
    chosen = candidates[::step][: max_images - 1]
    for k, shot in enumerate(chosen, start=2):
        frame = frames.get(shot["n"])
        anchor = {"id": f"frame_s{shot['n']:02d}", "shot_id": shot["n"],
                  "time_s": frame["time_s"] if frame else round((shot["start"] + shot["end"]) / 2, 3),
                  "time_precision": "exact" if frame else "approximate", "role": "primary",
                  "description": f"镜头{shot['n']}（{shot['id']}）中间帧，提供该区域的通行与遮挡关系",
                  "frame_number": frame["frame_1based"] if frame else None, "frame_number_base": 1 if frame else None}
        prompt = (f"{style}质感的局部场景：{shot['title']}。{boundary}保留参考帧中的通路方向、高差、前后遮挡与主体活动区域，"
                  f"按{world}重新设计真实建筑构件、材料和光照；沿用 @图1 的整体环境设计与光色。画面不出现人物近景，"
                  "不要文字与水印，不要白模、灰模或低模质感。")
        images.append({
            "id": f"img_{k:02d}", "ref": f"@图{k}", "scene_ids": [f"zone_{shot['id'].lower()}"], "name": f"{shot['title']}局部",
            "purpose": f"镜头{shot['n']}的关键区域空间逻辑与真实设计", "shot_ids": [shot["n"]], "image_type": "shot_scene",
            "generation_mode": "image_to_image",
            "space_source": {"kind": "shot_frame", "anchors": [anchor], "preserve": ["通路方向", "高差与前后遮挡", "主体活动区域"],
                             "design_freedom": ["建筑构件造型", "材料", "光照细节"]},
            "appearance_sources": [{"image_id": "img_01", "inherit": ["整体环境设计", "材质与光色"], "exclude": ["全景机位", "其他区域设施"]}],
            "background_constraints": {"required": ["本区域的真实环境"], "forbidden": ["其他区域的终点设施", "文字招牌"]},
            "state_scope": {"shown": "本镜开始前的环境状态", "video_rule": "只在本镜借用本区域设计，不提前带入后续状态"},
            "video_usage": [{"shot_id": shot["n"], "inherit": ["本区域环境设计"], "exclude": ["参考图构图与机位"]}],
            "prompt_constraints": ["白模只提供逻辑不提供外观", "不出现人物近景", "不要文字与水印"], "prompt": prompt,
            "reference_image_ids": ["img_01"],
        })
    refs = {s["n"]: ["@图1"] for s in shots}
    for item in images[1:]:
        refs[item["shot_ids"][0]].append(item["ref"])
    lines = [VIDEO_PROMPT_PREFIX + f"{story.get('一句话', payload.get('title', ''))}（mock 规划，仅用于离线联调）"]
    for s in shots:
        lines.append(f"镜头{s['n']}｜{s['start']:.2f}—{s['end']:.2f}秒｜{s['title']}（{s['id']}）／参考图 {'、'.join(refs[s['n']])}"
                     f"／摄影：沿用白模机位、景别与运镜／动作：{s['action'] or s['title']}")
    plan = {"status": "complete", "image_prompts": images, "video_prompt": "\n".join(lines),
            "issues": [{"type": "mock", "shot_ids": [], "description": "离线 mock 规划，未调用真实规划模型",
                        "handling": "接入真实模型后重新规划"}]}
    return json.dumps(plan, ensure_ascii=False)
