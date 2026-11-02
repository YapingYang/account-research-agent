"""Offline rate-limit header parsing, throttling, and same-call retry tests."""

from typing import Any

import pytest
from langchain.agents.middleware import ModelRequest, ModelResponse
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr

from account_research.models.ratelimit import RateLimitMiddleware, RateLimitState


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
