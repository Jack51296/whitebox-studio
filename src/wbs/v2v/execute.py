"""Node 2 — frames and image inputs via the vendored prepare_image_inputs.py, then one request per image.

Batch key is job_id + image_id; a succeeded image with the same prompt and inputs is never requested
again. Checks are mechanical only (file exists and decodes). No visual scoring, no auto-retry of content.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from PIL import Image

from ..errors import WbsError
from ..jsonio import read_json, write_json
from ..layout import safe_filename
from ..providers import CallContext, Providers
from ..tools import require_tool
from .planner import SKILL_DIR

SCRIPT = SKILL_DIR / "scripts" / "prepare_image_inputs.py"


def _run(args: list[str]) -> tuple[int, dict[str, Any] | None, str]:
    cmd = [sys.executable, str(SCRIPT), *args, "--ffmpeg", require_tool("ffmpeg"), "--ffprobe", require_tool("ffprobe")]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    last = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    try:
        out = json.loads(last) if last else None
    except json.JSONDecodeError:
        out = None
    return proc.returncode, out, proc.stderr.strip()


def _fresh_dir(base: Path, name: str) -> Path:
    path = base / name
    if not path.exists():
        return path
    return base / f"{name}_{time.strftime('%Y%m%d-%H%M%S')}"


def extract_frames(plan: dict, video: Path, v2v_dir: Path) -> Path | None:
    anchors, seen = [], set()
    for item in plan["image_prompts"]:
        for a in item["space_source"]["anchors"]:
            if a["id"] not in seen:
                seen.add(a["id"])
                anchors.append(a)
    if not anchors:
        return None
    existing = v2v_dir / "关键帧" / "frame_manifest.json"
    if existing.exists():
        data = read_json(existing)
        if data.get("status") == "ready" and {a["id"] for a in data["anchors"]} >= seen:
            return existing
    anchors_file = v2v_dir / "关键帧清单.json"
    write_json(anchors_file, {"anchors": anchors})
    out = _fresh_dir(v2v_dir, "关键帧")
    code, _, err = _run(["--anchors", str(anchors_file), "--video", str(video), "--out", str(out)])
    manifest = out / "frame_manifest.json"
    if code == 2 or not manifest.exists():
        raise WbsError(f"取帧失败（prepare_image_inputs 退出码 {code}）：{err[-400:]}")
    return manifest


def _sha(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _files_sha(files: list[str]) -> list[str]:
    return [hashlib.sha1(Path(f).read_bytes()).hexdigest()[:16] for f in files]


def generate(plan_path: Path, video: Path, v2v_dir: Path, providers: Providers, ctx: CallContext,
             max_rounds: int = 10) -> dict[str, Any]:
    plan = read_json(plan_path)
    frame_manifest = extract_frames(plan, video, v2v_dir)
    record_path = v2v_dir / "生成记录.json"
    record = read_json(record_path) if record_path.exists() else {"job_id": ctx.job_key, "items": {}}
    images_dir = v2v_dir / "图片"
    order = {it["id"]: n for n, it in enumerate(plan["image_prompts"], 1)}
    names = {it["id"]: it["name"] for it in plan["image_prompts"]}
    for round_no in range(1, max_rounds + 1):
        generated = {k: v["output"] for k, v in record["items"].items()
                     if v.get("status") == "succeeded" and Path(v["output"]).exists()}
        gen_file = v2v_dir / "已生成图片.json"
        write_json(gen_file, generated)
        out = _fresh_dir(v2v_dir / "生图输入", f"round_{round_no:02d}")
        out.parent.mkdir(parents=True, exist_ok=True)
        args = ["--plan", str(plan_path), "--video", str(video), "--out", str(out), "--generated-manifest", str(gen_file)]
        if frame_manifest is not None:
            args += ["--frame-manifest", str(frame_manifest)]
        code, _, err = _run(args)
        if code == 2 or not (out / "input_manifest.json").exists():
            raise WbsError(f"准备生图输入失败（退出码 {code}）：{err[-400:]}")
        manifest = read_json(out / "input_manifest.json")
        progressed = False
        for item in manifest["image_prompts"]:
            iid = item["id"]
            prev = record["items"].get(iid, {})
            if item["status"] != "ready":
                if not prev.get("status") == "succeeded":
                    record["items"][iid] = {**prev, "status": item["status"], "issues": item.get("issues", [])}
                continue
            key = {"prompt_sha1": _sha(item["prompt"]), "inputs_sha1": _files_sha(item["input_files"])}
            if prev.get("status") == "succeeded" and Path(prev.get("output", "")).exists() and \
                    prev.get("prompt_sha1") == key["prompt_sha1"] and prev.get("inputs_sha1") == key["inputs_sha1"]:
                continue
            target = images_dir / f"图{order[iid]}_{safe_filename(names[iid])}.png"
            entry = {"image_id": iid, "ref": item["ref"], "generation_mode": item["generation_mode"],
                     "input_files": item["input_files"], **key, "requests": prev.get("requests", 0) + 1,
                     "requested_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
            try:
                res = providers.image.generate(ctx, prompt=item["prompt"], input_files=[Path(p) for p in item["input_files"]],
                                               out_path=target, image_id=iid)
                with Image.open(res.path) as img:
                    img.verify()
                entry.update(status="succeeded", output=str(res.path), model=res.model, request_id=res.request_id,
                             simulated=res.simulated)
                progressed = True
            except Exception as exc:  # noqa: BLE001  (record every failure per image, keep going)
                entry.update(status="failed", error=f"{type(exc).__name__}: {exc}"[:500])
            record["items"][iid] = entry
            write_json(record_path, record)
        write_json(record_path, record)
        if manifest["status"] == "ready" or not progressed:
            break
    summary = {s: sum(1 for v in record["items"].values() if v.get("status") == s)
               for s in ("succeeded", "failed", "waiting_dependency", "error")}
    record["summary"] = summary
    write_json(record_path, record)
    return record
