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
    with pytest.raises(
        ValueError, match="supported providers: anthropic, groq, openai, ollama, gemini"
    ):
        get_chat_model("other:model", Settings(_env_file=None))


def test_missing_groq_key_is_clear() -> None:
    with pytest.raises(ValueError, match="GROQ_API_KEY is required"):
        get_chat_model("groq:model", Settings(_env_file=None))


def test_openai_provider_uses_configured_key_and_retries_without_temperature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    openai_mod = ModuleType("langchain_openai")
    openai_mod.ChatOpenAI = Mock(
        side_effect=[
            TypeError("got an unexpected keyword argument 'temperature'"),
            object(),
        ]
    )
    monkeypatch.setitem(sys.modules, "langchain_openai", openai_mod)
    settings = Settings(openai_api_key="openai-secret", request_timeout_seconds=12, max_retries=5,
        _env_file=None)
    result = get_chat_model("openai:gpt-4o-mini", settings)
    assert result is not None
    assert openai_mod.ChatOpenAI.call_count == 2
    first = openai_mod.ChatOpenAI.call_args_list[0].kwargs
    assert first["model"] == "gpt-4o-mini"
    assert first["timeout"] == 12
    assert first["max_retries"] == 5
    assert first["api_key"].get_secret_value() == "openai-secret"
    assert "temperature" in first
    second = openai_mod.ChatOpenAI.call_args_list[1].kwargs
    assert "temperature" not in second


def test_missing_openai_key_is_clear() -> None:
    with pytest.raises(ValueError, match="OPENAI_API_KEY is required"):
        get_chat_model("openai:model", Settings(_env_file=None))


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


def test_openai_key_is_redacted() -> None:
    assert Settings(openai_api_key="private", _env_file=None).redacted()["openai_api_key"] == "********"


def test_requires_provider_and_model() -> None:
    with pytest.raises(ValueError, match="provider:model"):
        get_chat_model("model", Settings(_env_file=None))


def test_anthropic_requires_key() -> None:
    settings = Settings(_env_file=None, ANTHROPIC_API_KEY=None)
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY is required"):
        get_chat_model("anthropic:claude-opus-5-5", settings)


def test_anthropic_request_payload_fits_opus_5_5_rules() -> None:
    """Build the request offline (nothing is sent) and check what the API will receive."""
    from langchain_core.messages import HumanMessage

    settings = Settings(_env_file=None, ANTHROPIC_API_KEY="sk-ant-test")
    model = get_chat_model("anthropic:claude-opus-5-5", settings)
    payload = model._get_request_payload(  # type: ignore[attr-defined]
        [HumanMessage(content="hi")]
    )
    assert payload["model"] == "claude-opus-5-5"
    assert payload["max_tokens"] == 16000
    # Opus 5.5 rejects sampling parameters and disabled thinking.
    assert "temperature" not in payload and "top_p" not in payload
    assert payload["thinking"] == {
        "type": "adaptive",
        "block_binding": {"prefix_mismatch_behavior": "drop_block"},
    }
    assert payload["output_config"] == {"effort": "medium"}
    assert payload["fallbacks"] == "default"
    assert set(payload["betas"]) == {
        "thinking-binding-controls-2026-08-01",
        "server-side-fallback-2026-07-01",
    }


def test_haiku_gets_no_server_side_fallback() -> None:
    from langchain_core.messages import HumanMessage

    settings = Settings(_env_file=None, ANTHROPIC_API_KEY="sk-ant-test")
    model = get_chat_model("anthropic:claude-haiku-5-5", settings)
    payload = model._get_request_payload(  # type: ignore[attr-defined]
        [HumanMessage(content="hi")]
    )
    assert "fallbacks" not in payload
    assert payload["betas"] == ["thinking-binding-controls-2026-08-01"]
