"""Blender orchestration: scene.json → build / audit / render in Blender → H.264 → storyboard and texts."""

from __future__ import annotations

import math
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .config import Settings
from .errors import WbsError
from .forward import texts
from .forward.story_examples import compare_card
from .jsonio import read_json, write_json, write_text
from .layout import JobPaths
from .log import get_logger, log_event
from .media import boards, vtt
from .media.ffmpeg import encode_frames, extract_frame
from .tools import require_tool

log = get_logger(__name__)
ENTRY = Path(__file__).resolve().parent / "blender" / "entry.py"


def blender_command(job: JobPaths, settings: Settings, *, engine: str, frames: str, render: bool, audit: bool,
                    save_blend: bool, audit_step: int = 1, passes: list[str] | None = None) -> list[str]:
    cmd = [require_tool("blender"), "-b", "--factory-startup", "-noaudio", "--python-exit-code", "1",
           "--python", str(ENTRY), "--", "--scene", str(job.scene), "--out", str(job.render_dir),
           "--engine", engine, "--frames", frames, "--status", str(job.render_dir / "blender_status.json")]
    if audit:
        cmd += ["--audit", str(job.audit_dir), "--audit-step", str(audit_step)]
    if save_blend:
        cmd += ["--blend", str(job.blend())]
    if not render:
        cmd.append("--no-render")
    if settings.fbx_library:
        cmd += ["--fbx-library", settings.fbx_library]
    if passes:
        cmd += ["--passes", ",".join(passes)]
    return cmd


def run_blender(job: JobPaths, settings: Settings, *, engine: str | None = None, frames: str = "all",
                render: bool = True, audit: bool = True, save_blend: bool = True,
                timeout: int | None = None, passes: list[str] | None = None) -> dict[str, Any]:
    if not job.scene.exists():
        raise WbsError(f"{job.scene} missing")
    engine = engine or settings.render.engine
    job.render_dir.mkdir(parents=True, exist_ok=True)
    if render and frames == "all" and job.frames_dir.exists():
        for old in job.frames_dir.glob("f*.png"):
            old.unlink()
    cmd = blender_command(job, settings, engine=engine, frames=frames, render=render, audit=audit,
                          save_blend=save_blend, passes=passes)
    log_path = job.render_dir / "blender.log"
    started = time.time()
    with log_path.open("w", encoding="utf-8", errors="replace") as stream:
        try:
            proc = subprocess.run(cmd, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout or settings.render.timeout_s)
        except subprocess.TimeoutExpired as exc:
            raise WbsError(f"Blender timed out after {exc.timeout}s; see {log_path}") from exc
    if proc.returncode != 0:
        tail = log_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-15:]
        raise WbsError(f"Blender failed (exit {proc.returncode}); see {log_path}\n" + "\n".join(tail))
    status = read_json(job.render_dir / "blender_status.json")
    status["wall_s"] = round(time.time() - started, 2)
    log_event(log, "blender finished", job=job.key, seconds=status["wall_s"])
    return status


def encode(job: JobPaths, settings: Settings, scene: dict) -> Path:
    expected = int(round(scene["duration_s"] * scene["fps"]))
    found = len(list(job.frames_dir.glob("f*.png")))
    if found != expected:
        raise WbsError(f"{job.key}: expected {expected} rendered frames, found {found}")
    return encode_frames(job.frames_dir, job.video(scene["title"]), int(scene["fps"]), settings.render.video)


def storyboard(job: JobPaths, scene: dict, video: Path | None = None) -> list[dict[str, Any]]:
    """One mid frame per shot (故事板/Sxx.png); phase frames added when the film has fewer than 4 shots."""
    from .blender.kinematics import SceneEvaluator

    ev = SceneEvaluator(scene)
    job.storyboard_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    per_shot = 1 if len(scene["shots"]) >= 4 else math.ceil(4 / len(scene["shots"]))

    def grab(frame: int, dest: Path) -> None:
        src = job.frames_dir / f"f{frame:04d}.png"
        if src.exists():
            shutil.copyfile(src, dest)
        elif video is not None and video.exists():
            extract_frame(video, dest, frame_index=frame - 1)
        else:
            raise WbsError(f"no rendered frame {frame} and no video to extract it from")

    for i, shot in enumerate(scene["shots"]):
        first, last = ev.shot_frames(i)
        mid = (first + last) // 2
        dest = job.storyboard_dir / f"{shot['id']}.png"
        grab(mid, dest)
        entries.append({"id": shot["id"], "shot_id": shot["id"], "role": "shot_mid", "frame": mid,
                        "time": round(ev.frame_time(mid), 4), "file": f"故事板/{dest.name}"})
        if per_shot > 1:
            for k in range(per_shot):
                frame = first + int((k + 0.5) * (last - first + 1) / per_shot)
                frame = min(max(frame, first), last)
                dest = job.storyboard_dir / f"{shot['id']}_p{k + 1}.png"
                grab(frame, dest)
                entries.append({"id": f"{shot['id']}_p{k + 1}", "shot_id": shot["id"], "role": "phase",
                                "frame": frame, "time": round(ev.frame_time(frame), 4), "file": f"故事板/{dest.name}"})
    write_json(job.report("storyboard-frames.json"), {"schema": "wbs.storyboard/1.0", "frames": entries})
    return entries


def overview(job: JobPaths, scene: dict, entries: list[dict[str, Any]]) -> Path:
    phases = [e for e in entries if e["role"] == "phase"]
    chosen = phases if phases else [e for e in entries if e["role"] == "shot_mid"]
    shots = {s["id"]: s for s in scene["shots"]}
    cards = []
    for e in chosen:
        shot = shots[e["shot_id"]]
        cards.append((job.root / e["file"], f"{e['id']}  {e['time']:.2f}s  {shot.get('title', '')}"))
    cols = 4 if len(cards) >= 8 else (3 if len(cards) > 4 else 2)
    w, h = scene["resolution"]
    cell = (480, int(480 * h / w))
    return boards.contact_sheet(cards, job.overview, cols=cols, cell=cell)


def write_texts(job: JobPaths, scene: dict, entries: list[dict[str, Any]] | None, style: str = "real") -> dict[str, str]:
    job.prompts_dir.mkdir(parents=True, exist_ok=True)
    control_text, control_ref = texts.control_layer_prompt(scene)
    v2v_text, v2v_ref = texts.v2v_render_prompt(scene, style=style)
    write_text(job.prompts_dir / "白模控制层.txt", control_text)
    write_text(job.prompts_dir / "V2V渲染提示词.txt", v2v_text)
    card = texts.director_card(scene, entries)
    write_text(job.director_card, card)
    labels = (scene.get("meta") or {}).get("labels") or {}
    content = labels.get("content_class_label") or (scene.get("meta") or {}).get("content_class_label", "")
    write_json(job.report("导演卡对照.json"),
               compare_card(card, {"叙事": "narrative", "运动": "motion"}.get(content)))
    write_text(job.continuation, texts.continuation_prompt(scene))
    vtt.write_vtt(texts.vtt_cues(scene), job.vtt)
    boards.route_map(scene, job.route_map)
    refs = {"control_layer": control_ref, "v2v_render": v2v_ref}
    write_json(job.prompts_dir / "prompt_refs.json", refs)
    return refs


def render_job(job: JobPaths, settings: Settings, *, engine: str | None = None, keep_frames: bool = False,
               save_blend: bool = True, style: str = "real") -> dict[str, Any]:
    scene = read_json(job.scene)
    status = run_blender(job, settings, engine=engine, save_blend=save_blend)
    video = encode(job, settings, scene)
    entries = storyboard(job, scene, video)
    overview(job, scene, entries)
    refs = write_texts(job, scene, entries, style=style)
    if not keep_frames:
        shutil.rmtree(job.frames_dir, ignore_errors=True)
    job.update_meta(title=scene["title"], video=video.name, rendered_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                    blender=status.get("blender_version"), render_seconds=status.get("wall_s"),
                    engine=status.get("render", {}).get("engine"), prompt_refs=refs)
    return {"video": str(video), "status": status, "storyboard": len(entries)}
