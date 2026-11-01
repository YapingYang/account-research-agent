"""Settings behavior tests."""

import pytest
from pydantic import ValidationError

from account_research.config import Settings


def test_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.search_budget == 8
    assert settings.model == "groq:openai/gpt-oss-120b"


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
