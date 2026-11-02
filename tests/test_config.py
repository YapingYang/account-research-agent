"""Settings behavior tests."""

import pytest
from pydantic import ValidationError

from account_research.config import Settings


def test_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.search_budget == 6
    assert settings.max_search_results == 5
    assert settings.snippet_chars == 300
    assert settings.max_page_chars == 3000
    assert settings.model == "groq:openai/gpt-oss-120b"
    assert settings.fallback_model is None
    assert settings.request_timeout_seconds == 60
    assert settings.max_retries == 3


def test_environment_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARA_SEARCH_BUDGET", "12")
    assert Settings(_env_file=None).search_budget == 12


def test_search_budget_validation() -> None:
    with pytest.raises(ValidationError):
        Settings(search_budget=26, _env_file=None)


def test_redacted_hides_secrets() -> None:
    settings = Settings(groq_api_key="private", _env_file=None)
    assert settings.redacted()["groq_api_key"] == "********"
    assert "private" not in str(settings.redacted())
