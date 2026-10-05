"""Data contracts (pydantic). scene.json is the single source of truth for building a white model."""

from __future__ import annotations

import json
from pathlib import Path

from .common import CheckStatus
from .scene import Actor, Block, CameraTrack, Event, SceneSpec, Shot, TimeMapSpec

__all__ = ["Actor", "Block", "CameraTrack", "CheckStatus", "Event", "SceneSpec", "Shot", "TimeMapSpec",
           "schema_documents", "export_schemas"]


def schema_documents() -> dict[str, dict]:
    from .longtake import LongTakePlan
    from .story import StoryPlan

    return {"scene": SceneSpec.model_json_schema(by_alias=True),
            "story_plan": StoryPlan.model_json_schema(),
            "longtake_plan": LongTakePlan.model_json_schema()}


def export_schemas(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, doc in schema_documents().items():
        path = out_dir / f"{name}.schema.json"
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        paths.append(path)
    return paths
