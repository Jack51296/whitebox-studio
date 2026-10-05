"""Content hashes used for lineage, idempotency keys and delivery manifests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def _normalise(value: Any) -> Any:
    if isinstance(value, Path):
        if value.is_file():
            return {"file": value.name, "sha256": sha256_file(value)}
        return {"path": value.as_posix()}
    if isinstance(value, dict):
        return {str(k): _normalise(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_normalise(v) for v in value]
    return value


def stable_hash(value: Any) -> str:
    """Hash of JSON-like data; Path values that point at files contribute their content hash."""
    text = json.dumps(_normalise(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
