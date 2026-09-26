"""Retry and fallback policies for agent LLM calls.

Provides:
  - RetryPolicy   : configurable retry/backoff parameters
  - call_with_retry: wraps any Callable[[], str] with bounded retries,
                     exponential backoff with jitter, and telemetry recording

Design goals:
  - Zero new dependencies (stdlib only)
  - Pluggable: swap policy per agent or per experiment
  - OTEL-aware: attaches retry metadata to the active span
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


# ── Policy ────────────────────────────────────────────────────────────────────

@dataclass
class RetryPolicy:
    """Configuration for retry and fallback behaviour.

    Attributes:
        max_retries:      Maximum number of retries (0 = no retries).
        base_delay_s:     Initial backoff delay in seconds.
        max_delay_s:      Cap on computed backoff delay.
        backoff_factor:   Multiplier applied each retry: delay = base * factor^attempt.
        jitter:           Add random ±50 % jitter so concurrent retries spread out.
        retry_on_empty:   Retry if the LLM returns an empty / stub / error-fallback response.
        retry_on_exception: Retry if the callable raises an exception.
    """

    max_retries: int = 3
    base_delay_s: float = 1.0
    max_delay_s: float = 10.0
    backoff_factor: float = 2.0
    jitter: bool = True
    retry_on_empty: bool = True
    retry_on_exception: bool = True

    # ── Named presets ────────────────────────────────────────────────────────

    @classmethod
    def disabled(cls) -> "RetryPolicy":
        """No retries at all — fail immediately on first error."""
        return cls(max_retries=0, retry_on_empty=False, retry_on_exception=False)

    @classmethod
    def default(cls) -> "RetryPolicy":
        """Balanced: up to 3 retries with gentle exponential backoff."""
        return cls()

    @classmethod
    def aggressive(cls) -> "RetryPolicy":
        """Up to 5 retries with faster backoff — for unreliable or flaky endpoints."""
        return cls(max_retries=5, base_delay_s=0.5, max_delay_s=30.0, backoff_factor=2.5)

    @classmethod
    def from_dict(cls, d: dict) -> "RetryPolicy":
        valid = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in valid})

    def __str__(self) -> str:
        return (
            f"RetryPolicy(max={self.max_retries}, base={self.base_delay_s}s, "
            f"factor={self.backoff_factor}, jitter={self.jitter})"
        )


# ── Error types ───────────────────────────────────────────────────────────────

class RetryError(Exception):
    """Raised when all retries are exhausted without a successful result."""

    def __init__(self, message: str, attempts: int, last_error: Optional[Exception] = None):
        super().__init__(message)
        self.attempts = attempts
        self.last_error = last_error


# ── Helpers ───────────────────────────────────────────────────────────────────

_STUB_PREFIXES = ("[stub-response]", "[llm-error-fallback]")


def _is_empty_or_stub(result: str) -> bool:
    """Return True if the result looks like a stub or error-fallback response."""
    if not result or not result.strip():
        return True
    for prefix in _STUB_PREFIXES:
        if result.startswith(prefix):
            return True
    return False


def _compute_delay(attempt: int, policy: RetryPolicy) -> float:
    """Compute backoff delay for a given attempt index (0-based)."""
    delay = min(policy.base_delay_s * (policy.backoff_factor ** attempt), policy.max_delay_s)
    if policy.jitter:
        delay *= 0.5 + random.random() * 0.5
    return delay


# ── Core function ─────────────────────────────────────────────────────────────

def call_with_retry(
    fn: Callable[[], str],
    policy: RetryPolicy,
    operation_name: str = "llm.call",
    span: Any = None,
) -> Tuple[str, Dict[str, Any]]:
    """Call fn() with retry/backoff according to *policy*.

    Args:
        fn:             Zero-argument callable that returns the LLM response string.
        policy:         RetryPolicy controlling retry behaviour.
        operation_name: Human-readable name for log/span messages.
        span:           Active OTEL span to attach retry attributes to (may be None).

    Returns:
        (result, metadata) where metadata contains:
          - retry_count    : int   — number of extra attempts made
          - fallback_used  : bool  — always False (future: per-model fallback)
          - attempts       : list  — per-attempt detail dicts

    Raises:
        RetryError: when all retries are exhausted without a clean result.
    """
    metadata: Dict[str, Any] = {
        "retry_count": 0,
        "fallback_used": False,
        "attempts": [],
    }

    last_error: Optional[Exception] = None
    result: Optional[str] = None

    for attempt in range(policy.max_retries + 1):
        attempt_info: Dict[str, Any] = {"attempt": attempt, "success": False}

        try:
            result = fn()

            # Check whether we got a usable response
            if policy.retry_on_empty and _is_empty_or_stub(result):
                attempt_info["reason"] = "empty_or_stub_response"
                attempt_info["response_preview"] = (result or "")[:80]
                metadata["attempts"].append(attempt_info)
                if attempt < policy.max_retries:
                    time.sleep(_compute_delay(attempt, policy))
                    metadata["retry_count"] += 1
                continue  # try again

            # Success
            attempt_info["success"] = True
            metadata["attempts"].append(attempt_info)
            _attach_to_span(span, metadata)
            return result, metadata  # type: ignore[return-value]

        except Exception as e:  # noqa: BLE001
            last_error = e
            attempt_info["error"] = str(e)
            attempt_info["error_type"] = type(e).__name__
            metadata["attempts"].append(attempt_info)

            if not policy.retry_on_exception:
                break

            if attempt < policy.max_retries:
                time.sleep(_compute_delay(attempt, policy))
                metadata["retry_count"] += 1
            continue

    # All attempts exhausted — if we have a stub-level result, return it rather than raising
    if result is not None:
        # Stub/empty but we've run out of retries — return best effort
        _attach_to_span(span, metadata, exhausted=True)
        return result, metadata

    _attach_to_span(span, metadata, exhausted=True)
    raise RetryError(
        f"All {policy.max_retries + 1} attempt(s) failed for '{operation_name}'",
        attempts=policy.max_retries + 1,
        last_error=last_error,
    )


def _attach_to_span(span: Any, metadata: Dict[str, Any], exhausted: bool = False) -> None:
    """Attach retry metadata as attributes on the active OTEL span if provided."""
    if span is None:
        return
    try:
        span.set_attribute("retry.count", metadata["retry_count"])
        span.set_attribute("retry.fallback_used", metadata["fallback_used"])
        if exhausted:
            span.set_attribute("retry.exhausted", True)
    except Exception:  # pragma: no cover
        pass  # never fail due to telemetry


__all__ = ["RetryPolicy", "RetryError", "call_with_retry"]
