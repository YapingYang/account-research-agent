"""Prompt file loading."""

from pathlib import Path


def load_prompt(name: str, root: Path | None = None) -> str:
    """Load a named Markdown prompt, with a clear error for unknown names."""
    prompt_root = root or Path(__file__).parent
    path = prompt_root / f"{name}.md"
    if name != "main" or not path.is_file():
        raise ValueError(f"Unknown prompt: {name}")
    return path.read_text(encoding="utf-8")
