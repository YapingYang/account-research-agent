"""MCP configuration and allowlist tests."""

from pathlib import Path
from types import SimpleNamespace

from account_research.mcp.allowlist import filter_tools
from account_research.mcp.config import load_mcp_config


def test_repo_config_validates() -> None:
    config = load_mcp_config(Path("mcp_servers.json"))
    assert config.servers["sec_edgar"].allowed_tools == []


def test_empty_allowlist_exposes_nothing() -> None:
    assert filter_tools([SimpleNamespace(name="one")], []) == []


def test_filter_keeps_only_allowed_names() -> None:
    tools = [SimpleNamespace(name="one"), SimpleNamespace(name="two")]
    assert filter_tools(tools, ["two"]) == [tools[1]]
