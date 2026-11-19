"""Offline rate-limit header parsing, throttling, and same-call retry tests."""

from typing import Any

import pytest
from langchain.agents.middleware import ModelRequest, ModelResponse
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr

from account_research.models.ratelimit import (
    DailyQuotaExceeded,
    RateLimitMiddleware,
    RateLimitState,
    RateLimitWaitExceeded,
)


class FakeModel(BaseChatModel):
    """Minimal chat model used only to construct a middleware request."""

    _unused: list[Any] = PrivateAttr(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "test"

    def _generate(
        self, messages: list[Any], stop: list[str] | None = None, **kwargs: Any
    ) -> ChatResult:
        del messages, stop, kwargs
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])


def _request() -> ModelRequest[Any]:
    return ModelRequest(model=FakeModel(), messages=[HumanMessage(content="x" * 400)])


def test_waits_when_estimate_exceeds_header_budget() -> None:
    state = RateLimitState()
    state.capture_headers(
        {
            "x-ratelimit-remaining-tokens": "1",
            "x-ratelimit-reset-tokens": "2.5s",
        }
    )
    slept: list[float] = []
    middleware = RateLimitMiddleware(state, sleeper=slept.append)
    response = middleware.wrap_model_call(
        _request(), lambda _request: ModelResponse(result=[AIMessage(content="done")])
    )
    assert response.result[0].content == "done"
    assert slept == [2.5]
    assert state.wait_count == 1


def test_429_retries_same_request_after_retry_after() -> None:
    state = RateLimitState()
    slept: list[float] = []
    middleware = RateLimitMiddleware(state, sleeper=slept.append)
    request = _request()
    seen: list[ModelRequest[Any]] = []

    def handler(current: ModelRequest[Any]) -> ModelResponse[Any]:
        seen.append(current)
        if len(seen) == 1:

            class RateError(RuntimeError):
                response = type("Response", (), {"headers": {"retry-after": "3"}})()

            raise RateError("429 rate limit")
        return ModelResponse(result=[AIMessage(content="ok")])

    assert middleware.wrap_model_call(request, handler).result[0].content == "ok"
    assert seen == [request, request]
    assert slept == [3.0]


def test_gives_up_after_configured_rate_limit_waits() -> None:
    state = RateLimitState()
    middleware = RateLimitMiddleware(state, max_rate_limit_waits=2, sleeper=lambda _s: None)
    request = _request()

    def handler(_request: ModelRequest[Any]) -> ModelResponse[Any]:
        raise RuntimeError("429 rate limit")

    with pytest.raises(RuntimeError, match="wait budget exhausted"):
        middleware.wrap_model_call(request, handler)
    assert state.wait_count == 2


def test_total_wait_cap_fails_fast_with_limit_and_reset() -> None:
    state = RateLimitState()
    state.capture_headers(
        {
            "x-ratelimit-remaining-tokens": "1",
            "x-ratelimit-reset-tokens": "1800s",
        }
    )
    slept: list[float] = []
    middleware = RateLimitMiddleware(
        state,
        max_total_wait_seconds=1,
        sleeper=slept.append,
    )
    with pytest.raises(RateLimitWaitExceeded, match="tokens/min.*reset time 1800s"):
        middleware.wrap_model_call(
            _request(), lambda _request: ModelResponse(result=[AIMessage(content="never")])
        )
    assert slept == []
    assert state.latest_headers["x-ratelimit-reset-tokens"] == "1800s"


def test_wait_status_names_request_and_daily_quotas() -> None:
    state = RateLimitState()
    state.capture_headers({"x-ratelimit-remaining-requests": "0"})
    assert state.classify() == "requests/min"
    state.capture_headers({"x-ratelimit-remaining-tokens-per-day": "0"})
    assert state.classify() == "tokens/day"


def test_unknown_headers_skip_throttling() -> None:
    state = RateLimitState()
    state.capture_headers({"x-ratelimit-unknown-header": "not-a-limit"})
    assert state.remaining_requests() is None
    assert state.remaining_tokens is None
    assert state.classify() == "unknown"

    slept: list[float] = []
    middleware = RateLimitMiddleware(state, sleeper=slept.append)
    response = middleware.wrap_model_call(
        _request(), lambda _request: ModelResponse(result=[AIMessage(content="done")])
    )
    assert response.result[0].content == "done"
    assert slept == []


def test_daily_quota_error_stops_without_retry_or_wait() -> None:
    state = RateLimitState()
    slept: list[float] = []
    middleware = RateLimitMiddleware(state, sleeper=slept.append)

    def handler(_request: ModelRequest[Any]) -> ModelResponse[Any]:
        raise RuntimeError("429 daily token quota exceeded")

    with pytest.raises(DailyQuotaExceeded, match="Daily token quota exhausted"):
        middleware.wrap_model_call(_request(), handler)
    assert slept == []


def test_extracts_headers_from_wrapped_exception() -> None:
    state = RateLimitState()

    class InnerError(RuntimeError):
        def __init__(self) -> None:
            self.response = type("Response", (), {"headers": {"retry-after": "1.5s"}})()

    exc = RuntimeError("429 rate limit")
    exc.__cause__ = InnerError()  # type: ignore[attr-defined]

    state.capture_exception(exc)

    assert state.headers["retry-after"] == "1.5s"
    assert state.reset_seconds == 1.5


def test_unknown_reset_uses_exponential_backoff() -> None:
    state = RateLimitState()
    slept: list[float] = []
    middleware = RateLimitMiddleware(state, max_rate_limit_waits=3, sleeper=slept.append)
    request = _request()

    def handler(_request: ModelRequest[Any]) -> ModelResponse[Any]:
        raise RuntimeError("429 rate limit")

    with pytest.raises(RuntimeError, match="Rate-limit wait budget exhausted"):
        middleware.wrap_model_call(request, handler)
    assert slept == [2.0, 4.0, 8.0]


def test_out_of_credit_fails_fast_without_waiting() -> None:
    from account_research.models.ratelimit import (
        ModelUnavailableError,
        RateLimitMiddleware,
        RateLimitState,
    )

    from types import SimpleNamespace

    waits: list[float] = []
    middleware = RateLimitMiddleware(RateLimitState(), 6, 300, 1500)
    request = SimpleNamespace(system_message=None, system_prompt="", messages=[], tools=[])

    def handler(_request: object) -> object:
        raise RuntimeError("Error code: 429 - {'error': {'code': 'credit_balance_exhausted'}}")

    with pytest.raises(ModelUnavailableError, match="out of credit"):
        middleware.wrap_model_call(request, handler)  # type: ignore[arg-type]
    assert waits == []
