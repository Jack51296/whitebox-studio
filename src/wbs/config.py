"""Layered configuration: configs/default.yaml → configs/local.yaml → $WBS_CONFIG → env overrides."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "configs"


class Spec(BaseModel):
    duration_s: float
    fps: int = 24
    resolution: tuple[int, int]
    lens_mm: float | None = None


class StaticGate(BaseModel):
    layout_min_similarity: float = 0.35
    structure_line_max_deg: float = 15.0
    occupancy_max_delta: float = 0.10
    interpenetration_max_m: float = 0.3


class MotionLimits(BaseModel):
    speed: float
    accel: float
    brake: float
    lateral: float
    yaw_rate: float


def _default_kinds() -> dict[str, MotionLimits]:
    from .blender.kinematics import DEFAULT_MOTION_LIMITS

    return {kind: MotionLimits(**values) for kind, values in DEFAULT_MOTION_LIMITS.items()}


class DynamicGate(BaseModel):
    sample_hz: int = 48
    camera_min_clearance_m: float = 0.3
    actor_min_pair_clearance_m: float = 0.05
    kinds: dict[str, MotionLimits] = Field(default_factory=_default_kinds)
    motion_smooth_s: float = 0.1
    motion_hold_s: float = 0.1
    motion_min_speed_mps: float = 1.0

    def limits(self, kind: str) -> MotionLimits:
        return self.kinds.get(kind) or self.kinds.get("pawn") or _default_kinds()["pawn"]


class Framing(BaseModel):
    min_visible_rate: float = 0.9
    min_unoccluded: float = 0.34


class Readability(BaseModel):
    setup_min_width_fraction: float = 0.05
    setup_min_unoccluded: float = 0.5
    enforce: bool = False


class Grammar(BaseModel):
    jump_cut_min_angle_deg: float = 30.0
    neutral_axis_deg: float = 20.0
    max_close_share: float = 0.5
    enforce: bool = False


class QC(BaseModel):
    static_gate: StaticGate = Field(default_factory=StaticGate)
    dynamic_gate: DynamicGate = Field(default_factory=DynamicGate)
    framing: Framing = Field(default_factory=Framing)
    readability: Readability = Field(default_factory=Readability)
    grammar: Grammar = Field(default_factory=Grammar)
    diversity_threshold: float = 0.3


class Dressing(BaseModel):
    enabled: bool = True
    posts: bool = True
    post_spacing_m: float = 25.0
    post_min_speed_mps: float = 5.0
    lane_lines: bool = True
    containers: bool = True
    floor_lines: bool = True
    floor_height_m: float = 3.2


class Forward(BaseModel):
    dressing: Dressing = Field(default_factory=Dressing)


class VideoEncode(BaseModel):
    codec: str = "libx264"
    crf: int = 18
    preset: str = "medium"
    pix_fmt: str = "yuv420p"


class Render(BaseModel):
    engine: str = "workbench"
    timeout_s: int = 3600
    video: VideoEncode = Field(default_factory=VideoEncode)


class Budget(BaseModel):
    per_job_cny: float = 50.0
    per_batch_cny: float = 2000.0


class Cost(BaseModel):
    pricing_file: str = "pricing.yaml"
    budget: Budget = Field(default_factory=Budget)


class Tools(BaseModel):
    blender: str | None = None
    ffmpeg: str | None = None
    ffprobe: str | None = None
    face_model: str | None = None


class Vision(BaseModel):
    models_dir: str = "models"
    python: str | None = None
    worker_timeout_s: int = 1800
    cuts: str = "builtin"
    subject: str = "motion"
    geometry: str = "heuristic"
    camera_motion: str = "rules"
    faces: list[str] = Field(default_factory=lambda: ["yunet"])


class Settings(BaseModel):
    workspace: Path
    specs: dict[str, Spec]
    precision: str = "low"
    fbx_library: str | None = None
    render: Render = Field(default_factory=Render)
    qc: QC = Field(default_factory=QC)
    forward: Forward = Field(default_factory=Forward)
    providers_file: str = "providers.yaml"
    cost: Cost = Field(default_factory=Cost)
    require_paid_confirmation: bool = True
    tools: Tools = Field(default_factory=Tools)
    vision: Vision = Field(default_factory=Vision)

    def spec(self, name: str) -> Spec:
        if name not in self.specs:
            raise KeyError(f"unknown spec '{name}', configured: {sorted(self.specs)}")
        return self.specs[name]


def _deep_merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as stream:
        data = yaml.safe_load(stream) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a mapping")
    return data


def config_file(name: str) -> Path:
    """Resolve a config file name relative to configs/ (absolute paths pass through)."""
    path = Path(name)
    return path if path.is_absolute() else CONFIG_DIR / path


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    data = _read_yaml(CONFIG_DIR / "default.yaml")
    local = CONFIG_DIR / "local.yaml"
    if local.exists():
        data = _deep_merge(data, _read_yaml(local))
    if os.environ.get("WBS_CONFIG"):
        data = _deep_merge(data, _read_yaml(Path(os.environ["WBS_CONFIG"])))

    if os.environ.get("WBS_WORKSPACE"):
        data["workspace"] = os.environ["WBS_WORKSPACE"]
    tools = data.setdefault("tools", {}) or {}
    for key in ("blender", "ffmpeg", "ffprobe", "face_model"):
        env = os.environ.get(f"WBS_{key.upper()}")
        if env:
            tools[key] = env
    data["tools"] = tools
    if os.environ.get("WBS_VISION_PYTHON"):
        data.setdefault("vision", {})["python"] = os.environ["WBS_VISION_PYTHON"]

    workspace = Path(data.get("workspace", "./workspace"))
    data["workspace"] = workspace if workspace.is_absolute() else (REPO_ROOT / workspace).resolve()
    return Settings.model_validate(data)


def load_yaml_config(name: str) -> dict[str, Any]:
    return _read_yaml(config_file(name))
