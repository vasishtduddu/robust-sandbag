"""Bounded-context policy for AutoDAN-Turbo attacker generations.

The released Hugging Face wrapper treats ``max_length`` as a total sequence
limit. Retrieved strategy examples can make the already-tokenized input exceed
that limit before generation starts. This module keeps the total-length budget
fixed while making that edge case deterministic and auditable.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any


AUTODAN_CONTEXT_POLICY = "total_4096_prune_retrieval_then_warmup_v1"


class AutoDANContextLimitError(ValueError):
    """An attacker input leaves no room under its total generation limit."""

    def __init__(self, input_tokens: int, total_length_limit: int) -> None:
        self.input_tokens = input_tokens
        self.total_length_limit = total_length_limit
        super().__init__(
            f"AutoDAN attacker input has {input_tokens} tokens, which does not "
            f"fit below total max_length={total_length_limit}."
        )


def validate_total_length(input_tokens: int, total_length_limit: int) -> None:
    """Require at least one token of generation room under ``max_length``."""
    if input_tokens >= total_length_limit:
        raise AutoDANContextLimitError(input_tokens, total_length_limit)


@dataclass
class AutoDANContextDiagnostics:
    """Aggregate context-policy activations for one evaluation split."""

    total_length_limit: int = 4096
    overflow_events: int = 0
    strategies_pruned: int = 0
    warmup_fallbacks: int = 0
    direct_request_fallbacks: int = 0
    max_overflow_input_tokens: int = 0

    def record_overflow(self, error: AutoDANContextLimitError) -> None:
        self.overflow_events += 1
        self.total_length_limit = error.total_length_limit
        self.max_overflow_input_tokens = max(
            self.max_overflow_input_tokens,
            error.input_tokens,
        )

    def as_result_fields(self) -> dict[str, str | int]:
        return {
            "autodan_context_policy": AUTODAN_CONTEXT_POLICY,
            "autodan_context_total_length_limit": self.total_length_limit,
            "autodan_context_overflow_events": self.overflow_events,
            "autodan_context_strategies_pruned": self.strategies_pruned,
            "autodan_context_warmup_fallbacks": self.warmup_fallbacks,
            "autodan_context_direct_request_fallbacks": self.direct_request_fallbacks,
            "autodan_context_max_overflow_input_tokens": self.max_overflow_input_tokens,
        }


def bounded_strategy_generation(
    request: str,
    strategies: Sequence[dict[str, Any]],
    *,
    generate_with_strategies: Callable[[list[dict[str, Any]]], tuple[str, str]],
    generate_warmup: Callable[[], tuple[str, str]],
    diagnostics: AutoDANContextDiagnostics,
    logger: Any = None,
) -> tuple[str, str, list[dict[str, Any]]]:
    """Generate under the fixed budget, pruning least-priority strategies.

    Retrieval returns strategies in priority order. The tail is therefore
    removed first. If even the highest-priority strategy is too large, use the
    ordinary strategy-free AutoDAN warm-up attacker. A pathologically long
    warm-up input falls back to the original request rather than invalidating
    the entire benchmark cell.
    """
    remaining = list(strategies)
    had_strategies = bool(remaining)
    while remaining:
        try:
            prompt, attacker_system = generate_with_strategies(remaining)
            return prompt, attacker_system, remaining
        except AutoDANContextLimitError as error:
            diagnostics.record_overflow(error)
            removed = remaining.pop()
            diagnostics.strategies_pruned += 1
            if logger is not None:
                logger.warning(
                    "AutoDAN context policy pruned strategy %r after a %d-token "
                    "input exceeded total max_length=%d.",
                    removed.get("Strategy", "<unnamed>"),
                    error.input_tokens,
                    error.total_length_limit,
                )

    if had_strategies:
        diagnostics.warmup_fallbacks += 1
    try:
        prompt, attacker_system = generate_warmup()
        return prompt, attacker_system, []
    except AutoDANContextLimitError as error:
        diagnostics.record_overflow(error)
        diagnostics.direct_request_fallbacks += 1
        if logger is not None:
            logger.warning(
                "AutoDAN warm-up input also exceeded total max_length=%d; "
                "using the original request for this candidate.",
                error.total_length_limit,
            )
        return request, "AutoDAN direct-request context fallback", []
