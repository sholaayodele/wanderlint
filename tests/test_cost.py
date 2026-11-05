import json
from types import SimpleNamespace

import pytest

from wanderlint.cost import CostCapExceeded, CostTracker, Price, Pricing, Usage
from wanderlint.llm import LLMClient

PRICING = Pricing(
    usd_to_eur=0.9,
    prices={"m": Price(input=2.0, output=10.0, cache_read=0.2, cache_write=2.5)},
)


def test_cost_eur_counts_all_token_kinds() -> None:
    u = Usage(
        input_tokens=1_000_000,
        output_tokens=100_000,
        cache_read_input_tokens=1_000_000,
        cache_creation_input_tokens=0,
    )
    # (2.00 + 1.00 + 0.20) USD * 0.9
    assert PRICING.cost_eur("m", u) == pytest.approx(2.88)


def test_tracker_refuses_call_that_could_exceed_cap() -> None:
    t = CostTracker(pricing=PRICING, cap_eur=0.05)
    t.check("m", input_tokens_estimate=1000, max_tokens=1000)  # worst case ≈ €0.0113
    with pytest.raises(CostCapExceeded):
        t.check("m", input_tokens_estimate=1000, max_tokens=10_000)  # ≈ €0.0923


class FakeMessages:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        assert "temperature" not in kwargs
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps(self.payload))],
            usage=SimpleNamespace(input_tokens=100, output_tokens=50),
            stop_reason="end_turn",
        )


def test_llm_client_caches_and_tracks_cost(tmp_path) -> None:
    fake = FakeMessages({"ok": True})
    tracker = CostTracker(pricing=PRICING, cap_eur=1.0)
    llm = LLMClient(
        client=SimpleNamespace(messages=fake),
        cache_dir=tmp_path,
        tracker=tracker,
        taxonomy_version=1,
    )
    kwargs = dict(
        purpose="t", prompt_version="v", model="m", system="s", user="u",
        schema={"type": "object"}, max_tokens=100, effort="low",
    )  # fmt: skip
    a = llm.complete_json(**kwargs)
    b = llm.complete_json(**kwargs)
    assert a.data == b.data == {"ok": True}
    assert fake.calls == 1
    assert b.record.cached and not a.record.cached
    assert tracker.total_eur == pytest.approx(a.record.cost_eur)  # cached hit costs nothing new
    llm.complete_json(**{**kwargs, "attempt": 1})  # different key -> new call
    assert fake.calls == 2
