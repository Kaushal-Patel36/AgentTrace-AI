"""Unit tests for RetryPolicy and call_with_retry."""

import time
import pytest

from src.agent.retry import RetryPolicy, RetryError, call_with_retry, _is_empty_or_stub


# ── RetryPolicy tests ─────────────────────────────────────────────────────────

class TestRetryPolicy:
    def test_default_has_3_retries(self):
        p = RetryPolicy.default()
        assert p.max_retries == 3

    def test_disabled_has_0_retries(self):
        p = RetryPolicy.disabled()
        assert p.max_retries == 0
        assert p.retry_on_empty is False
        assert p.retry_on_exception is False

    def test_aggressive_has_5_retries(self):
        p = RetryPolicy.aggressive()
        assert p.max_retries == 5

    def test_from_dict(self):
        p = RetryPolicy.from_dict({"max_retries": 7, "jitter": False})
        assert p.max_retries == 7
        assert p.jitter is False

    def test_from_dict_ignores_unknown_keys(self):
        p = RetryPolicy.from_dict({"max_retries": 2, "nonexistent": "ignored"})
        assert p.max_retries == 2

    def test_str_representation(self):
        p = RetryPolicy(max_retries=3)
        s = str(p)
        assert "max=3" in s


# ── Stub detection tests ──────────────────────────────────────────────────────

class TestStubDetection:
    def test_detects_stub_prefix(self):
        assert _is_empty_or_stub("[stub-response] something")

    def test_detects_error_prefix(self):
        assert _is_empty_or_stub("[llm-error-fallback] error")

    def test_detects_empty_string(self):
        assert _is_empty_or_stub("")

    def test_detects_whitespace_only(self):
        assert _is_empty_or_stub("   ")

    def test_real_response_not_stub(self):
        assert not _is_empty_or_stub("This is a real response from the model.")

    def test_partial_stub_prefix_not_detected(self):
        assert not _is_empty_or_stub("Some text [stub] in the middle")


# ── call_with_retry tests ─────────────────────────────────────────────────────

class TestCallWithRetry:
    def test_success_on_first_attempt(self):
        fn = lambda: "real response"
        result, meta = call_with_retry(fn, RetryPolicy.disabled(), "test")
        assert result == "real response"
        assert meta["retry_count"] == 0

    def test_raises_on_all_failures_disabled(self):
        fn = lambda: (_ for _ in ()).throw(RuntimeError("always fails"))

        def always_fails():
            raise RuntimeError("always fails")

        p = RetryPolicy(max_retries=0, retry_on_exception=True)
        with pytest.raises(RetryError) as exc_info:
            call_with_retry(always_fails, p, "test")
        assert exc_info.value.attempts == 1

    def test_retries_on_exception(self):
        call_count = [0]

        def flaky():
            call_count[0] += 1
            if call_count[0] < 3:
                raise RuntimeError("transient")
            return "success"

        p = RetryPolicy(max_retries=5, base_delay_s=0.0, retry_on_exception=True, jitter=False)
        result, meta = call_with_retry(flaky, p, "test")
        assert result == "success"
        assert meta["retry_count"] == 2
        assert call_count[0] == 3

    def test_retries_on_stub_response(self):
        call_count = [0]

        def returns_stub_then_real():
            call_count[0] += 1
            if call_count[0] < 2:
                return "[stub-response] placeholder"
            return "real answer"

        p = RetryPolicy(max_retries=3, base_delay_s=0.0, retry_on_empty=True, jitter=False)
        result, meta = call_with_retry(returns_stub_then_real, p, "test")
        assert result == "real answer"
        assert meta["retry_count"] == 1

    def test_returns_stub_when_retries_exhausted(self):
        """When retries are exhausted but we have a stub result, return it (best effort)."""
        fn = lambda: "[stub-response] always stub"
        p = RetryPolicy(max_retries=2, base_delay_s=0.0, retry_on_empty=True, jitter=False)
        result, meta = call_with_retry(fn, p, "test")
        assert result == "[stub-response] always stub"
        assert meta["retry_count"] == 2

    def test_raises_retry_error_when_always_failing(self):
        """When always raising exceptions, RetryError is raised after exhaustion."""
        def always_fails():
            raise ValueError("always")

        p = RetryPolicy(max_retries=2, base_delay_s=0.0, retry_on_exception=True, jitter=False)
        with pytest.raises(RetryError) as exc_info:
            call_with_retry(always_fails, p, "test")
        assert isinstance(exc_info.value.last_error, ValueError)

    def test_no_retry_on_exception_when_disabled(self):
        call_count = [0]

        def fails_once():
            call_count[0] += 1
            raise RuntimeError("fail")

        p = RetryPolicy(max_retries=5, retry_on_exception=False)
        with pytest.raises(RetryError):
            call_with_retry(fails_once, p, "test")
        assert call_count[0] == 1  # only tried once

    def test_metadata_attempts_list_populated(self):
        call_count = [0]

        def flaky():
            call_count[0] += 1
            if call_count[0] < 2:
                raise RuntimeError("transient")
            return "ok"

        p = RetryPolicy(max_retries=3, base_delay_s=0.0, jitter=False)
        _, meta = call_with_retry(flaky, p, "test")
        assert len(meta["attempts"]) >= 2  # one failure + one success

    def test_fallback_used_defaults_false(self):
        result, meta = call_with_retry(lambda: "real", RetryPolicy.disabled(), "test")
        assert meta["fallback_used"] is False

    def test_attaches_to_span(self):
        """Retry metadata should be set on the provided span object."""
        class FakeSpan:
            def __init__(self):
                self.attrs = {}
            def set_attribute(self, k, v):
                self.attrs[k] = v

        span = FakeSpan()
        call_with_retry(lambda: "ok", RetryPolicy.disabled(), "test", span=span)
        assert "retry.count" in span.attrs
