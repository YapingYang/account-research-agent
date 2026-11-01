"""Discover skill metadata from SKILL.md frontmatter."""

from pathlib import Path
from typing import TypedDict

import yaml


class SkillMetadata(TypedDict):
    """Frontmatter metadata and location for a discovered skill."""

    agent_folder: str
    name: str
    description: str
    path: Path


def discover_skills(root: Path) -> list[SkillMetadata]:
    """Find and parse all SKILL.md files beneath root."""
    skills: list[SkillMetadata] = []
    if not root.exists():
        return skills
    for path in sorted(root.glob("**/SKILL.md")):
        content = path.read_text(encoding="utf-8")
        if not content.startswith("---\n"):
            raise ValueError(f"Invalid skill frontmatter in {path}: expected opening ---")
        parts = content.split("---", 2)
        if len(parts) < 3:
            raise ValueError(f"Invalid skill frontmatter in {path}: missing closing ---")
        try:
            metadata = yaml.safe_load(parts[1])
        except yaml.YAMLError as exc:
            raise ValueError(f"Invalid skill frontmatter in {path}: {exc}") from exc
        if (
            not isinstance(metadata, dict)
            or not isinstance(metadata.get("name"), str)
            or not isinstance(metadata.get("description"), str)
        ):
            raise ValueError(
                f"Invalid skill frontmatter in {path}: name and description are required"
            )
        skills.append(
            {
                "agent_folder": path.parent.parent.name,
                "name": metadata["name"],
                "description": metadata["description"],
                "path": path,
            }
        )
    return skills


def skills_for(agent_name: str, root: Path = Path("skills")) -> list[Path]:
    """Return the skill directories explicitly assigned to an agent."""
    folder = root / agent_name
    if not folder.is_dir():
        raise ValueError(f"Unknown agent skill folder: {agent_name}")
    return sorted(path.parent for path in folder.glob("*/SKILL.md"))
