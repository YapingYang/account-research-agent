"""Tavily-backed compact web search."""

import json
from datetime import datetime

from langchain_core.tools import BaseTool, tool
from tavily import TavilyClient  # type: ignore[import-untyped]

from account_research.config import Settings
from account_research.sources.registry import SourceRegistry
from account_research.tools._text import truncate
from account_research.tools.budget import BUDGET_REACHED, ToolCallBudget


def make_web_search(settings: Settings, registry: SourceRegistry) -> BaseTool:
    """Create a per-run search tool with bounded calls and registered sources."""
    if settings.tavily_api_key is None:
        raise ValueError("TAVILY_API_KEY is required to create web search")
    client = TavilyClient(api_key=settings.tavily_api_key.get_secret_value())
    budget = ToolCallBudget(settings.search_budget)

    def _search_kwargs(query: str, *, is_fresh: bool = False) -> dict[str, object]:
        kwargs: dict[str, object] = {
            "query": query,
            "max_results": settings.max_search_results,
            "timeout": settings.request_timeout_seconds,
            "topic": "finance" if is_fresh else settings.default_search_topic,
            "time_range": "year" if is_fresh else settings.default_search_time_range,
        }
        if settings.default_search_time_range == "year" and is_fresh:
            kwargs["time_range"] = "year"
        return kwargs

    @tool("web_search")
    def search(query: str) -> str:
        """Search the web and return compact, source-registered results up to the configured limit.

        Results are kept to the configured count and snippet size.
        """
        if not budget.consume():
            return BUDGET_REACHED
        is_fresh = any(token in query.casefold() for token in ("latest", "earnings", "news", "financial", "quarter", "fiscal", "announc"))
        response = client.search(**_search_kwargs(query, is_fresh=is_fresh))
        results = []
        for item in response.get("results", []):
            url = str(item.get("url", ""))
            published = item.get("published_date") or item.get("publishedDate")
            content = str(item.get("content", ""))
            source = registry.record(
                url,
                str(item.get("title", "")),
                "search_result",
                published_date=published,
                content=truncate(content, settings.snippet_chars),
                date_source="tavily" if published else "none",
            )
            results.append(
                {
                    "id": source.id,
                    "title": source.title,
                    "url": url,
                    "date": source.published_date.isoformat() if source.published_date else "undated",
                    "freshness": source.freshness_status,
                    "snippet": truncate(str(item.get("content", "")), settings.snippet_chars),
                }
            )
        return json.dumps(results, ensure_ascii=False)

    return search
