"""Resilient MCP discovery and allowlisted tool wrapping."""

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, cast

from langchain_core.tools import BaseTool, StructuredTool

from account_research.config import Settings
from account_research.mcp.allowlist import filter_tools
from account_research.mcp.config import load_mcp_config
from account_research.sources.registry import SourceRegistry
from account_research.tools._text import truncate, wrap_untrusted

logger = logging.getLogger(__name__)
_ACCESSION_RE = re.compile(r"\b\d{10}-\d{2}-\d{6}\b|\b\d{18}\b")


def _record_sec_sources(
    text: str, args: dict[str, Any], tool_name: str, registry: SourceRegistry
) -> list[Any]:
    """Register SEC filing URLs, deriving Archives URLs from CIK/accession pairs."""
    cik_value = args.get("cik", args.get("cik_number", args.get("identifier", "")))
    if not str(cik_value).strip().isdigit():
        cik_match = re.search(r"\bCIK\s*[:=]?\s*(0*\d{1,10})", text, re.IGNORECASE)
        if cik_match is None:
            cik_match = re.search(r"/data/(0*\d{1,10})/", text, re.IGNORECASE)
        cik_value = cik_match.group(1) if cik_match else ""
    cik = str(cik_value).strip().lstrip("0")
    accessions = list(dict.fromkeys(_ACCESSION_RE.findall(text)))
    if cik.isdigit() and accessions:
        return [
            registry.record(
                f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/",
                tool_name,
                "sec_filing",
            )
            for accession in accessions
        ]
    urls = re.findall(r"https?://(?:www\.)?sec\.gov/[^\s<>\"']+", text)
    if urls:
        return [registry.record(url.rstrip(".,);"), tool_name, "sec_filing") for url in urls]
    fallback_url = (
        f"https://www.sec.gov/edgar/browse/?CIK={cik}"
        if cik.isdigit()
        else "https://www.sec.gov/edgar/"
    )
    return [registry.record(fallback_url, tool_name, "sec_filing")]


@dataclass(frozen=True)
class McpStatus:
    """MCP availability for one discovery attempt."""

    available: bool
    reason: str = ""


async def discover_raw_tools(settings: Settings) -> tuple[list[BaseTool], McpStatus]:
    """Start configured MCP servers and discover their tools, failing closed."""
    if not settings.mcp_enabled:
        return [], McpStatus(False, "MCP is disabled")
    try:
        config = load_mcp_config(settings.mcp_config_path)
        servers = {}
        for name, server in config.servers.items():
            if not server.enabled:
                continue
            env = {key: str(getattr(settings, key.lower(), "")) for key in server.env_from_settings}
            servers[name] = {
                "transport": server.transport,
                "command": server.command,
                "args": server.args,
                "env": env,
                "session_kwargs": {
                    "read_timeout_seconds": timedelta(seconds=settings.mcp_timeout_seconds)
                },
            }
        if not servers:
            return [], McpStatus(False, "No MCP servers are enabled")
        from langchain_mcp_adapters.client import MultiServerMCPClient

        client = MultiServerMCPClient(cast(Any, servers))
        tools = await asyncio.wait_for(client.get_tools(), timeout=settings.mcp_timeout_seconds)
        return list(tools), McpStatus(True)
    except Exception as exc:  # noqa: BLE001 - optional external server must not break startup
        logger.warning("MCP discovery unavailable: %s", exc)
        return [], McpStatus(False, str(exc))


def _wrap_tool(tool: BaseTool, settings: Settings, registry: SourceRegistry) -> BaseTool:
    """Bound MCP output and tag it as untrusted, recording filing URLs."""

    async def invoke(args: dict[str, Any]) -> str:
        value = await asyncio.wait_for(tool.ainvoke(args), timeout=settings.mcp_timeout_seconds)
        text = str(value)
        source = _record_sec_sources(text, args, tool.name, registry)[0]
        return wrap_untrusted(source, truncate(text, settings.mcp_result_chars))

    async def acall(**kwargs: Any) -> str:
        return await invoke(kwargs)

    def call(**kwargs: Any) -> str:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(invoke(kwargs))
        raise RuntimeError("Use ainvoke for MCP tools inside an active event loop")

    return StructuredTool.from_function(
        func=call,
        coroutine=acall,
        name=tool.name,
        description=tool.description or "SEC filing lookup",
        args_schema=tool.args_schema,
    )


async def load_mcp_tools(
    settings: Settings, registry: SourceRegistry
) -> tuple[list[BaseTool], McpStatus]:
    """Discover, allowlist, and safely wrap configured MCP tools."""
    tools, status = await discover_raw_tools(settings)
    if not status.available:
        return [], status
    try:
        config = load_mcp_config(settings.mcp_config_path)
        allowed = {
            name
            for server in config.servers.values()
            if server.enabled
            for name in server.allowed_tools
        }
        return [
            _wrap_tool(tool, settings, registry) for tool in filter_tools(tools, allowed)
        ], status
    except Exception as exc:  # noqa: BLE001
        logger.warning("MCP allowlist loading failed: %s", exc)
        return [], McpStatus(False, str(exc))
