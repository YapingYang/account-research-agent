"""Filesystem skill discovery tests."""

from pathlib import Path

import pytest

from account_research.skills.loader import discover_skills, skills_for


def test_all_repo_skills_are_discovered() -> None:
    found = discover_skills(Path("skills"))
    assert {skill["name"] for skill in found} == {
        "account-brief",
        "web-research",
        "sec-filings",
        "source-verification",
    }
    assert len(skills_for("researcher")) == 2


def test_missing_frontmatter_raises(tmp_path: Path) -> None:
    skill = tmp_path / "agent" / "skill" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("No metadata", encoding="utf-8")
    with pytest.raises(ValueError, match="frontmatter"):
        discover_skills(tmp_path)
