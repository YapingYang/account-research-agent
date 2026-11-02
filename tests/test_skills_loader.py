"""Filesystem skill discovery tests."""

from pathlib import Path

import pytest

from account_research.skills.loader import agent_skill_paths, discover_skills


def test_all_repo_skills_are_discovered() -> None:
    found = discover_skills(Path("skills"))
    assert {skill["name"] for skill in found} == {
        "account-brief",
        "sec-filings",
        "fact-format",
    }
    assert [path.name for path in agent_skill_paths()] == ["account-brief", "sec-filings"]


def test_production_skills_are_compact() -> None:
    for path in agent_skill_paths():
        content = (path / "SKILL.md").read_text(encoding="utf-8")
        assert len(content.split()) < 600


def test_missing_frontmatter_raises(tmp_path: Path) -> None:
    skill = tmp_path / "agent" / "skill" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("No metadata", encoding="utf-8")
    with pytest.raises(ValueError, match="frontmatter"):
        discover_skills(tmp_path)
