"""Chat model construction for configured providers."""

from importlib.util import find_spec
from typing import Any, cast

from langchain_core.language_models.chat_models import BaseChatModel

from account_research.config import Settings
from account_research.models.ratelimit import RateLimitState


_THINKING_BINDING_BETA = "thinking-binding-controls-2026-08-01"
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


def get_chat_model(
    model_string: str,
    settings: Settings,
    *,
    http_client: Any | None = None,
    rate_limit_state: RateLimitState | None = None,
) -> BaseChatModel:
    """Construct a chat model from a provider:model identifier."""
    if ":" not in model_string:
        raise ValueError(
            "Model must use provider:model syntax "
            "(supported: anthropic, groq, openai, ollama, gemini)"
        )
    provider, model = model_string.split(":", 1)
    if not provider or not model:
        raise ValueError("Model must use provider:model syntax with non-empty values")
    if provider == "groq":
        if settings.groq_api_key is None:
            raise ValueError("GROQ_API_KEY is required for groq models")
        from langchain_groq import ChatGroq

        options: dict[str, Any] = {
            "model": model,
            "timeout": settings.request_timeout_seconds,
            "max_retries": 0 if rate_limit_state is not None else settings.max_retries,
            "max_tokens": settings.max_output_tokens,
            "api_key": settings.groq_api_key,
        }
        if rate_limit_state is not None and http_client is None:
            import httpx

            http_client = httpx.Client(
                event_hooks={"response": [rate_limit_state.capture_response]}
            )
        if http_client is not None:
            options["http_client"] = http_client
        return cast(BaseChatModel, ChatGroq(**options))
    if provider == "openai":
        if settings.openai_api_key is None:
            raise ValueError("OPENAI_API_KEY is required for openai models")
        from langchain_openai import ChatOpenAI

        options: dict[str, Any] = {
            "model": model,
            "timeout": settings.request_timeout_seconds,
            "max_retries": 0 if rate_limit_state is not None else settings.max_retries,
            "max_tokens": settings.max_output_tokens,
            "api_key": settings.openai_api_key,
        }
        if rate_limit_state is not None and http_client is None:
            import httpx

            http_client = httpx.Client(
                event_hooks={"response": [rate_limit_state.capture_response]}
            )
        if http_client is not None:
            options["http_client"] = http_client
        kwargs: dict[str, Any] = dict(options)
        kwargs["temperature"] = 0.0
        try:
            return cast(BaseChatModel, ChatOpenAI(**kwargs))
        except TypeError as exc:
            if "temperature" not in str(exc):
                raise
            kwargs.pop("temperature", None)
            return cast(BaseChatModel, ChatOpenAI(**kwargs))
    if provider == "anthropic":
        if settings.anthropic_api_key is None:
            raise ValueError("ANTHROPIC_API_KEY is required for anthropic models")
        from langchain_anthropic import ChatAnthropic

        # If a safety classifier declines a turn, the API re-runs it on a fallback model chosen
        # by refusal category instead of stopping the run. Haiku has no server-side fallback.
        fallbacks = not model.startswith("claude-haiku")
        return cast(
            BaseChatModel,
            ChatAnthropic(  # type: ignore[call-arg]  # pydantic plugin misreads `stop`
                model_name=model,
                api_key=settings.anthropic_api_key,
                timeout=settings.request_timeout_seconds,
                # The SDK retries 429/5xx honoring retry-after; RateLimitMiddleware covers the rest.
                max_retries=settings.max_retries,
                max_tokens_to_sample=settings.anthropic_max_tokens,
                # No temperature/top_p: current Claude models reject sampling parameters.
                # Adaptive thinking is always on for Opus 5.5. This app trims old tool results and
                # summarizes earlier turns, which invalidates earlier thinking blocks; "drop_block"
                # has the API drop those blocks instead of rejecting the request with a 400.
                thinking={
                    "type": "adaptive",
                    "block_binding": {"prefix_mismatch_behavior": "drop_block"},
                },
                output_config={"effort": settings.anthropic_effort},
                betas=[_THINKING_BINDING_BETA, *([_FALLBACK_BETA] if fallbacks else [])],
                model_kwargs={"fallbacks": "default"} if fallbacks else {},
            ),
        )
    if provider == "ollama":
        if find_spec("langchain_ollama") is None:
            raise ValueError("Ollama support requires installing the 'local' extra")
        from langchain_ollama import ChatOllama

        return cast(
            BaseChatModel,
            ChatOllama(
                model=model,
                sync_client_kwargs={"timeout": settings.request_timeout_seconds},
                async_client_kwargs={"timeout": settings.request_timeout_seconds},
                num_predict=settings.max_output_tokens,
            ),
        )
    if provider == "gemini":
        if settings.google_api_key is None:
            raise ValueError("GOOGLE_API_KEY is required for gemini models")
        from langchain_google_genai import ChatGoogleGenerativeAI

        return cast(
            BaseChatModel,
            ChatGoogleGenerativeAI(
                model=model,
                api_key=settings.google_api_key,
                request_timeout=settings.request_timeout_seconds,
                retries=settings.max_retries,
                max_output_tokens=settings.max_output_tokens,
            ),
        )
    raise ValueError(
        f"Unknown model provider {provider!r}; "
        "supported providers: anthropic, groq, openai, ollama, gemini"
    )
