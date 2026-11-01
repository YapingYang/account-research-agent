"""Prompt loader tests."""

import pytest

from account_research.prompts import load_prompt


def test_load_prompt() -> None:
    assert "TODO" in load_prompt("main")


def test_unknown_prompt() -> None:
    with pytest.raises(ValueError, match="Unknown prompt"):
        load_prompt("missing")
