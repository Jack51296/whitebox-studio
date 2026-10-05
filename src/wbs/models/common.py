"""Shared status vocabulary. A check that was not actually performed is always ``not_run``."""

from __future__ import annotations

from enum import StrEnum


class CheckStatus(StrEnum):
    passed = "passed"
    failed = "failed"
    not_run = "not_run"
    skipped = "skipped"
    pending = "pending"


def combine(*statuses: str) -> str:
    """failed dominates, then not_run; passed only when every input passed (skipped inputs ignored)."""
    values = [s.value if isinstance(s, CheckStatus) else s for s in statuses if s not in (None, "skipped")]
    if not values:
        return CheckStatus.not_run.value
    if "failed" in values:
        return CheckStatus.failed.value
    if all(v == "passed" for v in values):
        return CheckStatus.passed.value
    return CheckStatus.not_run.value
