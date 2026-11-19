"""Shared test fixtures."""

import pytest


@pytest.fixture(autouse=True)
def _no_live_tracing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests from sending traces with the developer's real LangSmith key from .env."""
    monkeypatch.setenv("ARA_TRACING_BACKEND", "none")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGSMITH_TRACING_V2", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
