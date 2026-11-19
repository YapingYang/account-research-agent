"""Resilient MCP discovery and allowlisted tool wrapping."""

import asyncio
import json
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
from account_research.tools.budget import SEC_BUDGET_REACHED, ToolCallBudget

logger = logging.getLogger(__name__)
_ACCESSION_RE = re.compile(r"\b\d{10}-\d{2}-\d{6}\b|\b\d{18}\b")
_SEC_MCP_WARNING_EMITTED = False
_MAX_FILING_SOURCES = 8


def _record_sec_sources(
    text: str, args: dict[str, Any], tool_name: str, registry: SourceRegistry
) -> list[Any]:
    """Register SEC filing URLs, deriving Archives URLs from CIK/accession pairs."""
    enhanced = _enhanced_sec_source_registration(text, args, registry, tool_name)
    if enhanced:
        return enhanced
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
                "sec",
                content=text,
            )
            for accession in accessions
        ]
    urls = re.findall(r"https?://(?:www\.)?sec\.gov/[^\s<>\"']+", text)
    if urls:
        return [registry.record(url.rstrip(".,);"), tool_name, "sec", content=text) for url in urls]
    fallback_url = (
        f"https://www.sec.gov/edgar/browse/?CIK={cik}"
        if cik.isdigit()
        else "https://www.sec.gov/edgar/"
    )
    forms = re.findall(r"\"(?:form|form_type)\"\s*:\s*\"([^\"]+)\"", text, re.IGNORECASE)
    filing_dates = re.findall(r"filing_date.{0,40}?([0-9]{4}-[0-9]{2}-[0-9]{2})", text, re.IGNORECASE)
    metric_periods = re.findall(r"\"period\"\s*:\s*\"([0-9]{4}-[0-9]{2}-[0-9]{2})\"", text)
    date_for_source = filing_dates[0] if filing_dates else (metric_periods[0] if metric_periods else None)
    if cik_value and date_for_source:
        registry.record(
            f"https://www.sec.gov/Archives/edgar/data/{cik_value.lstrip('0')}/",
            str(tool_name),
            "sec",
            published_date=date_for_source,
            content=text,
            form_type=forms[0] if forms else None,
        )
    return []


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
        message = str(exc)
        if "sec_edgar_mcp" in message or "No module named 'sec_edgar_mcp'" in message:
            global _SEC_MCP_WARNING_EMITTED
            if not _SEC_MCP_WARNING_EMITTED:
                logger.warning(
                    "SEC EDGAR MCP is not installed. Install it with `pip install -e \".[edgar]\"` "
                    "to enable SEC filing tools; continuing with web-only research.",
                    exc_info=False,
                )
                _SEC_MCP_WARNING_EMITTED = True
        else:
            logger.warning("MCP discovery unavailable: %s", exc)
        return [], McpStatus(False, message)


def _wrap_tool(
    tool: BaseTool,
    settings: Settings,
    registry: SourceRegistry,
    budget: ToolCallBudget,
) -> BaseTool:
    """Bound MCP output and tag it as untrusted, recording filing URLs."""

    async def invoke(args: dict[str, Any]) -> str:
        if not budget.consume():
            return SEC_BUDGET_REACHED
        value = await asyncio.wait_for(tool.ainvoke(args), timeout=settings.mcp_timeout_seconds)
        text = str(value)
        structured = _enhanced_sec_source_registration(text, args, registry, tool.name)
        if structured:
            # One tagged block per metric/filing so the model cites the source that holds the value.
            blocks = [wrap_untrusted(source, source.content) for source in structured]
            return truncate("\n".join(blocks), settings.mcp_result_chars)
        sources = _record_sec_sources(text, args, tool.name, registry)
        if not sources:
            source = registry.record("https://www.sec.gov/edgar/", tool.name, "sec", content=text)
        else:
            source = sources[0]
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
        metadata={"sec_budget": budget},
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
        sec_budget = ToolCallBudget(settings.max_sec_calls)
        return [
            _wrap_tool(tool, settings, registry, sec_budget)
            for tool in filter_tools(tools, allowed)
        ], status
    except Exception as exc:  # noqa: BLE001
        logger.warning("MCP allowlist loading failed: %s", exc)
        return [], McpStatus(False, str(exc))


async def prefetch_sec_tools(
    tools: list[BaseTool], registry: SourceRegistry, ticker: str
) -> str:
    """Call core SEC MCP tools before agent invocation and return a compact summary."""
    del registry
    by_name = {tool.name: tool for tool in tools}
    required = ("get_company_info", "get_company_facts", "get_recent_filings")
    missing = [name for name in required if name not in by_name]
    if missing:
        return "SEC prefetch unavailable; missing tools: " + ", ".join(missing)

    outputs: dict[str, str] = {}
    budget = (by_name["get_company_info"].metadata or {}).get("sec_budget")
    calls_before = budget.calls if isinstance(budget, ToolCallBudget) else 0
    calls = (
        ("get_company_info", {"identifier": ticker}),
        ("get_company_facts", {"identifier": ticker}),
        ("get_recent_filings", {"identifier": ticker, "days": 365, "limit": 20}),
    )
    for name, args in calls:
        try:
            value = await asyncio.wait_for(by_name[name].ainvoke(args), timeout=30)
            outputs[name] = truncate(str(value), 3000)
        except Exception as exc:  # noqa: BLE001
            outputs[name] = f"error: {exc}"
    if isinstance(budget, ToolCallBudget):
        # The prefetch runs before the agent starts; it must not spend the agent's SEC budget.
        budget.refund(budget.calls - calls_before)

    return "\n".join((
        f"SEC prefetch for {ticker}:",
        "get_company_info: " + outputs["get_company_info"][:1200],
        "get_company_facts: " + outputs["get_company_facts"][:3000],
        "get_recent_filings: " + outputs["get_recent_filings"][:2000],
    ))


def _parse_sec_sources(text: str, args: dict[str, Any], registry: SourceRegistry, tool_name: str, cik_value: str | None = None) -> list[Any]:
    """Parse SEC sources from structured data in tool text output."""
    parsed_structured: dict[str, Any] | None = None
    try:
        marker = re.search(r"'text': '(\{.*\})'", text, re.DOTALL)
        if marker:
            parsed_structured = json.loads(marker.group(1).replace("\\'", "'"))
    except Exception:
        parsed_structured = None

    if parsed_structured:
        for filing in parsed_structured.get("filings", []):
            accession = str(filing.get("accession_number", ""))
            filing_cik = str(filing.get("cik", cik_value or "")).strip().lstrip("0")
            filing_date = filing.get("filing_date")
            form_type = filing.get("form_type")
            if filing_cik and accession:
                registry.record(
                    f"https://www.sec.gov/Archives/edgar/data/{filing_cik}/{accession.replace('-', '')}/",
                    str(form_type or tool_name),
                    "sec",
                    published_date=filing_date,
                    content=text,
                    form_type=form_type,
                    fiscal_period=filing.get("period_of_report"),
                )
        for metric in parsed_structured.get("metrics", {}).values():
            if isinstance(metric, dict):
                registry.record(
                    "https://www.sec.gov/edgar/browse/?CIK=" + str(parsed_structured.get("cik", "")),
                    str(parsed_structured.get("name", tool_name)),
                    "sec",
                    published_date=metric.get("period"),
                    content=text,
                    form_type=metric.get("form"),
                    fiscal_period=metric.get("fiscal_period"),
                )
        if parsed_structured.get("filings") or parsed_structured.get("metrics"):
            return [source for source in registry.all() if source.kind == "sec"]
    filing_dates = re.findall(r"filing_date.{0,40}?([0-9]{4}-[0-9]{2}-[0-9]{2})", text, re.IGNORECASE)
    metric_periods = re.findall(r"\"period\"\s*:\s*\"([0-9]{4}-[0-9]{2}-[0-9]{2})\"", text)
    forms = re.findall(r"\"(?:form|form_type)\"\s*:\s*\"([^\"]+)\"", text, re.IGNORECASE)
    date_for_source = filing_dates[0] if filing_dates else (metric_periods[0] if metric_periods else None)
    if cik_value and date_for_source:
        registry.record(
            f"https://www.sec.gov/Archives/edgar/data/{cik_value.lstrip('0')}/",
            str(tool_name),
            "sec",
            published_date=date_for_source,
            content=text,
            form_type=forms[0] if forms else None,
        )
    return []


def _unwrap_mcp_payload(text: str) -> list[dict[str, Any]]:
    """Extract JSON payload dictionaries from MCP text or wrapped content blocks.

    langchain-mcp-adapters returns a list of content blocks; str() of it is a Python repr, so the
    inner JSON carries repr escapes (e.g. a literal backslash-n). Parse the repr first, then fall
    back to un-escaping each 'text' field before decoding it as JSON.
    """
    import ast

    texts: list[str] = []
    try:
        outer = ast.literal_eval(text)
    except Exception:  # noqa: BLE001
        outer = None
    if outer is not None:
        candidates = outer if isinstance(outer, list) else [outer]
        texts = [
            candidate["text"]
            for candidate in candidates
            if isinstance(candidate, dict) and isinstance(candidate.get("text"), str)
        ]
    if not texts:
        for match in re.finditer(r"'text':\s*'((?:\\.|[^'])*)'", text, re.DOTALL):
            try:
                texts.append(ast.literal_eval("'" + match.group(1) + "'"))
            except Exception:  # noqa: BLE001
                texts.append(match.group(1))
    if not texts and text.lstrip().startswith("{"):
        texts = [text]
    payloads: list[dict[str, Any]] = []
    for item in texts:
        try:
            parsed = json.loads(item)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(parsed, dict):
            payloads.append(parsed)
    return payloads


def _enhanced_sec_source_registration(text: str, args: dict[str, Any], registry: SourceRegistry, tool_name: str) -> list[Any]:
    """Register SEC sources with enhanced parsing for metrics and filings."""
    for payload in _unwrap_mcp_payload(text):
        cik = str(payload.get("cik", args.get("identifier", ""))).strip().lstrip("0")
        metrics = payload.get("metrics")
        if isinstance(metrics, dict):
            registered: list[Any] = []
            for metric_name, metric in metrics.items():
                if not isinstance(metric, dict):
                    continue
                period = metric.get("period")
                form = str(metric.get("form") or "SEC")
                value = metric.get("value")
                unit = metric.get("unit") or ""
                fiscal_period = str(metric.get("fiscal_period") or "")
                fiscal_year = metric.get("fiscal_year")
                label = (
                    f" ({fiscal_period} fiscal {fiscal_year}, {fiscal_period} FY{fiscal_year})"
                    if fiscal_year and fiscal_period and fiscal_period != "FY"
                    else (f" (fiscal {fiscal_year}, FY{fiscal_year})" if fiscal_year else "")
                )
                excerpt = (
                    f"{metric_name}: {value} {unit}; period={period}; form={form}; "
                    f"fiscal_year={fiscal_year}; fiscal_period={fiscal_period}{label}."
                )
                url = f"https://www.sec.gov/edgar/browse/?CIK={cik}&metric={metric_name}&period={period}&form={form}" if cik else f"https://www.sec.gov/edgar/?metric={metric_name}&period={period}&form={form}"
                registered.append(registry.record(url, f"{metric_name} — {form}", "sec", published_date=period, content=excerpt, form_type=form, fiscal_period=fiscal_period, date_source="sec"))
            if registered:
                return registered

        filings = payload.get("filings")
        if isinstance(filings, list):
            sources = []
            for filing in filings:
                if len(sources) >= _MAX_FILING_SOURCES:
                    break
                if not isinstance(filing, dict):
                    continue
                form = str(filing.get("form_type") or "SEC")
                if form not in {"10-K", "10-Q", "8-K"}:
                    continue
                accession = str(filing.get("accession_number") or "")
                filing_cik = str(filing.get("cik") or cik).strip().lstrip("0")
                filing_date = filing.get("filing_date")
                period = filing.get("period_of_report")
                url = f"https://www.sec.gov/Archives/edgar/data/{filing_cik}/{accession.replace('-', '')}/" if filing_cik and accession else "https://www.sec.gov/edgar/"
                excerpt = f"{form} filing filed={filing_date}; period of report={period}; accession={accession}."
                sources.append(registry.record(url, f"{form} — {filing_date or 'undated'}", "sec", published_date=filing_date, content=excerpt, form_type=form, fiscal_period=str(period or ""), date_source="sec"))
            if sources:
                return sources

        company = payload.get("company")
        if isinstance(company, dict):
            company_cik = str(company.get("cik") or cik).strip().lstrip("0")
            if company_cik:
                details = "; ".join(f"{key}={value}" for key, value in company.items())
                return [
                    registry.record(
                        f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={company_cik}",
                        f"SEC company profile — {company.get('name') or tool_name}",
                        "sec",
                        content=f"SEC company profile: {details}.",
                        date_source="sec",
                    )
                ]
    return []
