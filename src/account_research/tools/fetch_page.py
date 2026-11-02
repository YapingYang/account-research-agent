"""Safe, bounded page extraction using Tavily."""

import ipaddress
import socket
from urllib.parse import urlsplit

from langchain_core.tools import BaseTool, tool
from tavily import TavilyClient  # type: ignore[import-untyped]

from account_research.config import Settings
from account_research.sources.registry import SourceRegistry
from account_research.tools._text import truncate, wrap_untrusted
from account_research.tools.budget import BUDGET_REACHED, ToolCallBudget


def _validate_public_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only public HTTP(S) URLs can be fetched.")
    host = parsed.hostname
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            addresses = [
                ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(host, None)
            ]
        except OSError as exc:
            raise ValueError("URL host could not be resolved.") from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("Only public HTTP(S) URLs can be fetched.")


def make_fetch_page(settings: Settings, registry: SourceRegistry) -> BaseTool:
    """Create bounded page extraction with SSRF checks and source registration."""
    if settings.tavily_api_key is None:
        raise ValueError("TAVILY_API_KEY is required to create page extraction")
    client = TavilyClient(api_key=settings.tavily_api_key.get_secret_value())
    budget = ToolCallBudget(settings.max_page_fetches)

    @tool("fetch_page")
    def fetch(url: str) -> str:
        """Extract bounded text from at most two public HTTP(S) pages per run."""
        if not budget.consume():
            return BUDGET_REACHED
        _validate_public_url(url)
        result = client.extract(
            urls=[url],
            format="text",
            extract_depth="basic",
            timeout=settings.request_timeout_seconds,
        )
        page = (result.get("results") or [{}])[0]
        source = registry.record(url, str(page.get("title", url)), "web_page")
        return wrap_untrusted(
            source, truncate(str(page.get("raw_content", "")), settings.max_page_chars)
        )

    return fetch
