"""Display-row helpers for the demo UI."""

from account_research.interfaces.ui_data import (
    sec_filings,
    sec_metrics,
    source_rows,
    tool_usage,
)
from account_research.sources.registry import SourceRegistry
from account_research.sources.sec_metrics import format_amount


def _registry() -> SourceRegistry:
    registry = SourceRegistry()
    registry.record(
        "https://www.sec.gov/edgar/browse/?CIK=1640147&metric=NetIncomeLoss",
        "NetIncomeLoss — 10-Q",
        "sec",
        content=(
            "NetIncomeLoss: -487291000.0 USD; period=2026-07-31; form=10-Q; fiscal_year=2027; "
            "fiscal_period=Q2 (Q2 fiscal 2027, Q2 FY2027)."
        ),
    )
    registry.record(
        "https://www.sec.gov/Archives/edgar/data/1640147/000164014726000043/",
        "8-K — 2026-10-02",
        "sec",
        published_date="2026-10-02",
        content="8-K filing filed=2026-10-02; period of report=2026-09-28; accession=x.",
        form_type="8-K",
    )
    registry.record("https://example.com/a", "Earnings recap", "web_page", content="text")
    return registry


def test_format_amount() -> None:
    assert format_amount(8690459000.0, "USD") == "$8.69B"
    assert format_amount(-487291000.0, "USD") == "-$487.29M"
    assert format_amount(-1.4, "USD/shares") == "-$1.40/share"
    assert format_amount(352460000.0, "shares") == "352.46M shares"


def test_sec_rows_split_metrics_and_filings() -> None:
    sources = _registry().all()
    metrics = sec_metrics(sources, cited={"S1"})
    assert metrics[0]["Metric"] == "Net income (loss)"
    assert metrics[0]["Value"] == "-$487.29M"
    assert metrics[0]["Fiscal period"] == "Q2 FY2027"
    assert metrics[0]["Cited in brief"] is True
    filings = sec_filings(sources, cited=set())
    assert [row["Form"] for row in filings] == ["8-K"]


def test_source_rows_include_uncited_sources() -> None:
    rows = source_rows(_registry().all(), cited={"S3"})
    assert [(row["Source"], row["Cited"], row["Type"]) for row in rows] == [
        ("S1", False, "SEC EDGAR"),
        ("S2", False, "SEC EDGAR"),
        ("S3", True, "Web page (read)"),
    ]


def test_tool_usage_ignores_prefetch() -> None:
    activity = [
        {"tool": "SEC prefetch", "phase": "prefetch"},
        {"tool": "web_search", "phase": "agent"},
        {"tool": "web_search", "phase": "agent"},
        {"tool": "get_recent_filings", "phase": "agent"},
    ]
    assert tool_usage(activity) == {"web_search": 2, "sec": 1}
