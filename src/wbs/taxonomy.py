"""Video structure tree ([D2]) and reproducible, constraint-aware sampling of control specs."""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any

import yaml

from .config import REPO_ROOT

TAXONOMY_FILE = REPO_ROOT / "taxonomy" / "structure_tree.yaml"
DIMENSIONS = ("shot_form", "subject", "subject_child", "color", "era", "viewpoint", "camera_move", "content_class")


@lru_cache(maxsize=1)
def taxonomy() -> dict[str, Any]:
    with TAXONOMY_FILE.open("r", encoding="utf-8") as stream:
        return yaml.safe_load(stream)


def _values(dim: str) -> list[str]:
    return list(taxonomy()["dimensions"][dim]["values"])


def _children(subject: str) -> list[str]:
    return list(taxonomy()["dimensions"]["subject"]["values"][subject]["children"])


@dataclass(frozen=True)
class ControlSpec:
    index: int
    seed: int
    shot_form: str
    shot_count: int
    subject: str
    subject_child: str
    color: str
    era: str
    viewpoint: str
    camera_move: str
    content_class: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def labels(self) -> dict[str, str]:
        dims = taxonomy()["dimensions"]

        def label(dim: str, key: str) -> str:
            value = dims[dim]["values"][key]
            return value["label"] if isinstance(value, dict) else value

        subject = dims["subject"]["values"][self.subject]
        color_rule = {
            "white": "场景与主体统一使用纯白色体块材质",
            "grey": "场景与主体统一使用灰白色 clay 体块材质",
            "identity": "场景为浅灰体块，主体用不同身份色区分（颜色只作身份识别）",
        }[self.color]
        return {
            "shot_form_label": label("shot_form", self.shot_form),
            "subject_label": f"{subject['label']}·{subject['children'][self.subject_child]}",
            "color_label": label("color", self.color),
            "era_label": label("era", self.era),
            "viewpoint_label": label("viewpoint", self.viewpoint),
            "camera_move_label": label("camera_move", self.camera_move),
            "content_class_label": label("content_class", self.content_class),
            "color_rule": color_rule,
        }


def _violates(values: dict[str, Any]) -> bool:
    for rule in taxonomy().get("constraints", []):
        when = rule.get("when", {})
        if not all(values.get(k) == v or (isinstance(v, list) and values.get(k) in v) for k, v in when.items()):
            continue
        for dim, banned in (rule.get("exclude") or {}).items():
            if values.get(dim) in banned:
                return True
        for dim, allowed in (rule.get("require") or {}).items():
            if values.get(dim) not in allowed:
                return True
    return False


def sample_controls(n: int, seed: int, narrative_ratio: float = 0.5, subjects: list[str] | None = None,
                    max_tries: int = 500) -> list[ControlSpec]:
    """Draw ``n`` control specs: subjects stratified evenly, content class by quota, constraints enforced."""
    if n <= 0:
        return []
    rng = random.Random(seed)
    subject_pool = subjects or _values("subject")
    unknown = set(subject_pool) - set(_values("subject"))
    if unknown:
        raise ValueError(f"unknown subject(s): {sorted(unknown)}")
    order = [subject_pool[i % len(subject_pool)] for i in range(n)]
    rng.shuffle(order)
    narrative = round(n * narrative_ratio)
    classes = ["narrative"] * narrative + ["motion"] * (n - narrative)
    rng.shuffle(classes)

    specs: list[ControlSpec] = []
    for i in range(n):
        for _ in range(max_tries):
            values = {
                "subject": order[i],
                "content_class": classes[i],
                "shot_form": rng.choice(_values("shot_form")),
                "color": rng.choice(_values("color")),
                "era": rng.choice(_values("era")),
                "viewpoint": rng.choice(_values("viewpoint")),
                "camera_move": rng.choice(_values("camera_move")),
            }
            values["subject_child"] = rng.choice(_children(values["subject"]))
            if values["camera_move"] == "one_take":
                values["shot_form"] = "long_take"
            if not _violates(values):
                break
        else:
            raise RuntimeError(f"could not satisfy taxonomy constraints for item {i}")
        lo, hi = taxonomy()["dimensions"]["shot_form"]["values"]["multi_shot"]["shot_count"]
        shot_count = rng.randint(lo, hi) if values["shot_form"] == "multi_shot" else 1
        specs.append(ControlSpec(index=i + 1, seed=rng.randrange(1, 2**31), shot_count=shot_count, **values))
    return specs
