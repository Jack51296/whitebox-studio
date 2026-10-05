"""Camera-language vocabulary (taxonomy/camera_language.yaml): framing bands, angles and moves shared by story
shots, director-card labels, shot-grammar checks and prompts."""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Any

import yaml

from .config import REPO_ROOT

VOCABULARY_FILE = REPO_ROOT / "taxonomy" / "camera_language.yaml"


@lru_cache(maxsize=1)
def vocabulary() -> dict[str, Any]:
    with VOCABULARY_FILE.open("r", encoding="utf-8") as stream:
        return yaml.safe_load(stream)


FRAMINGS: tuple[str, ...] = tuple(vocabulary()["framing"])
ANGLES: tuple[str, ...] = tuple(vocabulary()["angle"])
MOVES: tuple[str, ...] = tuple(vocabulary()["move"])


def label(kind: str, key: str) -> str:
    return (vocabulary()[kind].get(key) or {}).get("label", "") if key else ""


def is_figure(actor_kind: str) -> bool:
    return actor_kind in vocabulary()["figure_kinds"]


def framing_of(size: float, actor_kind: str) -> str:
    """Framing band for an unclipped subject size (figure: height/frame height; object: largest side/frame side)."""
    column = "figure" if is_figure(actor_kind) else "object"
    for name, spec in vocabulary()["framing"].items():
        lo, hi = spec[column]
        if size >= lo and (hi is None or size < hi):
            return name
    return FRAMINGS[-1]


def framing_distance(size: float, name: str, actor_kind: str) -> float:
    """How far (log ratio, 0 inside) a measured size lies outside the declared band."""
    lo, hi = vocabulary()["framing"][name]["figure" if is_figure(actor_kind) else "object"]
    if size <= 0:
        return math.inf
    if lo and size < lo:
        return math.log(lo / size)
    if hi is not None and size >= hi:
        return math.log(size / hi)
    return 0.0


def angle_of(pitch_deg: float, roll_deg: float = 0.0) -> str:
    spec = vocabulary()["angle"]
    if abs(roll_deg) >= spec["dutch"]["roll_min_deg"]:
        return "dutch"
    for name, band in spec.items():
        if "pitch" in band and band["pitch"][0] <= pitch_deg <= band["pitch"][1]:
            return name
    return "eye"


def prompt_vocabulary() -> str:
    """Compact vocabulary text for planner prompts."""
    v = vocabulary()
    parts = ["framing（景别）：" + "、".join(f"{k}={s['label']}" for k, s in v["framing"].items()),
             "angle（角度）：" + "、".join(f"{k}={s['label']}" for k, s in v["angle"].items()),
             "move（运镜）：" + "、".join(f"{k}={s['label']}" for k, s in v["move"].items())]
    return "\n".join(parts)
