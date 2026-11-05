"""Lightweight per-run telemetry for OpenAI-compatible chat completions.

The evaluator must report request counts, token usage and elapsed time without
ever serialising prompt contents or API keys.  The context manager is safe to
use around one question or an entire batch.  It intentionally treats missing
usage as missing data rather than guessing token counts.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any


def _usage_value(usage: Any, name: str) -> int | None:
    if usage is None:
        return None
    value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
    return int(value) if isinstance(value, int | float) else None


@dataclass
class UsageStats:
    """Aggregate telemetry for one question or batch."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    requests: int = 0
    usage_missing_requests: int = 0
    api_elapsed_seconds: float = 0.0
    returned_models: set[str] = field(default_factory=set)

    def add_response(self, response: Any, elapsed_seconds: float) -> None:
        self.requests += 1
        self.api_elapsed_seconds += elapsed_seconds
        returned_model = getattr(response, "model", None)
        if isinstance(returned_model, str):
            self.returned_models.add(returned_model)
        usage = getattr(response, "usage", None)
        prompt = _usage_value(usage, "prompt_tokens")
        completion = _usage_value(usage, "completion_tokens")
        total = _usage_value(usage, "total_tokens")
        if prompt is None or completion is None or total is None:
            self.usage_missing_requests += 1
            return
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.total_tokens += total

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens if self.usage_missing_requests == 0 else None,
            "completion_tokens": self.completion_tokens
            if self.usage_missing_requests == 0
            else None,
            "total_tokens": self.total_tokens if self.usage_missing_requests == 0 else None,
            "known_total_tokens": self.total_tokens,
            "requests": self.requests,
            "usage_missing_requests": self.usage_missing_requests,
            "api_elapsed_seconds": round(self.api_elapsed_seconds, 6),
            "returned_models": sorted(self.returned_models),
            "request_counting": "SDK calls; SDK automatic retries disabled",
        }


@contextmanager
def capture_usage() -> Iterator[UsageStats]:
    """Collect telemetry for calls made through :func:`tracked_completion`."""

    stats = UsageStats()
    token = _CURRENT_STATS.set(stats)
    try:
        yield stats
    finally:
        _CURRENT_STATS.reset(token)


def tracked_completion(client: Any, **kwargs: Any) -> Any:
    """Call ``chat.completions.create`` and record safe response metadata."""

    started = perf_counter()
    try:
        response = client.chat.completions.create(**kwargs)
    except Exception:
        stats = _CURRENT_STATS.get()
        if stats is not None:
            stats.requests += 1
            stats.usage_missing_requests += 1
            stats.api_elapsed_seconds += perf_counter() - started
        raise
    stats = _CURRENT_STATS.get()
    if stats is not None:
        stats.add_response(response, perf_counter() - started)
    return response


_CURRENT_STATS: ContextVar[UsageStats | None] = ContextVar(
    "dynamic_memory_usage_stats", default=None
)
