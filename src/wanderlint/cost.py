"""Token and EUR cost accounting per LLM call, with a hard per-run cap."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class CostCapExceeded(RuntimeError):
    """Raised before a call that could push the run past its hard cap."""


@dataclass(frozen=True)
class Price:
    """USD per million tokens."""

    input: float
    output: float
    cache_read: float
    cache_write: float


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass(frozen=True)
class Pricing:
    usd_to_eur: float
    prices: dict[str, Price]

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> Pricing:
        p = config["pricing"]
        return cls(
            usd_to_eur=float(p["usd_to_eur"]),
            prices={model: Price(**v) for model, v in p["usd_per_mtok"].items()},
        )

    def price(self, model: str) -> Price:
        if model not in self.prices:
            raise KeyError(f"no pricing for model {model!r} in config.yaml")
        return self.prices[model]

    def cost_eur(self, model: str, usage: Usage) -> float:
        p = self.price(model)
        usd = (
            usage.input_tokens * p.input
            + usage.output_tokens * p.output
            + usage.cache_read_input_tokens * p.cache_read
            + usage.cache_creation_input_tokens * p.cache_write
        ) / 1_000_000
        return usd * self.usd_to_eur

    def worst_case_eur(self, model: str, input_tokens: int, max_tokens: int) -> float:
        """Upper bound for one call: all input uncached-and-written, full max_tokens output."""
        p = self.price(model)
        usd = (input_tokens * max(p.input, p.cache_write) + max_tokens * p.output) / 1_000_000
        return usd * self.usd_to_eur


@dataclass(frozen=True)
class CallRecord:
    purpose: str
    model: str
    usage: Usage
    cost_eur: float
    latency_s: float
    cached: bool


@dataclass
class CostTracker:
    """Accumulates spend for one run and refuses calls that could exceed the cap."""

    pricing: Pricing
    cap_eur: float
    calls: list[CallRecord] = field(default_factory=list)

    @property
    def total_eur(self) -> float:
        return sum(c.cost_eur for c in self.calls if not c.cached)

    def check(self, model: str, input_tokens_estimate: int, max_tokens: int) -> None:
        worst = self.pricing.worst_case_eur(model, input_tokens_estimate, max_tokens)
        if self.total_eur + worst > self.cap_eur:
            raise CostCapExceeded(
                f"next call could cost up to €{worst:.4f}; spent €{self.total_eur:.4f} "
                f"of €{self.cap_eur:.2f} cap"
            )

    def record(self, record: CallRecord) -> None:
        self.calls.append(record)
