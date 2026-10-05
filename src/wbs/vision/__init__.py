"""Optional vision backends with automatic fallback to the built-in implementations.

Every kind has a fallback chain ending in the existing implementation, which needs nothing extra.
``resolve`` walks the chain from the requested backend and records why a better one was skipped.
"""

from __future__ import annotations

import importlib.util
from dataclasses import asdict, dataclass
from typing import Any

from ..config import load_settings
from .fetch import model_path
from .fetch import status as model_status
from .registry import get as model_spec

CHAINS: dict[str, tuple[str, ...]] = {
    "cuts": ("transnetv2", "pyscenedetect", "builtin"),
    "subject": ("sam3", "grounding_dino", "motion"),
    "geometry": ("mapanything", "da3", "heuristic"),
    "faces": ("centerface", "yunet"),
    "camera_motion": ("llm", "rules"),
}
BUILTIN = {"cuts": "builtin", "subject": "motion", "geometry": "heuristic", "faces": "yunet", "camera_motion": "rules"}


@dataclass
class Choice:
    kind: str
    requested: str
    used: str
    skipped: dict[str, str]

    @property
    def fell_back(self) -> bool:
        return self.used != self.requested

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def has_package(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _model_ready(name: str) -> str | None:
    spec = model_spec(name)
    folder = model_path(name)
    if spec.gated:
        if folder.exists() and any(folder.iterdir()):
            return None
        return f"{name} 权重是门控仓库，未申请也未下载（{spec.note}）"
    missing = [f.path for f in spec.files if not (folder / f.path.split("/")[-1]).exists()]
    return f"缺少模型文件 models/{name}/（运行 scripts/fetch_models.py）: {', '.join(missing[:3])}" if missing else None


def _worker_package(package: str) -> str | None:
    from .worker import selftest

    info = selftest()
    if not info.get("installed"):
        return info.get("reason", "未安装视觉工作环境")
    if not info.get("ok", True):
        return f"视觉工作环境自检失败：{info.get('reason')}"
    if not info.get("packages", {}).get(package):
        return f"视觉工作环境缺少 {package} 包"
    return None


def unavailable_reason(kind: str, name: str) -> str | None:
    """None when the backend can run here, otherwise a human-readable reason."""
    if name == BUILTIN.get(kind):
        if kind == "faces":
            from ..tools import find_tool

            return None if find_tool("face_model") else "未配置 YuNet 人脸模型（tools/tools.json 或 WBS_FACE_MODEL）"
        return None
    if kind == "cuts" and name == "pyscenedetect":
        return None if has_package("scenedetect") else "未安装 scenedetect（pip install -e .[vision]）"
    if kind == "cuts" and name == "transnetv2":
        if not has_package("onnxruntime"):
            return "未安装 onnxruntime（pip install -e .[vision]）"
        onnx = model_path("transnetv2") / "transnetv2.onnx"
        return None if onnx.exists() else "缺少 models/transnetv2/transnetv2.onnx（fetch_models.py 下载权重后由视觉工作环境导出）"
    if kind == "faces" and name == "centerface":
        return _model_ready("centerface")
    if kind == "subject" and name == "grounding_dino":
        return _model_ready("grounding-dino-tiny") or _worker_package("transformers")
    if kind == "subject" and name == "sam3":
        return _model_ready("sam3") or _worker_package("sam3")
    if kind == "geometry" and name == "da3":
        if all(_model_ready(m) for m in ("da3-small", "da3-base")):
            return _model_ready("da3-small")
        return _worker_package("depth_anything_3")
    if kind == "geometry" and name == "mapanything":
        return _model_ready("map-anything-apache") or _worker_package("mapanything")
    if kind == "camera_motion" and name == "llm":
        return None
    return f"未知后端 {kind}={name}（可选：{', '.join(CHAINS.get(kind, ()))}）"


def resolve(kind: str, requested: str | None = None) -> Choice:
    requested = requested or getattr(load_settings().vision, kind, None) or BUILTIN[kind]
    chain = CHAINS[kind]
    start = chain.index(requested) if requested in chain else len(chain) - 1
    skipped: dict[str, str] = {}
    if requested not in chain:
        skipped[requested] = f"未知后端（可选：{', '.join(chain)}）"
    for name in chain[start:]:
        reason = unavailable_reason(kind, name)
        if reason is None or name == BUILTIN[kind]:
            if reason is not None:
                skipped[name] = reason
            return Choice(kind, requested, name, skipped)
        skipped[name] = reason
    return Choice(kind, requested, BUILTIN[kind], skipped)


def capabilities(probe_worker: bool = False) -> dict[str, Any]:
    from .worker import selftest, vision_python

    settings = load_settings().vision
    worker = selftest(refresh=probe_worker) if (probe_worker or vision_python()) else {"installed": False}
    backends = {kind: {name: unavailable_reason(kind, name) or "ok" for name in chain} for kind, chain in CHAINS.items()}
    return {
        "configured": {k: getattr(settings, k) for k in ("cuts", "subject", "geometry", "camera_motion", "faces")},
        "packages": {p: has_package(p) for p in ("scenedetect", "onnxruntime")},
        "worker": {k: v for k, v in worker.items() if k != "stamp"},
        "models": model_status(),
        "backends": backends,
    }
