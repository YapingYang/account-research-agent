"""Aggregate per-call usage metadata for one research run."""

from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import ChatGeneration, LLMResult


@dataclass
class TokenReport:
    """Model call and token usage totals."""

    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    largest_request_input_tokens: int = 0
    rate_limit_errors: int = 0

    @property
    def total_tokens(self) -> int:
        """Total reported input and output tokens."""
        return self.input_tokens + self.output_tokens

    def as_dict(self) -> dict[str, int]:
        """Return a JSON-compatible usage summary."""
        values = asdict(self)
        values["total_tokens"] = self.total_tokens
        return values


class TokenUsageHandler(BaseCallbackHandler):
    """Collect model-call usage from LangChain callback responses."""

    def __init__(self) -> None:
        self.report = TokenReport()

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        del serialized, messages, run_id, kwargs
        self.report.model_calls += 1

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        del serialized, prompts, run_id, kwargs
        self.report.model_calls += 1

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        del kwargs
        generation = response.generations[0][0] if response.generations else None
        message = generation.message if isinstance(generation, ChatGeneration) else None
        usage = getattr(message, "usage_metadata", None) or {}
        llm_usage = (response.llm_output or {}).get("token_usage", {})
        input_tokens = int(usage.get("input_tokens", llm_usage.get("prompt_tokens", 0)) or 0)
        output_tokens = int(usage.get("output_tokens", llm_usage.get("completion_tokens", 0)) or 0)
        self.report.input_tokens += input_tokens
        self.report.output_tokens += output_tokens
        self.report.largest_request_input_tokens = max(
            self.report.largest_request_input_tokens, input_tokens
        )

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        del kwargs
        if _is_rate_limit_error(error):
            self.report.rate_limit_errors += 1


def _is_rate_limit_error(error: BaseException) -> bool:
    text = f"{type(error).__name__} {error}".casefold()
    return "429" in text or "rate limit" in text or "rate_limit" in text or "ratelimit" in text
