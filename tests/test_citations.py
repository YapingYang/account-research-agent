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
