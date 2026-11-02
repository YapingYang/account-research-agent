"""Tests for provider:model parsing and provider construction."""

import sys
from types import ModuleType
from unittest.mock import Mock

import pytest

from account_research.config import Settings
from account_research.models.factory import get_chat_model


def test_groq_model_parsing_preserves_slashes(monkeypatch: pytest.MonkeyPatch) -> None:
    chat_groq = ModuleType("langchain_groq")
    chat_groq.ChatGroq = Mock(return_value=object())  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langchain_groq", chat_groq)
    settings = Settings(
        groq_api_key="secret", request_timeout_seconds=17, max_retries=2, _env_file=None
    )
    result = get_chat_model("groq:openai/gpt-oss-120b", settings)
    assert result is not None
    called = chat_groq.ChatGroq.call_args  # type: ignore[attr-defined]
    assert called.kwargs["model"] == "openai/gpt-oss-120b"
    assert called.kwargs["timeout"] == 17
    assert called.kwargs["max_retries"] == 2
    assert called.kwargs["api_key"].get_secret_value() == "secret"


def test_unknown_provider_is_clear() -> None:
    with pytest.raises(ValueError, match="supported providers: groq, ollama, gemini"):
        get_chat_model("other:model", Settings(_env_file=None))


def test_missing_groq_key_is_clear() -> None:
    with pytest.raises(ValueError, match="GROQ_API_KEY is required"):
        get_chat_model("groq:model", Settings(_env_file=None))


def test_gemini_provider_uses_configured_key(monkeypatch: pytest.MonkeyPatch) -> None:
    google_genai = ModuleType("langchain_google_genai")
    google_genai.ChatGoogleGenerativeAI = Mock(return_value=object())  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langchain_google_genai", google_genai)
    settings = Settings(google_api_key="google-secret", _env_file=None)
    assert get_chat_model("gemini:gemini-3.7-flash", settings) is not None
    called = google_genai.ChatGoogleGenerativeAI.call_args  # type: ignore[attr-defined]
    assert called.kwargs["model"] == "gemini-3.7-flash"
    assert called.kwargs["api_key"].get_secret_value() == "google-secret"
    assert called.kwargs["request_timeout"] == 60
    assert called.kwargs["retries"] == 3


def test_missing_gemini_key_is_clear() -> None:
    with pytest.raises(ValueError, match="GOOGLE_API_KEY is required"):
        get_chat_model("gemini:gemini-3.7-flash", Settings(_env_file=None))


def test_google_key_is_redacted() -> None:
    assert (
        Settings(google_api_key="private", _env_file=None).redacted()["google_api_key"]
        == "********"
    )


def test_requires_provider_and_model() -> None:
    with pytest.raises(ValueError, match="provider:model"):
        get_chat_model("model", Settings(_env_file=None))
