"""Prompt library: Markdown files with YAML front matter, either verbatim source text or Jinja2 templates."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, StrictUndefined

from .config import REPO_ROOT

PROMPTS_DIR = REPO_ROOT / "prompts"
_ENV = Environment(undefined=StrictUndefined, keep_trailing_newline=True, autoescape=False,
                   trim_blocks=False, lstrip_blocks=False)


@dataclass(frozen=True)
class Prompt:
    id: str
    version: str
    kind: str
    title: str
    source: str
    body: str
    path: Path
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"

    def render(self, **variables: Any) -> str:
        if self.kind == "verbatim":
            if variables:
                raise ValueError(f"prompt {self.id} is verbatim and takes no variables")
            return self.body
        text = _ENV.from_string(self.body).render(**variables)
        lines = [line.rstrip() for line in text.strip().splitlines()]
        compact: list[str] = []
        for line in lines:
            if line or (compact and compact[-1]):
                compact.append(line)
        return "\n".join(compact).strip() + "\n"


def _parse(path: Path) -> Prompt:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    if not text.startswith("---\n"):
        raise ValueError(f"{path}: missing YAML front matter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError(f"{path}: unterminated front matter")
    meta = yaml.safe_load(text[4:end]) or {}
    for key in ("id", "version", "kind"):
        if key not in meta:
            raise ValueError(f"{path}: front matter lacks '{key}'")
    if meta["kind"] not in ("verbatim", "template"):
        raise ValueError(f"{path}: kind must be verbatim or template")
    return Prompt(id=str(meta["id"]), version=str(meta["version"]), kind=meta["kind"],
                  title=str(meta.get("title", "")), source=str(meta.get("source", "")),
                  body=text[end + 5:], path=path, meta=meta)


@lru_cache(maxsize=1)
def library() -> dict[str, Prompt]:
    prompts: dict[str, Prompt] = {}
    for path in sorted(PROMPTS_DIR.rglob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        prompt = _parse(path)
        if prompt.id in prompts:
            raise ValueError(f"duplicate prompt id {prompt.id}: {path} and {prompts[prompt.id].path}")
        prompts[prompt.id] = prompt
    return prompts


def get_prompt(prompt_id: str) -> Prompt:
    try:
        return library()[prompt_id]
    except KeyError as exc:
        raise KeyError(f"unknown prompt id '{prompt_id}'") from exc


def render(prompt_id: str, **variables: Any) -> tuple[str, str]:
    """Render a prompt and return (text, "id@version") for lineage records."""
    prompt = get_prompt(prompt_id)
    return prompt.render(**variables), prompt.ref
