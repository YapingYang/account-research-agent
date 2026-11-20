"""Pure source-ID citation validation and record assembly tests."""

from datetime import UTC

from account_research.checks.citations import check_citations, to_account_record
from account_research.schemas.account import BriefDraft, Claim
from account_research.sources.registry import SourceRegistry


def make_draft(source_ids: list[str]) -> BriefDraft:
    return BriefDraft(
        company_name="Acme",
        summary={"text": "Acme makes software", "source_ids": source_ids},
    )


def make_registry() -> SourceRegistry:
    registry = SourceRegistry()
    registry.record("https://example.com/a", "A", "search_result")
    registry.record("https://example.com/b", "B", "web_page")
    return registry


def test_citations_pass_for_known_ids() -> None:
    registry = make_registry()
    report = check_citations(make_draft(["S1"]), registry)
    assert report.passed_count == 1
    assert report.failed_count == 0
    assert report.valid_source_ids == ["S1", "S2"]
    assert report.cited_source_ids == ["S1"]


def test_citations_report_missing_ids() -> None:
    registry = make_registry()
    claim = Claim.model_construct(text="No citation", source_ids=[])
    draft = make_draft(["S1"])
    draft.summary = claim
    report = check_citations(draft, registry)
    assert report.failed_claims[0].reason == "no source_ids"


def test_citations_report_unknown_id() -> None:
    report = check_citations(make_draft(["S99"]), make_registry())
    assert report.failed_claims[0].reason == "unknown source ID(s): S99"


def test_record_uses_only_sources_cited_by_passed_claims() -> None:
    registry = make_registry()
    draft = make_draft(["S2"])
    report = check_citations(draft, registry)
    record = to_account_record(draft, report, registry, model="test")
    assert [source.id for source in record.sources] == ["S2"]
    assert record.generated_at.tzinfo == UTC


def test_record_drops_failed_claim_and_flags_it() -> None:
    registry = make_registry()
    draft = BriefDraft.model_construct(
        company_name="Acme",
        summary=Claim.model_construct(text="bad", source_ids=["S99"]),
        signals=[Claim(text="Good", source_ids=["S1"])],
    )
    report = check_citations(draft, registry)
    record = to_account_record(draft, report, registry, model="test")
    assert record.summary is None
    assert len(record.signals) == 1
    assert [source.id for source in record.sources] == ["S1"]
    assert record.flags and "Dropped summary claim" in record.flags[0]


def _sec_registry() -> SourceRegistry:
    registry = SourceRegistry()
    registry.record(
        "https://www.sec.gov/edgar/browse/?CIK=1640147&metric=NetIncomeLoss",
        "NetIncomeLoss — 10-Q",
        "sec",
        content=(
            "NetIncomeLoss: -487296000 USD; period=2026-07-31; form=10-Q; fiscal_period=Q2. "
            "Assets: 8691234000 USD. EarningsPerShareBasic: -1.4 USD/shares. GrossMargin: 74%"
        ),
    )
    return registry


def _value_claim(text: str) -> BriefDraft:
    return BriefDraft(company_name="Snowflake", summary={"text": text, "source_ids": ["S1"]})


def test_rounded_claim_values_match_raw_sec_numbers() -> None:
    text = (
        "Snowflake reported a net loss of $487.3 million in Q2, total assets of $8.69 billion, "
        "EPS of -$1.40 and a 74% margin."
    )
    report = check_citations(_value_claim(text), _sec_registry())
    assert report.failed_count == 0


def test_claim_value_not_in_source_is_rejected() -> None:
    report = check_citations(_value_claim("Net loss was $512.0 million."), _sec_registry())
    assert report.failed_claims[0].reason == (
        "value mismatch: missing value(s) in source text: 512.0m"
    )


def test_dates_in_source_do_not_support_claim_values() -> None:
    report = check_citations(_value_claim("Snowflake spent $31 on coffee."), _sec_registry())
    assert report.failed_count == 1


def _quote_claim(quote: str, text: str = "Leadership stressed cost control.") -> BriefDraft:
    return BriefDraft(
        company_name="Snowflake",
        summary={"text": text, "source_ids": ["S1"], "evidence_quote": quote},
    )


def _web_registry() -> SourceRegistry:
    registry = SourceRegistry()
    registry.record(
        "https://example.com/call",
        "Call",
        "search_result",
        content=(
            "Snowflake used the conference to present a message of faster growth, stronger AI "
            "adoption and tighter cost control. “We delivered this accelerating growth while "
            "expanding operating margin,” the CFO said."
        ),
    )
    return registry


def test_quote_with_ellipses_and_wrapping_quotes_is_found() -> None:
    quote = '"...faster growth, stronger AI adoption and tighter cost control."'
    assert check_citations(_quote_claim(quote), _web_registry()).failed_count == 0


def test_quote_fragments_must_appear_in_order() -> None:
    ok = '"We delivered this accelerating growth ... expanding operating margin"'
    assert check_citations(_quote_claim(ok), _web_registry()).failed_count == 0
    # Reordered fragments are not an exact match, but every word comes from one real sentence,
    # so the claim is kept with that sentence as its evidence instead of the reordered quote.
    swapped = '"expanding operating margin ... We delivered this accelerating growth"'
    report = check_citations(_quote_claim(swapped), _web_registry())
    assert report.failed_count == 0
    assert report.repaired_claims[("summary", 0)].evidence_quote.startswith("\u201cWe delivered")


def test_invented_quote_is_still_rejected() -> None:
    report = check_citations(_quote_claim('"We will cut headcount by 20%"'), _web_registry())
    assert report.failed_claims[0].reason == "evidence_quote not found in cited source text"


def test_quarter_and_fiscal_year_can_appear_separately() -> None:
    registry = SourceRegistry()
    registry.record(
        "https://www.sec.gov/x",
        "EPS",
        "sec",
        content="EarningsPerShareBasic: -1.4 USD/shares; fiscal_year=2027; fiscal_period=Q2.",
    )
    good = BriefDraft(
        company_name="Snowflake",
        summary={"text": "EPS for Q2 fiscal 2027 was -$1.40.", "source_ids": ["S1"]},
    )
    assert check_citations(good, registry).failed_count == 0
    wrong_year = BriefDraft(
        company_name="Snowflake",
        summary={"text": "EPS for Q2 fiscal 2026 was -$1.40.", "source_ids": ["S1"]},
    )
    assert "fy2026" in check_citations(wrong_year, registry).failed_claims[0].reason


def test_quote_repair_hint_points_at_real_source_sentence() -> None:
    from account_research.agent.submission import _closest_evidence

    registry = _web_registry()
    claim = Claim(
        text="Leadership stressed cost control.",
        source_ids=["S1"],
        evidence_quote="Snowflake prioritized tighter cost control",
    )
    hint = _closest_evidence(claim, "evidence_quote not found in cited source text", registry)
    assert "faster growth, stronger AI adoption and tighter cost control" in hint
    assert _closest_evidence(claim, "value mismatch: x", registry) == ""


def _submit_tool(seller_context: str | None):  # type: ignore[no-untyped-def]
    from account_research.agent.submission import DraftSubmission, make_submit_draft_tool

    store = DraftSubmission()
    tool = make_submit_draft_tool(
        _web_registry(), store, model="test", seller_context=seller_context
    )
    return tool, store


def _thin_draft() -> dict:  # type: ignore[type-arg]
    return {
        "company_name": "Snowflake",
        "summary": {
            "text": "Snowflake stressed cost control.",
            "source_ids": ["S1"],
            "evidence_quote": "stronger AI adoption and tighter cost control",
        },
    }


def test_empty_seller_sections_get_one_request_to_fill_them() -> None:
    tool, store = _submit_tool("Meeting their CFO about finance automation.")
    reply = tool.invoke(_thin_draft())
    assert "These sections are empty: priorities, pain_points, talking_points" in reply
    assert "finance automation" in reply
    assert store.record is None
    # Second submission is accepted even if the model still leaves them empty.
    assert tool.invoke(_thin_draft()).startswith("Draft accepted")
    assert store.record is not None


def test_no_seller_context_accepts_thin_draft_immediately() -> None:
    tool, store = _submit_tool(None)
    assert tool.invoke(_thin_draft()) == "Draft accepted. Call export_account next."
