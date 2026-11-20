"""Safe, bounded page extraction using Tavily."""

import ipaddress
import socket
from urllib.parse import urlsplit

from langchain_core.tools import BaseTool, tool
from tavily import TavilyClient  # type: ignore[import-untyped]

from account_research.config import Settings
from account_research.sources.registry import SourceRegistry
from account_research.tools._text import truncate, wrap_untrusted
from account_research.tools.budget import PAGE_BUDGET_REACHED, ToolCallBudget


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


def _main_text(text: str) -> str:
    """Drop menu/navigation lines so the page character budget goes to article prose.

    Keeps lines that read like sentences (six or more words); short lines such as
    "2. Day Gainers" or "Sign in" are site chrome.
    """
    lines = [line.strip() for line in text.splitlines()]
    prose = [line for line in lines if len(line.split()) >= 6]
    return "\n".join(prose) if prose else text


def make_fetch_page(settings: Settings, registry: SourceRegistry) -> BaseTool:
    """Create bounded page extraction with SSRF checks and source registration."""
    if settings.tavily_api_key is None:
        raise ValueError("TAVILY_API_KEY is required to create page extraction")
    client = TavilyClient(api_key=settings.tavily_api_key.get_secret_value())
    budget = ToolCallBudget(settings.max_page_fetches)

    @tool("fetch_page")
    def fetch(url: str) -> str:
        """Extract bounded text from a public HTTP(S) page found by web_search."""
        host = (urlsplit(url).hostname or "").casefold()
        if host == "sec.gov" or host.endswith(".sec.gov"):
            return (
                "fetch_page does not read sec.gov URLs. Cite the SEC source IDs you already have "
                "and use web_search for commentary."
            )
        if not budget.consume():
            return PAGE_BUDGET_REACHED
        _validate_public_url(url)
        result = client.extract(
            urls=[url],
            format="text",
            extract_depth="basic",
            timeout=settings.request_timeout_seconds,
        )
        page = (result.get("results") or [{}])[0]
        raw_content = truncate(_main_text(str(page.get("raw_content", ""))), settings.max_page_chars)
        source = registry.record(url, str(page.get("title", url)), "web_page", content=raw_content)
        return wrap_untrusted(source, raw_content)

    return fetch
