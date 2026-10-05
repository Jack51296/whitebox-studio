"""Provider interfaces shared by LLM, image and video adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Usage:
    input_tokens: int = 0          # total prompt tokens, cached ones included
    cached_input_tokens: int = 0   # subset of input_tokens served from cache
    output_tokens: int = 0
    images: int = 0
    video_seconds: float = 0.0


@dataclass
class CallContext:
    job_key: str
    step: str
    batch_id: str | None = None


@dataclass
class LLMResult:
    text: str
    usage: Usage
    model: str
    request_id: str | None = None
    simulated: bool = False        # True for mock output or dry-run placeholders


@dataclass
class ImageResult:
    path: Path
    usage: Usage
    model: str
    request_id: str | None = None
    simulated: bool = False


@dataclass
class SubmitResult:
    status: str                    # exported | submitted | failed
    message: str
    remote_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


def estimate_tokens(text: str) -> int:
    """Conservative token estimate for mixed Chinese/English text (≈1 token per CJK char)."""
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return cjk + (len(text) - cjk) // 3 + 1


class LLMProvider(ABC):
    name: str = "llm"
    model: str | None = None
    is_paid: bool = False
    max_output_tokens: int = 16000

    @abstractmethod
    def complete(self, *, system: str, user: str, images: list[Path] | None = None, json_mode: bool = False,
                 task: str | None = None, payload: dict[str, Any] | None = None) -> LLMResult: ...

    def estimate(self, *, system: str, user: str, images: list[Path] | None = None) -> Usage:
        prompt = estimate_tokens(system) + estimate_tokens(user) + 1100 * len(images or [])
        return Usage(input_tokens=prompt, output_tokens=min(self.max_output_tokens, 6000))


class ImageProvider(ABC):
    name: str = "image"
    model: str | None = None
    is_paid: bool = False

    @abstractmethod
    def generate(self, *, prompt: str, input_files: list[Path], out_path: Path, image_id: str) -> ImageResult: ...

    def estimate(self) -> Usage:
        return Usage(images=1)


class VideoProvider(ABC):
    name: str = "video"
    model: str | None = None
    is_paid: bool = False

    @abstractmethod
    def submit(self, *, package_dir: Path, manifest: dict[str, Any]) -> SubmitResult: ...
