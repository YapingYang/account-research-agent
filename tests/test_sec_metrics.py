"""SEC figures are added to the brief and pass the same citation checks as model claims."""

from account_research.checks.citations import check_citations
from account_research.schemas.account import BriefDraft
from account_research.sources.registry import SourceRegistry
from account_research.sources.sec_metrics import add_sec_highlights


def _metric(
    registry: SourceRegistry,
    name: str,
    value: float,
    unit: str,
    period: str,
    form: str,
    fy: int,
    fp: str,
) -> None:
    registry.record(
        f"https://www.sec.gov/edgar/browse/?CIK=1640147&metric={name}&period={period}",
        f"{name} — {form}",
        "sec",
        content=(
            f"{name}: {value} {unit}; period={period}; form={form}; fiscal_year={fy}; "
            f"fiscal_period={fp} ({fp} fiscal {fy}, {fp} FY{fy})."
        ),
    )


def _registry() -> SourceRegistry:
    registry = SourceRegistry()
    _metric(registry, "Assets", 8690459000.0, "USD", "2026-07-31", "10-Q", 2027, "Q2")
    _metric(registry, "StockholdersEquity", 1924102000.0, "USD", "2026-01-31", "10-K", 2026, "FY")
    _metric(registry, "NetIncomeLoss", -487291000.0, "USD", "2026-07-31", "10-Q", 2027, "Q2")
    _metric(registry, "EarningsPerShareBasic", -1.4, "USD/shares", "2026-07-31", "10-Q", 2027, "Q2")
    registry.record(
        "https://example.com/call", "Call recap", "web_page", content="CFO stressed discipline."
    )
    return registry


def test_latest_sec_figures_are_added_and_pass_citation_checks() -> None:
    registry = _registry()
    draft = BriefDraft(
        company_name="Snowflake",
        summary={"text": "Snowflake sells data software", "source_ids": ["S5"]},
    )
    enriched = add_sec_highlights(draft, registry.all())
    texts = [claim.text for claim in enriched.financial_highlights]
    assert texts == [
        "Net loss was $487.29M for Q2 FY2027 (SEC 10-Q).",
        "EPS (basic) was -$1.40/share for Q2 FY2027 (SEC 10-Q).",
        "Total assets were $8.69B as of 2026-07-31 (Q2 FY2027, SEC 10-Q).",
    ]
    report = check_citations(enriched, registry)
    assert report.failed_count == 0


def test_metrics_the_model_already_cited_are_not_duplicated() -> None:
    registry = _registry()
    draft = BriefDraft(
        company_name="Snowflake",
        summary={"text": "Snowflake sells data software", "source_ids": ["S5"]},
        financial_highlights=[
            {"text": "Net loss was $487.3 million in Q2 FY2027.", "source_ids": ["S3"]}
        ],
    )
    enriched = add_sec_highlights(draft, registry.all())
    assert sum("Net loss" in claim.text for claim in enriched.financial_highlights) == 1


def test_no_sec_sources_leaves_draft_unchanged() -> None:
    registry = SourceRegistry()
    registry.record("https://example.com/a", "A", "web_page", content="text")
    draft = BriefDraft(
        company_name="Acme", summary={"text": "Acme sells software", "source_ids": ["S1"]}
    )
    assert add_sec_highlights(draft, registry.all()) is draft
