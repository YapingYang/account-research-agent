"""Per-research-run tools and source registry."""

from langchain_core.tools import BaseTool

from account_research.config import Settings
from account_research.sources.registry import SourceRegistry
from account_research.tools.fetch_page import make_fetch_page
from account_research.tools.web_search import make_web_search


def make_research_tools(settings: Settings, registry: SourceRegistry) -> list[BaseTool]:
    """Build compact read-only web tools bound to a single run's source registry."""
    tools: list[BaseTool] = []
    if settings.tavily_api_key:
        tools.extend([make_web_search(settings, registry), make_fetch_page(settings, registry)])
    return tools
