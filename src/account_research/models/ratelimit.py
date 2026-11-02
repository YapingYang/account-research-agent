"""Groq rate-limit header capture and synchronous model-call throttling."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse


class ModelUnavailableError(RuntimeError):
    """Model was not found or returned repeated server errors."""


def _seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        total = 0.0
        matches = re.findall(r"(\d+(?:\.\d+)?)(ms|h|m|s)", value)
        if not matches:
            return None
        for amount, unit in matches:
            total += float(amount) * {"h": 3600, "m": 60, "s": 1, "ms": 0.001}[unit]
        return total


def _headers(error: BaseException) -> dict[str, str]:
    response = getattr(error, "response", None)
    raw = getattr(response, "headers", {}) or {}
    return {str(k).lower(): str(v) for k, v in raw.items()}


class RateLimitState:
    """Store the latest Groq rate-limit headers and wait diagnostics."""

    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.wait_count = 0
        self.wait_seconds = 0.0
        self.last_status = ""

    def capture_response(self, response: Any) -> None:
        self.capture_headers(response.headers)
        self.last_status = str(getattr(response, "status_code", ""))

    def capture_headers(self, headers: Any) -> None:
        self.headers.update(
            {
                str(k).lower(): str(v)
                for k, v in headers.items()
                if str(k).lower().startswith("x-ratelimit-")
            }
        )

    @property
    def remaining_tokens(self) -> int | None:
        value = self.headers.get("x-ratelimit-remaining-tokens")
        return int(value) if value and value.isdigit() else None

    @property
    def reset_seconds(self) -> float | None:
        return _seconds(self.headers.get("x-ratelimit-reset-tokens"))

    @property
    def output_tpm(self) -> int | None:
        for key in (
            "x-ratelimit-limit-output-tokens",
            "x-ratelimit-limit-tokens-output",
        ):
            value = self.headers.get(key)
            if value and value.isdigit():
                return int(value)
        return None

    def wait(self, seconds: float, sleeper: Callable[[float], None] = time.sleep) -> None:
        delay = max(0.0, seconds)
        self.wait_count += 1
        self.wait_seconds += delay
        print(f"Rate limit: waiting {delay:.0f}s…", flush=True)
        sleeper(delay)


class RateLimitMiddleware(AgentMiddleware):
    """Wait before over-budget calls and retry the same call after 429 responses."""

    def __init__(
        self,
        state: RateLimitState,
        max_rate_limit_waits: int = 6,
        *,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__()
        self.state = state
        self.max_rate_limit_waits = max_rate_limit_waits
        self.sleeper = sleeper

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        estimate = (
            len(
                repr(
                    (request.system_message, request.system_prompt, request.messages, request.tools)
                )
            )
            + 3
        ) // 4
        waits = 0
        server_errors = 0
        while True:
            remaining = self.state.remaining_tokens
            if remaining is not None and estimate > remaining:
                if waits >= self.max_rate_limit_waits:
                    raise RuntimeError("Rate-limit wait budget exhausted before model call")
                self.state.wait(self.state.reset_seconds or 60.0, self.sleeper)
                waits += 1
                self.state.headers.pop("x-ratelimit-remaining-tokens", None)
                continue
            try:
                return handler(request)
            except Exception as exc:
                headers = _headers(exc)
                if headers:
                    self.state.capture_headers(headers)
                text = f"{type(exc).__name__} {exc}".casefold()
                if "429" in text or "rate limit" in text or "rate_limit" in text:
                    if waits >= self.max_rate_limit_waits:
                        raise RuntimeError(
                            "Rate-limit wait budget exhausted after 429 responses"
                        ) from exc
                    retry_after = getattr(exc, "retry_after", None) or headers.get("retry-after")
                    delay = _seconds(str(retry_after)) if retry_after is not None else None
                    delay = delay or self.state.reset_seconds or 60.0
                    self.state.wait(delay, self.sleeper)
                    waits += 1
                    self.state.headers.pop("x-ratelimit-remaining-tokens", None)
                    continue
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if "model not found" in text or "decommissioned" in text or status == 404:
                    raise ModelUnavailableError(str(exc)) from exc
                if status is not None and 500 <= int(status) < 600:
                    server_errors += 1
                    if server_errors < 3:
                        self.sleeper(1.0)
                        continue
                    raise ModelUnavailableError(
                        f"Model returned {server_errors} consecutive server errors: {exc}"
                    ) from exc
                raise
