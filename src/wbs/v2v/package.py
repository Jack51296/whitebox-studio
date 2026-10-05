"""Node 3 — bind images to @图N and build the portable 项目_提交包; export-only submission by default."""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

from ..config import VideoEncode
from ..errors import WbsError
from ..jsonio import read_json, write_json, write_text
from ..layout import safe_filename
from ..media.ffmpeg import probe, side_by_side
from ..providers import CallContext, Providers, SubmitResult
from .planner import V2VInputs


def build_package(inputs: V2VInputs, v2v_dir: Path, options: dict[str, Any], image_model: str | None) -> dict[str, Any]:
    plan = read_json(v2v_dir / "SP输出_v3.json")
    record = read_json(v2v_dir / "生成记录.json") if (v2v_dir / "生成记录.json").exists() else {"items": {}}
    pkg = v2v_dir / "项目_提交包"
    if pkg.exists():
        shutil.move(str(pkg), str(v2v_dir / f"项目_提交包_旧_{time.strftime('%Y%m%d-%H%M%S')}"))
    materials = pkg / "创作资料"
    materials.mkdir(parents=True)
    shutil.copyfile(inputs.video, pkg / "视频1_白模.mp4")
    info = probe(pkg / "视频1_白模.mp4", count_frames=True)
    images, missing, simulated = [], [], False
    for n, item in enumerate(plan["image_prompts"], 1):
        entry = record["items"].get(item["id"], {})
        name = f"图{n}_{safe_filename(item['name'])}.png"
        ok = entry.get("status") == "succeeded" and Path(entry.get("output", "")).exists()
        if ok:
            shutil.copyfile(entry["output"], pkg / name)
            simulated |= bool(entry.get("simulated"))
        else:
            missing.append({"ref": item["ref"], "id": item["id"], "status": entry.get("status", "not_requested"),
                            "reason": entry.get("error") or "; ".join(entry.get("issues", [])) or "尚未生成"})
        images.append({"ref": item["ref"], "id": item["id"], "name": item["name"], "file": name if ok else None,
                       "generation_mode": item["generation_mode"], "simulated": bool(entry.get("simulated")) if ok else None})
    write_text(pkg / "视频渲染提示词.txt", plan["video_prompt"] + "\n")
    shutil.copyfile(v2v_dir / "SP输出_v3.json", materials / "SP输出_v3.json")
    shutil.copyfile(v2v_dir / "生图提示词.txt", materials / "生图提示词.txt")
    shutil.copyfile(inputs.overview, materials / f"分镜总览{inputs.overview.suffix}")
    shutil.copyfile(inputs.director_card, materials / "剧本与分镜导演卡.txt")
    shutil.copyfile(inputs.continuation, materials / "视频续作提示词.txt")
    for sub in ("生图输入", "关键帧"):
        if (v2v_dir / sub).exists():
            shutil.copytree(v2v_dir / sub, materials / sub)
    if (v2v_dir / "生成记录.json").exists():
        shutil.copyfile(v2v_dir / "生成记录.json", materials / "生成记录.json")
    controls = {}
    source_controls = Path(inputs.video).parent / "控制通道"
    for name in ("depth", "seg", "edge"):
        found = sorted(source_controls.glob(f"*_{name}.mp4")) if source_controls.exists() else []
        if found:
            (pkg / "控制通道").mkdir(exist_ok=True)
            shutil.copyfile(found[0], pkg / "控制通道" / f"{name}.mp4")
            controls[name] = f"控制通道/{name}.mp4"
    files_complete = not missing and plan["status"] == "complete" and bool(plan["video_prompt"].strip())
    manifest = {
        "schema": "wbs.v2v.submission/1.0", "job_id": inputs.job_id, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "video": {"ref": "@视频1", "file": "视频1_白模.mp4", "width": info["width"], "height": info["height"],
                  "fps": info["fps_str"], "frames": info["frames"], "duration_s": info["duration_s"],
                  "audio_streams": info["audio_streams"]},
        "images": images, "video_prompt_file": "视频渲染提示词.txt", "controls": controls,
        "target_models": {"image": image_model or options.get("image_model"), "video": options.get("video_model")},
        "plan_status": plan["status"], "missing": missing, "files_complete": files_complete,
        "simulated_images": simulated,
        "package_ready": files_complete and not simulated,
        "package_ready_meaning": "仅表示可提交的文件齐全，不表示美术或视频效果已验证",
    }
    if simulated:
        manifest["note"] = "图片为 mock 占位图（未调用真实生图模型），因此 package_ready=false"
    write_json(pkg / "提交清单.json", manifest)
    return manifest


def submit(v2v_dir: Path, providers: Providers, ctx: CallContext) -> SubmitResult:
    pkg = v2v_dir / "项目_提交包"
    manifest = read_json(pkg / "提交清单.json")
    if not manifest["files_complete"]:
        raise WbsError("提交包文件不齐（见 提交清单.json 的 missing），不能提交")
    result = providers.video.submit(ctx, package_dir=pkg, manifest=manifest, video_seconds=manifest["video"]["duration_s"])
    write_json(v2v_dir / "提交记录.json", {"status": result.status, "message": result.message, "remote_id": result.remote_id,
                                          "provider": providers.video.name, "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                          "details": result.details})
    return result


def compare(white_video: Path, result_video: Path, out: Path, encode: VideoEncode) -> Path:
    """Side-by-side white model | V2V result for the dual-comparison dashboard."""
    if not result_video.exists():
        raise WbsError(f"{result_video} 不存在：把平台返回的成片放到该位置后再对比")
    return side_by_side(white_video, result_video, out, encode)
