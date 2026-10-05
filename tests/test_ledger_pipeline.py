from __future__ import annotations

import pytest

from wbs.errors import TransientError
from wbs.ledger import Ledger
from wbs.pipeline import run_step


def test_step_is_skipped_when_inputs_are_unchanged(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    calls = []

    def work():
        calls.append(1)
        return {"value": len(calls)}

    first = run_step(ledger, "B/j1", "render", work, inputs={"scene": "a"})
    second = run_step(ledger, "B/j1", "render", work, inputs={"scene": "a"})
    assert first == second == {"value": 1}
    assert len(calls) == 1

    third = run_step(ledger, "B/j1", "render", work, inputs={"scene": "b"})
    assert third == {"value": 2}


def test_failed_step_is_recorded_and_resumed(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    state = {"fail": True}

    def work():
        if state["fail"]:
            raise RuntimeError("boom")
        return {"ok": True}

    with pytest.raises(RuntimeError):
        run_step(ledger, "B/j1", "qc", work, inputs={})
    record = ledger.get_step("B/j1", "qc")
    assert record.status == "failed" and "boom" in record.error

    state["fail"] = False
    assert run_step(ledger, "B/j1", "qc", work, inputs={}) == {"ok": True}
    assert ledger.get_step("B/j1", "qc").attempts == 2


def test_transient_errors_are_retried_with_a_bound(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise TransientError("429")
        return {"done": True}

    assert run_step(ledger, "B/j1", "llm", flaky, inputs={}, retries=2, backoff_s=0) == {"done": True}

    attempts.clear()

    def always():
        attempts.append(1)
        raise TransientError("503")

    with pytest.raises(TransientError):
        run_step(ledger, "B/j2", "llm", always, inputs={}, retries=1, backoff_s=0)
    assert len(attempts) == 2


def test_reviews_keep_latest_value(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    ledger.add_review("B/j1", "normal_speed_viewing", "failed", "alice", "节奏拖")
    ledger.add_review("B/j1", "normal_speed_viewing", "passed", "bob")
    assert ledger.reviews("B/j1")["normal_speed_viewing"]["value"] == "passed"
