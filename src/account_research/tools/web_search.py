"""Tavily-backed compact web search."""

import json

from langchain_core.tools import BaseTool, tool
from tavily import TavilyClient  # type: ignore[import-untyped]

from account_research.config import Settings
from account_research.sources.registry import SourceRegistry
from account_research.tools._text import truncate


def make_web_search(settings: Settings, registry: SourceRegistry) -> BaseTool:
    """Create a per-run search tool with bounded calls and registered sources."""
    if settings.tavily_api_key is None:
        raise ValueError("TAVILY_API_KEY is required to create web search")
    client = TavilyClient(api_key=settings.tavily_api_key.get_secret_value())
    calls = 0

    @tool("web_search")
    def search(query: str) -> str:
        """Search the web and return compact, source-registered results up to the configured limit.

        Results are kept to the configured count and snippet size.
        """
        nonlocal calls
        if calls >= settings.search_budget:
            return "Search budget reached. Draft with the sources you have."
        calls += 1
        response = client.search(
            query,
            max_results=settings.max_search_results,
            timeout=settings.request_timeout_seconds,
        )
        results = []
        for item in response.get("results", []):
            url = str(item.get("url", ""))
            source = registry.record(url, str(item.get("title", "")), "search_result")
            results.append(
                {
                    "id": source.id,
                    "title": source.title,
                    "url": url,
                    "snippet": truncate(str(item.get("content", "")), settings.snippet_chars),
                }
            )
        return json.dumps(results, ensure_ascii=False)

    return search
