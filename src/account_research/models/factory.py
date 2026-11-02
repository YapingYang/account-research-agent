"""Chat model construction for configured providers."""

from importlib.util import find_spec
from typing import Any, cast

from langchain_core.language_models.chat_models import BaseChatModel

from account_research.config import Settings
from account_research.models.ratelimit import RateLimitState


def get_chat_model(
    model_string: str,
    settings: Settings,
    *,
    http_client: Any | None = None,
    rate_limit_state: RateLimitState | None = None,
) -> BaseChatModel:
    """Construct a chat model from a provider:model identifier."""
    if ":" not in model_string:
        raise ValueError("Model must use provider:model syntax (supported: groq, ollama, gemini)")
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
        f"Unknown model provider {provider!r}; supported providers: groq, ollama, gemini"
    )
