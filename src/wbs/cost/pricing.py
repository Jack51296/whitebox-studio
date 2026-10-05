"""Unit prices from configs/pricing.yaml; unknown prices stay unknown instead of being guessed."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from ..config import load_settings, load_yaml_config


@lru_cache(maxsize=1)
def pricing() -> dict[str, Any]:
    return load_yaml_config(load_settings().cost.pricing_file)


def usd_to_cny() -> float:
    return float(pricing().get("usd_to_cny", 7.0))


def price_for(model: str | None) -> dict[str, Any] | None:
    if not model:
        return None
    return (pricing().get("models") or {}).get(model)


def cost_usd(model: str | None, usage: Any) -> float | None:
    """Return the USD cost of a providers.base.Usage, or None when a needed unit price is missing."""
    prices = price_for(model)
    if prices is None:
        return None
    parts = [
        ("input_per_mtok", max(usage.input_tokens - usage.cached_input_tokens, 0) / 1e6),
        ("cached_input_per_mtok", usage.cached_input_tokens / 1e6),
        ("output_per_mtok", usage.output_tokens / 1e6),
        ("per_image", usage.images),
        ("per_video_second", usage.video_seconds),
    ]
    total = 0.0
    for key, quantity in parts:
        if not quantity:
            continue
        price = prices.get(key)
        if price is None:
            return None
        total += float(price) * quantity
    return round(total, 6)
