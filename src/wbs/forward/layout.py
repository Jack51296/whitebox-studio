"""Optional layout import: blocks from an Infinigen room (or any .blend / layout JSON) into a job's scene.json.

The imported blocks replace the scene's blocks (the ground can be kept); cameras and actors stay as they are,
so the dynamic gate is re-run and every collision is reported — nothing is moved automatically. The previous
scene.json is copied to versions/ first.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

from ..config import Settings
from ..errors import WbsError
from ..jsonio import read_json, write_json
from ..layout import JobPaths
from ..models.scene import SceneSpec
from ..qc.dynamic import dynamic_gate
from ..tools import require_tool

EXPORTER = Path(__file__).resolve().parents[1] / "blender" / "layout_export.py"


def export_blend_layout(blend: Path, out: Path, exclude: str = "", timeout: int = 900) -> dict[str, Any]:
    cmd = [require_tool("blender"), "-b", str(blend), "--factory-startup", "-noaudio", "--python-exit-code", "1",
           "--python", str(EXPORTER), "--", "--out", str(out)]
    if exclude:
        cmd += ["--exclude", exclude]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if proc.returncode != 0 or not out.exists():
        raise WbsError(f"Blender 布局导出失败（exit {proc.returncode}）：{(proc.stdout + proc.stderr)[-600:]}")
    return read_json(out)


def import_layout(job: JobPaths, settings: Settings, source: Path, *, keep_ground: bool = True,
                  offset: tuple[float, float] = (0.0, 0.0), exclude: str = "") -> dict[str, Any]:
    if not job.scene.exists():
        raise WbsError(f"{job.key} 没有 scene.json：先生成场景（相机与主体路线）再导入布局")
    layout = export_blend_layout(source, job.root / "layout.json", exclude) if source.suffix == ".blend" else read_json(source)
    scene = read_json(job.scene)
    blocks = []
    for b in layout["blocks"]:
        if b.get("role") == "ground" and keep_ground:
            continue
        center = [b["center"][0] + offset[0], b["center"][1] + offset[1], b["center"][2]]
        blocks.append({**b, "center": center})
    kept = [b for b in scene["blocks"] if keep_ground and b.get("role") == "ground"]
    backup = job.root / "versions" / f"layout_{time.strftime('%Y%m%d-%H%M%S')}"
    backup.mkdir(parents=True, exist_ok=True)
    write_json(backup / "scene.json", scene)
    scene["blocks"] = kept + blocks
    scene.setdefault("meta", {})["layout"] = {"source": str(source), "blocks": len(blocks), "offset": list(offset)}
    scene = SceneSpec.model_validate(scene).to_json_dict()
    write_json(job.scene, scene)
    gate = dynamic_gate(scene, settings.qc.dynamic_gate)
    report = {"schema": "wbs.layout_import/1.0", "source": str(source), "imported_blocks": len(blocks),
              "kept_ground": len(kept), "backup": str(backup), "dynamic_gate": gate["status"],
              "issue_counts": gate["issue_counts"],
              "note": "相机与主体路线未改动；如有碰撞，调整 --offset 或在原布局里移开家具后重新导入"}
    write_json(job.report("布局导入.json"), report)
    job.update_meta(layout=str(source), pregate=gate["status"])
    return report
