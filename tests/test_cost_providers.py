from __future__ import annotations

from pathlib import Path

import pytest

from wbs.config import load_settings
from wbs.cost.pricing import cost_usd
from wbs.cost.report import summarize
from wbs.errors import BudgetExceeded, PaidCallNotConfirmed
from wbs.ledger import Ledger
from wbs.providers import CallContext, CallPolicy, GuardedLLM, _Guard, get_providers
from wbs.providers.base import LLMProvider, LLMResult, Usage
from wbs.providers.mock import register_mock


def test_gpt6_astra_prices_reproduce_the_documented_cost():
    # [D7]: cached input 1.966M ≈ $1.97, normal input 147K ≈ $1.47, output 10.3K ≈ $0.51 → ≈ $3.94
    usage = Usage(input_tokens=1_966_000 + 147_000, cached_input_tokens=1_966_000, output_tokens=10_300)
    assert cost_usd("gpt-6-astra", usage) == pytest.approx(3.951, abs=0.02)


def test_unknown_prices_are_not_guessed():
    assert cost_usd("skill-agent-default", Usage(input_tokens=1000, output_tokens=10)) is None
    assert cost_usd("not-in-table", Usage(input_tokens=1)) is None
    assert cost_usd("mock", Usage(input_tokens=10**6, output_tokens=10**6)) == 0


class FakePaidLLM(LLMProvider):
    name = "fake"
    model = "gpt-6-astra"
    is_paid = True

    def __init__(self):
        self.calls = 0

    def complete(self, **kwargs):
        self.calls += 1
        return LLMResult(text="real", usage=Usage(input_tokens=1000, output_tokens=100), model=self.model)


@register_mock("unit_test_task")
def _mock_handler(payload):
    return "mocked:" + payload.get("x", "")


def _guarded(tmp_path, *, confirmed, dry_run, per_job=50.0):
    ledger = Ledger(tmp_path / "l.sqlite")
    provider = FakePaidLLM()
    guard = _Guard(ledger, CallPolicy(confirmed=confirmed, dry_run=dry_run, per_job_cny=per_job, per_batch_cny=1000))
    return GuardedLLM(provider, guard), provider, ledger


def test_paid_call_requires_confirmation(tmp_path):
    llm, provider, _ = _guarded(tmp_path, confirmed=False, dry_run=False)
    with pytest.raises(PaidCallNotConfirmed):
        llm.complete(CallContext("B/j1", "plan", "B"), system="s", user="u")
    assert provider.calls == 0


def test_dry_run_records_estimate_and_uses_mock(tmp_path):
    llm, provider, ledger = _guarded(tmp_path, confirmed=False, dry_run=True)
    result = llm.complete(CallContext("B/j1", "plan", "B"), system="s", user="u", task="unit_test_task",
                          payload={"x": "1"})
    assert result.text == "mocked:1" and result.simulated
    assert provider.calls == 0
    rows = ledger.usage_rows(job_key="B/j1")
    assert len(rows) == 1 and rows[0]["dry_run"] == 1
    assert summarize(ledger)["dry_run_estimates"]["calls"] == 1


def test_confirmed_call_is_recorded_and_budget_enforced(tmp_path):
    llm, provider, ledger = _guarded(tmp_path, confirmed=True, dry_run=False, per_job=5.0)
    llm.complete(CallContext("B/j1", "plan", "B"), system="s", user="u")
    assert provider.calls == 1
    assert ledger.spend_usd(job_key="B/j1") > 0
    with pytest.raises(BudgetExceeded):
        for _ in range(50):
            llm.complete(CallContext("B/j1", "plan", "B"), system="s" * 20000, user="u")


def test_default_providers_are_mock_and_export_only(workspace):
    settings = load_settings()
    providers = get_providers(settings, Ledger(Path(workspace) / "l.sqlite"))
    assert providers.llm.model == "mock"
    assert providers.image.model == "mock"
    assert providers.video.name == "manual"
