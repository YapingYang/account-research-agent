"""Turn a research run into display rows for the demo UI (no Streamlit imports)."""

from __future__ import annotations

from collections import Counter
from typing import Any

from account_research.sources.sec_metrics import METRIC_LABELS, format_amount, parse_sec_metric

SEC_KINDS = {"sec", "sec_filing"}
SEC_TOOLS = {"search_companies", "get_company_info", "get_company_facts", "get_recent_filings"}


def sec_metrics(sources: list[Any], cited: set[str]) -> list[dict[str, Any]]:
    """Rows for SEC XBRL metric sources, parsed from their registered excerpt."""
    rows = []
    for source in sources:
        metric = parse_sec_metric(source)
        if metric is None:
            continue
        rows.append(
            {
                "Metric": METRIC_LABELS.get(metric.name, metric.name),
                "Value": format_amount(metric.value, metric.unit),
                "Fiscal period": metric.period_label,
                "Period end": metric.period_end,
                "Form": metric.form,
                "Source": metric.source_id,
                "Cited in brief": metric.source_id in cited,
                "Link": metric.url,
            }
        )
    return rows


def sec_filings(sources: list[Any], cited: set[str]) -> list[dict[str, Any]]:
    """Rows for SEC filing sources (10-K, 10-Q, 8-K)."""
    return [
        {
            "Form": source.form_type or "SEC",
            "Filed": source.published_date.isoformat() if source.published_date else "undated",
            "Source": source.id,
            "Cited in brief": source.id in cited,
            "Link": str(source.url),
        }
        for source in sources
        if source.kind in SEC_KINDS
        and "/Archives/edgar/data/" in str(source.url)
        and parse_sec_metric(source) is None
    ]


def source_type(source: Any) -> str:
    if source.kind in SEC_KINDS:
        return "SEC EDGAR"
    if source.kind == "web_page":
        return "Web page (read)"
    return "Search result"


def source_rows(sources: list[Any], cited: set[str]) -> list[dict[str, Any]]:
    """One row per source the run gathered, cited or not."""
    return [
        {
            "Source": source.id,
            "Cited": source.id in cited,
            "Type": source_type(source),
            "Title": source.title,
            "Date": source.published_date.isoformat() if source.published_date else "undated",
            "Link": str(source.url),
        }
        for source in sources
    ]


def tool_usage(activity: list[dict[str, Any]]) -> dict[str, int]:
    """Count the agent's own research calls (the SEC prefetch is not charged to its budget)."""
    counts: Counter[str] = Counter()
    for step in activity:
        if step.get("phase") != "agent":
            continue
        tool = step.get("tool", "")
        counts["sec" if tool in SEC_TOOLS else tool] += 1
    return dict(counts)
