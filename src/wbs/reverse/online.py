"""Online-model conversion package ([D2] Omni 线上反推: 真实视频 + 四宫格白模样板图 + 提示词). Export only.

The four-panel template must show white-model forms (never raw frames): it is composed from this
job's own rendered white-model frames. The source video is face-blurred first; nothing is uploaded.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

from ..config import Settings
from ..errors import WbsError
from ..hashing import sha256_file
from ..jsonio import read_json, write_json, write_text
from ..layout import JobPaths
from ..media import boards
from ..media.ffmpeg import extract_frame
from ..privacy import blur_faces
from ..prompts import render

DEFAULT_FORMS = {
    "person": ("人物必须始终是四宫格样板中那种积木人，头是立方体、身体是长方体，各部分独立拼接，不要脸、五官、头发、衣服褶皱、手指、鞋子，"
               "不要身体曲线和真实人形轮廓，不要低多边形写实人物。"),
    "bird": ("鸟必须始终是四宫格样板中那种体块鸟，躯干是一个长条形大体块，两侧翅膀各是一整片平直薄板，头是一个小体块，各部分独立拼接。"
             "翅膀扇动时只能表现为两片薄板整体上下摆动。"),
    "vehicle": "载具必须始终是四宫格样板中那种体块载具，由不超过四个长方体/圆柱体块组成，不要窗户、文字、灯具和任何表面细节。",
}
DEFAULT_STRUCTURE = "地面合并为一整块平面；建筑、地形和道具只保留长方体、圆柱等大体块；"


def _template_frames(job: JobPaths, scene: dict) -> list[Path]:
    boards_dir = job.storyboard_dir
    picks = sorted(p for p in boards_dir.glob("S*.png") if "_p" not in p.stem) if boards_dir.exists() else []
    if len(picks) < 4:
        picks = sorted(boards_dir.glob("S*.png")) if boards_dir.exists() else []
    video = job.video(scene["title"])
    if len(picks) < 4 and video.exists():
        total = int(round(scene["duration_s"] * scene["fps"]))
        tmp = job.root / "online" / "_template"
        picks = [extract_frame(video, tmp / f"t{i}.png", frame_index=int((i + 0.5) * total / 4)) for i in range(4)]
    if len(picks) < 4:
        raise WbsError("四宫格白模样板图需要本任务先渲染出白模（wbs render），不能用原片画面代替")
    step = len(picks) / 4
    return [picks[int(i * step)] for i in range(4)]


def build_package(job: JobPaths, settings: Settings, source_video: Path, *, subject: str = "person",
                  template_desc: str | None = None, subject_form: str | None = None,
                  scene_structure: str | None = None) -> dict[str, Any]:
    scene = read_json(job.scene)
    analysis = read_json(job.analysis_dir / "analysis.json")
    out = job.root / "online" / "提交包"
    if out.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        shutil.move(str(out), str(out.with_name(f"提交包_旧_{stamp}")))
    out.mkdir(parents=True)
    grid = boards.grid_2x2(_template_frames(job, scene), out / "四宫格白模样板图.jpg")
    desc = template_desc or "四个格子分别展示了本片主体和场景在不同时刻、不同视角下白模化后的标准形态"
    prompt, ref = render("reverse.online_convert", duration_s=round(analysis["video"]["duration_s"], 2),
                         template_desc=desc, subject_form=subject_form or DEFAULT_FORMS.get(subject, DEFAULT_FORMS["person"]),
                         scene_structure=scene_structure or DEFAULT_STRUCTURE,
                         cut_times=[f"{t:.2f}" for t in analysis["cuts"]["times_s"]])
    write_text(out / "提示词.txt", prompt)
    blurred = out / "原视频_人脸打码.mp4"
    face = blur_faces(source_video, blurred, settings.render.video)
    meta = job.read_meta()
    manifest = {
        "schema": "wbs.reverse.online_package/1.0", "job": job.key, "status": "export_only", "uploaded": False,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "prompt_ref": ref,
        "files": {p.name: {"sha256": sha256_file(p), "bytes": p.stat().st_size} for p in (grid, out / "提示词.txt", blurred)},
        "face_blur": face, "source_license": meta.get("source_license"),
        "manual_checks": {"人脸打码抽检": "pending", "素材授权确认": "pending" if not meta.get("source_license") else "recorded"},
        "cut_times_s": analysis["cuts"]["times_s"],
    }
    write_json(out / "提交清单.json", manifest)
    write_text(out / "说明.md", "\n".join([
        "# 在线模型转换提交包（仅导出，未上传）", "",
        "1. 先人工抽查 `原视频_人脸打码.mp4`：自动检测可能漏掉小脸、遮挡或大角度侧脸。",
        "2. 确认素材授权允许外发到所用平台。",
        "3. 在平台上传：视频 = `原视频_人脸打码.mp4`，参考图 = `四宫格白模样板图.jpg`，提示词 = `提示词.txt` 全文。",
        "4. 下载结果后放到本任务 `online/结果/`，再运行 `wbs qc` 做媒体与切镜核对。", "",
        "本包由程序生成，不包含任何账号或密钥。"]) + "\n")
    job.update_meta(online_package=str(out.relative_to(job.root)), online_status="export_only")
    return manifest
