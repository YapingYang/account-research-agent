"""Deny-by-default MCP tool filtering."""

import logging
from collections.abc import Iterable
from typing import Any

logger = logging.getLogger(__name__)


def filter_tools(tools: Iterable[Any], allowed_names: Iterable[str]) -> list[Any]:
    """Keep only named tools in the allowlist and log dropped names."""
    allowed = set(allowed_names)
    kept: list[Any] = []
    dropped: list[str] = []
    for tool in tools:
        name = getattr(tool, "name", None)
        if name in allowed:
            kept.append(tool)
        else:
            dropped.append(str(name))
    if dropped:
        logger.info("Dropped MCP tools outside allowlist: %s", ", ".join(dropped))
    return kept
