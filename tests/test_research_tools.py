"""Offline coverage for bounded, source-aware tools."""

import asyncio
import json
import socket
from pathlib import Path

import pytest
from langchain_core.tools import StructuredTool

from account_research.config import Settings
from account_research.mcp.client import _record_sec_sources, discover_raw_tools
from account_research.sources.registry import SourceRegistry, normalize_url
from account_research.tools import fetch_page as fetch_module
from account_research.tools import web_search as search_module
from account_research.tools.fetch_page import _validate_public_url, make_fetch_page
from account_research.tools.web_search import make_web_search


def test_registry_normalizes_and_deduplicates() -> None:
    registry = SourceRegistry()
    first = registry.record("HTTPS://Example.com/a/?utm_source=x#top", "A", "search_result")
    second = registry.record("https://example.com/a", "A page", "web_page")
    third = registry.record("https://example.com/b", "B", "search_result")
    assert normalize_url("https://Example.com/a?fbclid=xyz") == "https://example.com/a"
    assert first.id == second.id == "S1"
    assert second.kind == "web_page"
    assert third.id == "S2"
    assert registry.contains("https://example.com/a?utm_campaign=ignored")
    assert not registry.contains("https://example.com/missing")
    assert len(registry.all()) == 2


def test_search_caps_calls_compacts_results_and_registers(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeTavily:
        def __init__(self, **_: object) -> None:
            pass

        def search(self, query: str, **kwargs: object) -> dict[str, object]:
            assert query == "Acme"
            assert kwargs["max_results"] == 1
            return {
                "results": [
                    {"url": "https://example.com", "title": "Acme", "content": "word " * 200}
                ]
            }

    monkeypatch.setattr(search_module, "TavilyClient", FakeTavily)
    settings = Settings(
        tavily_api_key="secret",
        search_budget=1,
        max_search_results=1,
        snippet_chars=20,
        _env_file=None,
    )
    registry = SourceRegistry()
    tool = make_web_search(settings, registry)
    result = tool.invoke({"query": "Acme"})
    assert json.loads(result)[0]["id"] == "S1"
    assert len(json.loads(result)[0]["snippet"]) <= 20
    assert "Search budget reached" in tool.invoke({"query": "Acme"})
    assert len(registry.all()) == 1


def test_fetch_rejects_local_urls_and_wraps_truncated_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError):
        _validate_public_url("http://127.0.0.1/admin")
    with pytest.raises(ValueError):
        _validate_public_url("file:///etc/passwd")

    class FakeTavily:
        def __init__(self, **_: object) -> None:
            pass

        def extract(self, **kwargs: object) -> dict[str, object]:
            assert kwargs["format"] == "text"
            return {"results": [{"title": "Page", "raw_content": "valuable " * 500}]}

    monkeypatch.setattr(fetch_module, "TavilyClient", FakeTavily)
    monkeypatch.setattr(
        fetch_module.socket,
        "getaddrinfo",
        lambda *_args: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))],
    )
    settings = Settings(tavily_api_key="secret", max_page_chars=100, _env_file=None)
    registry = SourceRegistry()
    tool = make_fetch_page(settings, registry)
    result = tool.invoke({"url": "https://example.com"})
    assert '<untrusted_content source_id="S1"' in result
    assert "[truncated]" in result
    tool.invoke({"url": "https://example.com/2"})
    tool.invoke({"url": "https://example.com/3"})
    assert "Page fetch limit reached" in tool.invoke({"url": "https://example.com/4"})
    assert len(registry.all()) == 3


def test_mcp_disabled_fails_closed() -> None:
    tools, status = asyncio.run(discover_raw_tools(Settings(mcp_enabled=False, _env_file=None)))
    assert tools == []
    assert not status.available


def test_mcp_start_failure_and_timeout_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps({"servers": {"sec": {"enabled": True, "command": "fake", "allowed_tools": []}}}),
        encoding="utf-8",
    )

    class BrokenClient:
        def __init__(self, _servers: object) -> None:
            pass

        async def get_tools(self) -> list[object]:
            raise RuntimeError("start failed")

    monkeypatch.setattr("langchain_mcp_adapters.client.MultiServerMCPClient", BrokenClient)
    tools, status = asyncio.run(
        discover_raw_tools(Settings(mcp_config_path=config, _env_file=None))
    )
    assert tools == [] and not status.available

    class SlowClient:
        def __init__(self, _servers: object) -> None:
            pass

        async def get_tools(self) -> list[object]:
            await asyncio.sleep(1)
            return []

    monkeypatch.setattr("langchain_mcp_adapters.client.MultiServerMCPClient", SlowClient)
    tools, status = asyncio.run(
        discover_raw_tools(Settings(mcp_config_path=config, mcp_timeout_seconds=1, _env_file=None))
    )
    assert tools == [] and not status.available


def test_mcp_allowlist_wrapper_bounds_marks_and_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from account_research.mcp import client as mcp_client

    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps(
            {"servers": {"sec": {"enabled": True, "command": "fake", "allowed_tools": ["lookup"]}}}
        ),
        encoding="utf-8",
    )
    raw_tool = StructuredTool.from_function(
        func=lambda identifier: (
            f"Data https://www.sec.gov/Archives/edgar/data/1/filing.txt {'fact ' * 200}"
        ),
        name="lookup",
        description="Lookup SEC data",
    )

    async def fake_discover(_settings: Settings) -> tuple[list[StructuredTool], object]:
        from account_research.mcp.client import McpStatus

        return [raw_tool], McpStatus(True)

    monkeypatch.setattr(mcp_client, "discover_raw_tools", fake_discover)
    settings = Settings(mcp_config_path=config, mcp_result_chars=80, _env_file=None)
    registry = SourceRegistry()
    tools, status = asyncio.run(mcp_client.load_mcp_tools(settings, registry))
    assert status.available and [item.name for item in tools] == ["lookup"]
    result = tools[0].invoke({"identifier": "AAPL"})
    assert '<untrusted_content source_id="S1"' in result
    assert "[truncated]" in result
    assert "https://www.sec.gov/Archives/edgar/data/1/filing.txt" in str(registry.all()[0].url)


def test_sec_output_builds_filing_urls_from_cik_and_accessions() -> None:
    registry = SourceRegistry()
    sources = _record_sec_sources(
        "CIK: 0000320193; accession_number: 0000320193-26-000020; "
        "accession_number: 0000320193-25-000011",
        {},
        "get_recent_filings",
        registry,
    )
    assert len(sources) == 2
    assert str(sources[0].url) == (
        "https://www.sec.gov/Archives/edgar/data/320193/000032019326000020"
    )
    assert all(source.kind == "sec_filing" for source in sources)
