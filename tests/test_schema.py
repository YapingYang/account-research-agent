"""Account schema tests."""

from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError

from account_research.checks.citations import check_citations, to_account_record
from account_research.schemas.account import AccountRecord, BriefDraft, Claim, Source
from account_research.sources.registry import SourceRegistry


def test_valid_record() -> None:
    record = AccountRecord(
        company_name="Acme",
        generated_at=datetime.now(UTC),
        model="test",
        summary={"text": "Acme builds tools", "source_ids": ["S1"]},
        financial_highlights=[{"text": "Revenue grew", "source_ids": ["S1"]}],
    )
    assert record.company_name == "Acme"


def test_claim_requires_source() -> None:
    with pytest.raises(ValidationError):
        Claim(text="Unsupported", source_ids=[])


def test_source_kind_is_restricted() -> None:
    with pytest.raises(ValidationError):
        Source(
            id="S1",
            url="https://example.com",
            title="Example",
            retrieved_at=datetime.now(UTC),
            kind="other",
        )  # type: ignore[arg-type]


def test_sources_track_published_date_and_freshness() -> None:
    registry = SourceRegistry(freshness_window_days=365)
    source = registry.record(
        "https://example.com/earnings",
        "Example earnings",
        "search_result",
        published_date=datetime.now(UTC) - timedelta(days=30),
    )

    assert source.published_date is not None
    assert source.freshness == "fresh"


def test_recent_claims_with_stale_sources_are_flagged() -> None:
    registry = SourceRegistry(freshness_window_days=365)
    stale = registry.record(
        "https://example.com/old-earnings",
        "Old earnings",
        "search_result",
        published_date=datetime.now(UTC) - timedelta(days=900),
    )
    draft = BriefDraft(
        company_name="Acme",
        summary=Claim(
            text="Acme reported the latest Q2 FY2026 revenue of $100M.",
            source_ids=[stale.id],
        ),
    )

    report = check_citations(draft, registry)
    record = to_account_record(draft, report, registry, model="test")

    assert report.failed_claims == []
    assert any("stale or undated" in flag.lower() for flag in record.flags)


def test_source_dates_are_parsed_from_url_paths() -> None:
    registry = SourceRegistry(freshness_window_days=365)
    source = registry.record(
        "https://example.com/2025/12/03/filing.html",
        "Quarterly results",
        "web_page",
    )

    assert source.published_date == date(2025, 12, 3)
    assert source.freshness_status == "fresh"


def test_undated_market_data_claims_are_rejected() -> None:
    registry = SourceRegistry(freshness_window_days=365)
    source = registry.record(
        "https://example.com/market-cap",
        "Market cap",
        "web_page",
        content="Market cap is $45 billion.",
    )
    draft = BriefDraft(
        company_name="Acme",
        summary=Claim(
            text="Acme market cap is $45 billion.",
            source_ids=[source.id],
        ),
    )

    report = check_citations(draft, registry)
    assert report.failed_claims
    assert any("market cap" in item.reason.lower() or "market-data" in item.reason.lower() for item in report.failed_claims)


def test_claim_support_requires_value_and_period_match_in_source_text() -> None:
    registry = SourceRegistry(freshness_window_days=365)
    source = registry.record(
        "https://example.com/earnings",
        "Quarterly results",
        "web_page",
        published_date=datetime.now(UTC),
        content="Revenue was $1.55 billion in Q2 fiscal 2027; net income was 18%.",
    )
    draft = BriefDraft(
        company_name="Acme",
        summary=Claim(
            text="Acme reported $1.55B revenue in Q2 fiscal 2027 and an 18% margin.",
            source_ids=[source.id],
        ),
    )

    report = check_citations(draft, registry)
    assert report.failed_claims == []


def test_claim_support_detects_missing_value_in_source_text() -> None:
    registry = SourceRegistry(freshness_window_days=365)
    source = registry.record(
        "https://example.com/earnings",
        "Quarterly results",
        "web_page",
        published_date=datetime.now(UTC),
        content="Revenue was $1.55 billion in Q2 fiscal 2027.",
    )
    draft = BriefDraft(
        company_name="Acme",
        summary=Claim(
            text="Acme reported $2.00B revenue in Q2 fiscal 2027 and a 30% margin.",
            source_ids=[source.id],
        ),
    )

    report = check_citations(draft, registry)
    assert report.failed_claims
    assert any("value" in item.reason.lower() for item in report.failed_claims)
