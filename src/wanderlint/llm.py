"""Cached, cost-tracked structured-output calls to the Anthropic Messages API.

Responses are cached on disk keyed on everything that determines the output
(prompt version, model, taxonomy version, system, user, schema, effort,
max_tokens, attempt), so re-runs are free and reproducible. The API has no seed
parameter and rejects sampling parameters, so the cache is the reproducibility
mechanism.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from wanderlint.cost import CallRecord, CostTracker, Usage


class LLMError(RuntimeError):
    """The model returned no usable structured output (refusal, truncation, bad JSON)."""


class MessagesAPI(Protocol):
    def create(self, **kwargs: Any) -> Any: ...


class ClientLike(Protocol):
    messages: MessagesAPI


@dataclass(frozen=True)
class LLMResult:
    data: dict[str, Any]
    record: CallRecord


def cache_key(parts: dict[str, Any]) -> str:
    blob = json.dumps(parts, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _usage_from(resp_usage: Any) -> Usage:
    return Usage(
        input_tokens=getattr(resp_usage, "input_tokens", 0) or 0,
        output_tokens=getattr(resp_usage, "output_tokens", 0) or 0,
        cache_read_input_tokens=getattr(resp_usage, "cache_read_input_tokens", 0) or 0,
        cache_creation_input_tokens=getattr(resp_usage, "cache_creation_input_tokens", 0) or 0,
    )


@dataclass
class LLMClient:
    client: ClientLike | None
    cache_dir: Path
    tracker: CostTracker
    taxonomy_version: int

    def complete_json(
        self,
        *,
        purpose: str,
        prompt_version: str,
        model: str,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int,
        effort: str,
        attempt: int = 0,
    ) -> LLMResult:
        key = cache_key(
            {
                "prompt_version": prompt_version,
                "model": model,
                "taxonomy_version": self.taxonomy_version,
                "system": system,
                "user": user,
                "schema": schema,
                "max_tokens": max_tokens,
                "effort": effort,
                "attempt": attempt,
            }
        )
        path = self.cache_dir / f"{key}.json"
        if path.exists():
            cached = json.loads(path.read_text(encoding="utf-8"))
            usage = Usage(**cached["usage"])
            record = CallRecord(
                purpose=purpose,
                model=model,
                usage=usage,
                cost_eur=self.tracker.pricing.cost_eur(model, usage),
                latency_s=cached["latency_s"],
                cached=True,
            )
            self.tracker.record(record)
            return LLMResult(data=cached["data"], record=record)

        if self.client is None:
            raise LLMError("cache miss and no API client configured (dry run?)")

        # ~3 chars per token is a conservative estimate for EN/DE prose + JSON.
        self.tracker.check(model, (len(system) + len(user)) // 3 + 500, max_tokens)

        start = time.monotonic()
        resp = self.client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_config={
                "effort": effort,
                "format": {"type": "json_schema", "schema": schema},
            },
        )
        latency = time.monotonic() - start
        usage = _usage_from(resp.usage)
        record = CallRecord(
            purpose=purpose,
            model=model,
            usage=usage,
            cost_eur=self.tracker.pricing.cost_eur(model, usage),
            latency_s=latency,
            cached=False,
        )
        self.tracker.record(record)

        if resp.stop_reason in ("refusal", "max_tokens"):
            raise LLMError(f"stop_reason={resp.stop_reason}")
        text = next((b.text for b in resp.content if b.type == "text"), None)
        if text is None:
            raise LLMError("no text block in response")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMError(f"invalid JSON: {e}") from e

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"data": data, "usage": asdict(usage), "latency_s": latency},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return LLMResult(data=data, record=record)
