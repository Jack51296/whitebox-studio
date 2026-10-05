"""Provider factory with the paid-call guard, budget caps and usage accounting.

Rules (see docs/security-compliance.md):
- real providers are opt-in in configs/providers.yaml; defaults are mock / export-only;
- a real call needs explicit confirmation (--confirm-paid or WBS_CONFIRM_PAID=1);
- --dry-run records estimated usage for every would-be real call and continues with mock output;
- per-job and per-batch caps are checked before each real call; actual usage is written to the ledger.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import Settings, load_yaml_config
from ..cost.pricing import cost_usd, usd_to_cny
from ..errors import BudgetExceeded, PaidCallNotConfirmed
from ..ledger import Ledger
from ..log import get_logger
from .base import (
    CallContext,
    ImageProvider,
    ImageResult,
    LLMProvider,
    LLMResult,
    SubmitResult,
    Usage,
    VideoProvider,
)
from .mock import MockImage, MockLLM
from .openai_compat import OpenAICompatImage, OpenAICompatLLM
from .video import CosmosTransferVideo, HttpGenericVideo, ManualVideo, WanVaceComfyVideo

log = get_logger("providers")


@dataclass
class CallPolicy:
    confirmed: bool
    dry_run: bool
    per_job_cny: float
    per_batch_cny: float


class _Guard:
    def __init__(self, ledger: Ledger, policy: CallPolicy) -> None:
        self.ledger = ledger
        self.policy = policy

    def check(self, ctx: CallContext, provider_name: str, model: str | None, estimate: Usage) -> None:
        if self.policy.dry_run:
            return
        if not self.policy.confirmed:
            raise PaidCallNotConfirmed(
                f"{ctx.job_key} {ctx.step}: 将调用真实接口 {provider_name}/{model}。请先 --dry-run 查看预估，"
                "确认后加 --confirm-paid（或设置 WBS_CONFIRM_PAID=1）再运行")
        est = cost_usd(model, estimate)
        est_cny = (est or 0.0) * usd_to_cny()
        rate = usd_to_cny()
        job_spent = self.ledger.spend_usd(job_key=ctx.job_key) * rate
        if job_spent + est_cny > self.policy.per_job_cny:
            raise BudgetExceeded(f"{ctx.job_key}: 已花 ¥{job_spent:.2f}，本次预估 ¥{est_cny:.2f}，"
                                 f"超过单条上限 ¥{self.policy.per_job_cny}")
        if ctx.batch_id:
            batch_spent = self.ledger.spend_usd(batch_id=ctx.batch_id) * rate
            if batch_spent + est_cny > self.policy.per_batch_cny:
                raise BudgetExceeded(f"批次 {ctx.batch_id}: 已花 ¥{batch_spent:.2f}，本次预估 ¥{est_cny:.2f}，"
                                     f"超过批次上限 ¥{self.policy.per_batch_cny}")

    def record(self, ctx: CallContext, provider_name: str, model: str | None, usage: Usage,
               request_id: str | None, dry_run: bool) -> None:
        self.ledger.record_usage(
            job_key=ctx.job_key, batch_id=ctx.batch_id, step=ctx.step, provider=provider_name, model=model,
            request_id=request_id, input_tokens=usage.input_tokens, cached_input_tokens=usage.cached_input_tokens,
            output_tokens=usage.output_tokens, images=usage.images, video_seconds=usage.video_seconds,
            cost_usd=cost_usd(model, usage), dry_run=dry_run)


class GuardedLLM:
    def __init__(self, provider: LLMProvider, guard: _Guard) -> None:
        self.provider, self.guard, self._mock = provider, guard, MockLLM()

    @property
    def model(self) -> str | None:
        return self.provider.model

    @property
    def is_real(self) -> bool:
        return self.provider.is_paid

    def complete(self, ctx: CallContext, *, system: str, user: str, images: list[Path] | None = None,
                 json_mode: bool = False, task: str | None = None,
                 payload: dict[str, Any] | None = None) -> LLMResult:
        if not self.provider.is_paid:
            result = self.provider.complete(system=system, user=user, images=images, json_mode=json_mode,
                                            task=task, payload=payload)
            self.guard.record(ctx, self.provider.name, result.model, result.usage, result.request_id, False)
            return result
        estimate = self.provider.estimate(system=system, user=user, images=images)
        if self.guard.policy.dry_run:
            self.guard.record(ctx, self.provider.name, self.provider.model, estimate, None, True)
            log.info("dry-run: %s %s would call %s (~%d input tokens)", ctx.job_key, ctx.step,
                     self.provider.model, estimate.input_tokens)
            return self._mock.complete(system=system, user=user, images=images, json_mode=json_mode,
                                       task=task, payload=payload)
        self.guard.check(ctx, self.provider.name, self.provider.model, estimate)
        result = self.provider.complete(system=system, user=user, images=images, json_mode=json_mode,
                                        task=task, payload=payload)
        self.guard.record(ctx, self.provider.name, result.model, result.usage, result.request_id, False)
        return result


class GuardedImage:
    def __init__(self, provider: ImageProvider, guard: _Guard) -> None:
        self.provider, self.guard, self._mock = provider, guard, MockImage()

    @property
    def model(self) -> str | None:
        return self.provider.model

    def generate(self, ctx: CallContext, *, prompt: str, input_files: list[Path], out_path: Path,
                 image_id: str) -> ImageResult:
        if self.provider.is_paid and self.guard.policy.dry_run:
            self.guard.record(ctx, self.provider.name, self.provider.model, self.provider.estimate(), None, True)
            return self._mock.generate(prompt=prompt, input_files=input_files, out_path=out_path, image_id=image_id)
        if self.provider.is_paid:
            self.guard.check(ctx, self.provider.name, self.provider.model, self.provider.estimate())
        result = self.provider.generate(prompt=prompt, input_files=input_files, out_path=out_path, image_id=image_id)
        self.guard.record(ctx, self.provider.name, result.model, result.usage, result.request_id, False)
        return result


class GuardedVideo:
    def __init__(self, provider: VideoProvider, guard: _Guard) -> None:
        self.provider, self.guard = provider, guard

    @property
    def name(self) -> str:
        return self.provider.name

    def submit(self, ctx: CallContext, *, package_dir: Path, manifest: dict[str, Any],
               video_seconds: float) -> SubmitResult:
        estimate = Usage(video_seconds=video_seconds)
        if self.provider.is_paid and self.guard.policy.dry_run:
            self.guard.record(ctx, self.provider.name, self.provider.model, estimate, None, True)
            return SubmitResult(status="dry_run", message="dry-run：未提交")
        if self.provider.is_paid:
            self.guard.check(ctx, self.provider.name, self.provider.model, estimate)
        result = self.provider.submit(package_dir=package_dir, manifest=manifest)
        if self.provider.is_paid and result.status == "submitted":
            self.guard.record(ctx, self.provider.name, self.provider.model, estimate, result.remote_id, False)
        return result


@dataclass
class Providers:
    llm: GuardedLLM
    image: GuardedImage
    video: GuardedVideo
    policy: CallPolicy


def _build(kind: str, cfg: dict[str, Any]) -> Any:
    name = cfg.get("provider", "mock")
    if kind == "llm":
        return MockLLM() if name == "mock" else OpenAICompatLLM(cfg)
    if kind == "image":
        if name == "mock":
            width, height = (int(v) for v in str(cfg.get("size", "1536x864")).split("x"))
            return MockImage((width, height))
        return OpenAICompatImage(cfg)
    if name == "manual":
        return ManualVideo(cfg)
    if name == "cosmos_transfer":
        return CosmosTransferVideo(cfg)
    if name == "wan_vace":
        return WanVaceComfyVideo(cfg)
    return HttpGenericVideo(cfg)


def get_providers(settings: Settings, ledger: Ledger, *, confirmed: bool = False, dry_run: bool = False) -> Providers:
    cfg = load_yaml_config(settings.providers_file)
    confirmed = confirmed or os.environ.get("WBS_CONFIRM_PAID") == "1" or not settings.require_paid_confirmation
    policy = CallPolicy(confirmed=confirmed, dry_run=dry_run, per_job_cny=settings.cost.budget.per_job_cny,
                        per_batch_cny=settings.cost.budget.per_batch_cny)
    guard = _Guard(ledger, policy)
    return Providers(llm=GuardedLLM(_build("llm", cfg.get("llm", {})), guard),
                     image=GuardedImage(_build("image", cfg.get("image", {})), guard),
                     video=GuardedVideo(_build("video", cfg.get("video", {})), guard),
                     policy=policy)


__all__ = ["CallContext", "Providers", "get_providers", "LLMResult", "ImageResult", "SubmitResult", "Usage"]
