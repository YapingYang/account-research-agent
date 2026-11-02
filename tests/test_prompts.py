"""Prompt loader tests."""

import pytest

from account_research.prompts import load_prompt


def test_load_prompt() -> None:
    prompt = load_prompt("main")
    assert "<untrusted_content>" in prompt
    assert len(prompt) <= 1400


def test_unknown_prompt() -> None:
    with pytest.raises(ValueError, match="Unknown prompt"):
        load_prompt("missing")
