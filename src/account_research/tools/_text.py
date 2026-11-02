"""Small helpers for keeping tool output bounded and marked as untrusted."""

from html import escape
from typing import Any


def truncate(text: str, n: int) -> str:
    """Limit text to n characters, ending at a word boundary when possible."""
    if len(text) <= n:
        return text
    suffix = "…[truncated]"
    if n <= len(suffix):
        return suffix[:n]
    prefix = text[: n - len(suffix)]
    if " " in prefix:
        prefix = prefix.rsplit(" ", 1)[0]
    return prefix.rstrip() + suffix


def wrap_untrusted(source: Any, text: str) -> str:
    """Wrap source text in a source-tagged boundary safe for XML-like prompts."""
    source_id = escape(str(source.id), quote=True)
    title = escape(str(source.title), quote=True)
    url = escape(str(source.url), quote=True)
    return (
        f'<untrusted_content source_id="{source_id}" title="{title}" url="{url}">'
        f"{escape(text, quote=False)}</untrusted_content>"
    )
