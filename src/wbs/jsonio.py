"""JSON helpers: UTF-8, human readable, atomic writes."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def read_json(path: Path) -> Any:
    with Path(path).open("r", encoding="utf-8-sig") as stream:
        return json.load(stream)


def write_json(path: Path, data: Any) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2, default=_default) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return path


def write_text(path: Path, text: str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def extract_json(text: str) -> Any:
    """Parse the JSON object a model returned, tolerating code fences and surrounding prose."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else stripped
        stripped = stripped.rsplit("```", 1)[0]
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    start = stripped.find("{")
    if start < 0:
        raise ValueError("no JSON object in model output")
    decoder = json.JSONDecoder()
    obj, _ = decoder.raw_decode(stripped[start:])
    return obj


def _default(value: Any) -> Any:
    if isinstance(value, Path):
        return value.as_posix()
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise TypeError(f"not JSON serialisable: {type(value).__name__}")
