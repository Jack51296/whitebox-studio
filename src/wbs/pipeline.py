"""Step runner: idempotent resume keyed by (job, step, input hash) plus bounded retries."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from .errors import TransientError
from .hashing import stable_hash
from .ledger import Ledger
from .log import get_logger

log = get_logger("pipeline")


def run_step(ledger: Ledger, job_key: str, step: str, fn: Callable[[], dict[str, Any] | None], *,
             inputs: Any, run_id: str | None = None, force: bool = False, retries: int = 2,
             backoff_s: float = 2.0) -> dict[str, Any]:
    """Run ``fn`` once per distinct ``inputs``; a succeeded step with the same input hash is skipped.

    Only :class:`TransientError` is retried (network timeouts, rate limits). Anything else is recorded
    as failed and re-raised, so a later run resumes from this step.
    """
    input_hash = stable_hash(inputs)
    previous = ledger.get_step(job_key, step)
    if previous and previous.status == "succeeded" and previous.input_hash == input_hash and not force:
        log.debug("skip %s %s (already succeeded with identical inputs)", job_key, step)
        return previous.output or {}

    attempt = 0
    while True:
        attempt += 1
        ledger.begin_step(job_key, step, input_hash, run_id)
        try:
            output = fn() or {}
        except TransientError as exc:
            ledger.fail_step(job_key, step, f"transient: {exc}")
            if attempt > retries:
                raise
            delay = backoff_s * 2 ** (attempt - 1)
            log.warning("%s %s transient failure (%s); retry %d/%d in %.0fs", job_key, step, exc, attempt,
                        retries, delay)
            time.sleep(delay)
            continue
        except BaseException as exc:
            ledger.fail_step(job_key, step, f"{type(exc).__name__}: {exc}")
            raise
        ledger.succeed_step(job_key, step, output)
        return output
