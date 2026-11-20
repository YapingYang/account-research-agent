"""Groq rate-limit header capture and synchronous model-call throttling."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse


_BILLING_CODES = (
    "insufficient_quota",  # OpenAI
    "credit_balance_exhausted",  # OpenAI
    "billing_hard_limit",  # OpenAI
    "billing_error",  # Anthropic (HTTP 402)
    "credit balance is too low",  # Anthropic
)


class ModelUnavailableError(RuntimeError):
    """Model was not found or returned repeated server errors."""


class RateLimitWaitExceeded(RuntimeError):
    """A requested rate-limit sleep would exceed the run-wide wait budget."""


class DailyQuotaExceeded(RuntimeError):
    """The provider reports an exhausted daily token allowance."""


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
    headers: dict[str, str] = {}
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        response = getattr(current, "response", None)
        raw = getattr(response, "headers", {}) or {}
        headers.update({str(k).lower(): str(v) for k, v in raw.items()})
        for candidate in (getattr(current, "headers", None),):
            if not candidate:
                continue
            headers.update({str(k).lower(): str(v) for k, v in dict(candidate).items()})
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
    return {k: v for k, v in headers.items() if k.startswith("x-ratelimit-") or k == "retry-after"}


class RateLimitState:
    """Store the latest Groq rate-limit headers and wait diagnostics."""

    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.wait_count = 0
        self.wait_seconds = 0.0
        self.last_status = ""
        self.last_limit = "unknown"

    def capture_response(self, response: Any) -> None:
        self.capture_headers(response.headers)
        self.last_status = str(getattr(response, "status_code", ""))

    def capture_exception(self, error: BaseException) -> None:
        self.capture_headers(_headers(error))

    def capture_headers(self, headers: Any) -> None:
        if not headers:
            return
        self.headers.update(
            {
                str(k).lower(): str(v)
                for k, v in dict(headers).items()
                if str(k).lower().startswith("x-ratelimit-") or str(k).lower() == "retry-after"
            }
        )

    @property
    def latest_headers(self) -> dict[str, str]:
        """Return a copy of the latest observed rate-limit headers."""
        return dict(self.headers)

    def classify(self, error: BaseException | None = None) -> str:
        """Name the quota category indicated by an error or remaining-limit header."""
        text = str(error or "").casefold()
        if "output tokens per minute" in text or "otpm" in text:
            return "output tokens per minute"
        if "tokens per day" in text or "per day" in text or "daily" in text or "tpd" in text:
            return "tokens/day"
        if "requests per minute" in text or "rpm" in text:
            return "requests/min"
        if "tokens per minute" in text or "tpm" in text:
            return "tokens/min"
        daily = next(
            (value for key, value in self.headers.items() if "remaining" in key and "day" in key),
            None,
        )
        if daily == "0":
            return "tokens/day"
        if self.headers.get("x-ratelimit-remaining-requests") == "0":
            return "requests/min"
        if self.headers.get("x-ratelimit-remaining-tokens") == "0":
            return "tokens/min"
        return "unknown"

    def reset_for(self, limit: str) -> str:
        """Return the raw reset or retry-after value for a quota category."""
        keys = {
            "requests/min": ("x-ratelimit-reset-requests",),
            "tokens/min": ("x-ratelimit-reset-tokens",),
            "output tokens per minute": (
                "x-ratelimit-reset-output-tokens",
                "x-ratelimit-reset-tokens",
            ),
            "tokens/day": (
                "x-ratelimit-reset-tokens-per-day",
                "x-ratelimit-reset-tokens-day",
            ),
        }
        for key in keys.get(limit, ()):
            if key in self.headers:
                return self.headers[key]
        return self.headers.get("retry-after", "unknown")

    @property
    def remaining_tokens(self) -> int | None:
        value = self.headers.get("x-ratelimit-remaining-tokens")
        return int(value) if value and value.isdigit() else None

    def remaining_requests(self) -> int | None:
        """Return remaining request budget when supplied by Groq."""
        value = self.headers.get("x-ratelimit-remaining-requests")
        return int(value) if value and value.isdigit() else None

    def remaining_output_tokens(self) -> int | None:
        """Return output token budget when supplied by Groq."""
        for key in (
            "x-ratelimit-remaining-output-tokens",
            "x-ratelimit-remaining-tokens-output",
        ):
            value = self.headers.get(key)
            if value and value.isdigit():
                return int(value)
        return None

    @property
    def reset_seconds(self) -> float | None:
        return _seconds(self.headers.get("x-ratelimit-reset-tokens")) or _seconds(
            self.headers.get("retry-after")
        )

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

    def wait(
        self,
        seconds: float,
        limit: str,
        max_total_wait_seconds: float,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        delay = max(0.0, seconds)
        if self.wait_seconds + delay > max_total_wait_seconds:
            raise RateLimitWaitExceeded(
                f"Rate-limit wait cap exceeded for {limit}: requested {delay:.0f}s, "
                f"total cap {max_total_wait_seconds:.0f}s; reset time {self.reset_for(limit)}."
            )
        self.wait_count += 1
        self.wait_seconds += delay
        self.last_limit = limit
        print(
            f"Rate limit ({limit}): waiting {delay:.0f}s; reset {self.reset_for(limit)}…",
            flush=True,
        )
        sleeper(delay)


class RateLimitMiddleware(AgentMiddleware):
    """Wait before over-budget calls and retry the same call after 429 responses."""

    def __init__(
        self,
        state: RateLimitState,
        max_rate_limit_waits: int = 6,
        max_total_wait_seconds: float = 300,
        max_output_tokens: int = 1500,
        *,
        sleeper: Callable[[float], None] = time.sleep,
        default_wait_seconds: float = 0.0,
        backoff_base_seconds: float = 2.0,
        backoff_cap_seconds: float = 15.0,
        on_wait: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        super().__init__()
        self.state = state
        self.max_rate_limit_waits = max_rate_limit_waits
        self.max_total_wait_seconds = max_total_wait_seconds
        self.max_output_tokens = max_output_tokens
        self.sleeper = sleeper
        self.default_wait_seconds = default_wait_seconds
        self.backoff_base_seconds = backoff_base_seconds
        self.backoff_cap_seconds = backoff_cap_seconds
        self.on_wait = on_wait

    def _backoff_delay(self, wait_count: int) -> float:
        if wait_count < 0:
            wait_count = 0
        return min(self.backoff_base_seconds * (2 ** wait_count), self.backoff_cap_seconds)

    def _emit_wait(self, limit: str, seconds: float, reset: str) -> None:
        if self.on_wait is not None:
            self.on_wait({
                "type": "rate-limit-wait",
                "limit": limit,
                "seconds": seconds,
                "reset": reset,
            })

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
            remaining_requests = self.state.remaining_requests()
            remaining_output = self.state.remaining_output_tokens()
            remaining_daily = next(
                (
                    int(value)
                    for key, value in self.state.headers.items()
                    if "remaining" in key and "day" in key and value.isdigit()
                ),
                None,
            )
            if remaining_daily == 0:
                raise DailyQuotaExceeded(
                    f"Daily token quota exhausted; reset time "
                    f"{self.state.reset_for('tokens/day')}; "
                    f"headers={self.state.latest_headers}"
                )
            elif remaining_requests == 0:
                limit, reset = "requests/min", self.state.reset_for("requests/min")
            elif remaining is not None and estimate > remaining:
                limit, reset = "tokens/min", self.state.reset_for("tokens/min")
            elif remaining_output is not None and self.max_output_tokens > remaining_output:
                limit, reset = (
                    "output tokens per minute",
                    self.state.reset_for("output tokens per minute"),
                )
            else:
                limit = ""
                reset = ""
            if limit and (not reset or reset == "unknown"):
                if waits >= self.max_rate_limit_waits:
                    raise RuntimeError("Rate-limit wait budget exhausted before model call")
                delay = self._backoff_delay(waits)
                self._emit_wait(limit, delay, reset or "unknown")
                self.state.wait(delay, limit, self.max_total_wait_seconds, self.sleeper)
                waits += 1
                remaining_header = {
                    "tokens/min": "x-ratelimit-remaining-tokens",
                    "requests/min": "x-ratelimit-remaining-requests",
                    "output tokens per minute": "x-ratelimit-remaining-output-tokens",
                    "tokens/day": "x-ratelimit-remaining-tokens-per-day",
                }.get(limit)
                if remaining_header:
                    self.state.headers.pop(remaining_header, None)
                continue
            if limit:
                if waits >= self.max_rate_limit_waits:
                    raise RuntimeError("Rate-limit wait budget exhausted before model call")
                delay = _seconds(reset) or self.state.reset_seconds or self._backoff_delay(waits)
                self._emit_wait(limit, delay, reset or "unknown")
                self.state.wait(
                    delay,
                    limit,
                    self.max_total_wait_seconds,
                    self.sleeper,
                )
                waits += 1
                remaining_header = {
                    "tokens/min": "x-ratelimit-remaining-tokens",
                    "requests/min": "x-ratelimit-remaining-requests",
                    "output tokens per minute": "x-ratelimit-remaining-output-tokens",
                    "tokens/day": "x-ratelimit-remaining-tokens-per-day",
                }.get(limit)
                if remaining_header:
                    self.state.headers.pop(remaining_header, None)
                continue
            try:
                return handler(request)
            except Exception as exc:
                headers = _headers(exc)
                if headers:
                    self.state.capture_headers(headers)
                text = f"{type(exc).__name__} {exc}".casefold()
                if any(code in text for code in _BILLING_CODES):
                    # Out of credit is not a rate limit: waiting will not help, so fail fast.
                    raise ModelUnavailableError(
                        "The model provider rejected the request: the account is out of credit "
                        "or quota. Add billing credit or switch ARA_MODEL to another provider."
                    ) from exc
                if "429" in text or "rate limit" in text or "rate_limit" in text:
                    if waits >= self.max_rate_limit_waits:
                        raise RuntimeError(
                            "Rate-limit wait budget exhausted after 429 responses"
                        ) from exc
                    retry_after = getattr(exc, "retry_after", None) or headers.get("retry-after")
                    delay = _seconds(str(retry_after)) if retry_after is not None else None
                    limit = self.state.classify(exc)
                    reset = self.state.reset_for(limit) if limit != "unknown" else "unknown"
                    if delay is None:
                        delay = _seconds(reset) if reset not in (None, "unknown") else self._backoff_delay(waits)
                    if limit == "tokens/day":
                        raise DailyQuotaExceeded(
                            f"Daily token quota exhausted; reset time "
                            f"{self.state.reset_for(limit)}; headers={self.state.latest_headers}"
                        ) from exc
                    self._emit_wait(limit, delay, reset)
                    self.state.wait(
                        delay,
                        limit,
                        self.max_total_wait_seconds,
                        self.sleeper,
                    )
                    waits += 1
                    remaining_header = {
                        "tokens/min": "x-ratelimit-remaining-tokens",
                        "requests/min": "x-ratelimit-remaining-requests",
                        "output tokens per minute": "x-ratelimit-remaining-output-tokens",
                        "tokens/day": "x-ratelimit-remaining-tokens-per-day",
                    }.get(limit)
                    if remaining_header:
                        self.state.headers.pop(remaining_header, None)
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
