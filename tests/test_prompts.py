"""Prompt loader tests."""

from datetime import date

import pytest

from account_research.prompts import load_prompt


def test_load_prompt() -> None:
    prompt = load_prompt("main")
    assert "<untrusted_content>" in prompt
    assert len(prompt) <= 1400


def test_runtime_prompt_includes_today_and_date_rules() -> None:
    today = date.today().isoformat()
    prompt = load_prompt("main")
    runtime_prompt = f"Today's date is {today}.\n{prompt}"
    assert f"Today's date is {today}." in runtime_prompt
    assert "never add years or fiscal periods to search queries from model knowledge" in runtime_prompt


def test_unknown_prompt() -> None:
    with pytest.raises(ValueError, match="Unknown prompt"):
        load_prompt("missing")
