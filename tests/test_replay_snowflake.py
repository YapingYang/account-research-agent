"""Replay real rejected Snowflake claims (captured from live runs) through the citation checker.

No model or network calls: the fixture holds each claim and the text of the sources it cited.
"""

import json
from pathlib import Path
from typing import Any

from account_research.checks.citations import CitationReport, check_citations
from account_research.schemas.account import BriefDraft
from account_research.sources.registry import SourceRegistry

FIXTURE = Path(__file__).parent / "fixtures" / "snowflake_rejected_quotes.json"


def _replay(case: dict[str, Any]) -> CitationReport:
    registry = SourceRegistry()
    for source in sorted(case["sources"], key=lambda item: int(item["id"][1:])):
        while f"S{registry._next_id}" != source["id"]:  # keep the original source IDs
            registry.record(f"https://pad.example/{registry._next_id}", "pad", "search_result")
        registry.record(source["url"], source["title"], source["kind"], content=source["content"])
    draft = BriefDraft(
        company_name="Snowflake",
        summary={
            "text": case["text"],
            "source_ids": case["source_ids"],
            "evidence_quote": case["quote"][:220],
        },
    )
    return check_citations(draft, registry)


def outcomes() -> list[tuple[str, str]]:
    results = []
    for case in json.loads(FIXTURE.read_text()):
        report = _replay(case)
        if report.failed_claims:
            status = "rejected"
        elif report.repaired_claims:
            status = "repaired"
        else:
            status = "passed"
        results.append((status, case["text"]))
    return results


def test_real_paraphrases_are_repaired_and_fabrications_stay_rejected() -> None:
    results = outcomes()
    by_status: dict[str, list[str]] = {}
    for status, text in results:
        by_status.setdefault(status, []).append(text)
    # Close rewordings of a real source sentence now pass with that sentence as evidence.
    repaired = " ".join(by_status["repaired"])
    assert "Non-GAAP EPS of $0.62" in repaired
    assert "product revenue guidance to $6.07 billion" in repaired
    # A pain point quoting the SEC net-loss figure is verified by value.
    assert any(text.startswith("Possible need") for text in by_status["passed"])
    # Quotes the model invented (most cite an 8-K listing with no such words) stay rejected.
    rejected = by_status["rejected"]
    assert "Snowflake's leadership emphasized cost discipline in recent communications." in rejected
    assert sum("finance automation" in text for text in rejected) >= 3
    assert len(results) == 16
    assert (len(by_status["passed"]), len(by_status["repaired"]), len(rejected)) == (4, 4, 8)


def test_wrong_figure_quoted_against_sec_data_is_rejected() -> None:
    registry = SourceRegistry()
    registry.record(
        "https://www.sec.gov/edgar/browse/?CIK=1640147&metric=NetIncomeLoss",
        "NetIncomeLoss — 10-Q",
        "sec",
        content=(
            "NetIncomeLoss: -487291000.0 USD; period=2026-07-31; fiscal_year=2027; "
            "fiscal_period=Q2."
        ),
    )
    draft = BriefDraft(
        company_name="Snowflake",
        summary={
            "text": "Possible need: tighter cost control after a large quarterly loss.",
            "source_ids": ["S1"],
            "evidence_quote": "Net loss of $612.0 million for Q2 FY2027.",
        },
    )
    assert check_citations(draft, registry).failed_count == 1


if __name__ == "__main__":
    for status, text in outcomes():
        print(f"{status:9} | {text[:100]}")
