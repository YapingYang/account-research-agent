"""Citation validation and draft-to-record conversion."""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime

from account_research.schemas.account import AccountRecord, BriefDraft, Claim
from account_research.sources.registry import SourceRegistry

_CLAIM_SECTIONS = (
    "summary",
    "financial_highlights",
    "signals",
    "priorities",
    "pain_points",
    "talking_points",
)


@dataclass(frozen=True)
class FailedClaim:
    """A draft claim that cannot be resolved to registered sources."""

    section: str
    index: int
    claim: Claim
    reason: str


@dataclass(frozen=True)
class CitationReport:
    """Citation check result, preserving valid claims and their locations."""

    passed_claims: list[Claim]
    failed_claims: list[FailedClaim]
    valid_source_ids: list[str]
    cited_source_ids: list[str]

    @property
    def passed_count(self) -> int:
        return len(self.passed_claims)

    @property
    def failed_count(self) -> int:
        return len(self.failed_claims)


def _claims(draft: BriefDraft) -> Iterator[tuple[str, int, Claim]]:
    for section in _CLAIM_SECTIONS:
        value = getattr(draft, section)
        if section == "summary":
            yield section, 0, value
        else:
            for index, claim in enumerate(value):
                yield section, index, claim


def check_citations(draft: BriefDraft, registry: SourceRegistry) -> CitationReport:
    """Pass claims citing known source IDs and report missing or unknown IDs."""
    known = {source.id for source in registry.all()}
    passed: list[Claim] = []
    failed: list[FailedClaim] = []
    cited: set[str] = set()
    for section, index, claim in _claims(draft):
        source_ids = list(claim.source_ids or [])
        if not source_ids:
            failed.append(FailedClaim(section, index, claim, "no source_ids"))
        else:
            unknown = [source_id for source_id in source_ids if source_id not in known]
            if unknown:
                failed.append(
                    FailedClaim(
                        section, index, claim, "unknown source ID(s): " + ", ".join(unknown)
                    )
                )
            else:
                passed.append(claim)
                cited.update(source_ids)
    return CitationReport(
        passed_claims=passed,
        failed_claims=failed,
        valid_source_ids=[source.id for source in registry.all()],
        cited_source_ids=[source.id for source in registry.all() if source.id in cited],
    )


def to_account_record(
    draft: BriefDraft,
    report: CitationReport,
    registry: SourceRegistry,
    *,
    model: str,
    flags: list[str] | None = None,
) -> AccountRecord:
    """Drop failed claims and attach only cited registry sources."""
    failed_locations = {(item.section, item.index) for item in report.failed_claims}
    out_flags = list(flags or [])
    out_flags.extend(
        f"Dropped {item.section} claim: {item.claim.text} ({item.reason})"
        for item in report.failed_claims
    )

    def keep(section: str, claims: list[Claim]) -> list[Claim]:
        return [
            claim for index, claim in enumerate(claims) if (section, index) not in failed_locations
        ]

    summary = None if ("summary", 0) in failed_locations else draft.summary
    sources = [source for source in registry.all() if source.id in report.cited_source_ids]
    return AccountRecord(
        company_name=draft.company_name,
        domain=draft.domain,
        industry=draft.industry,
        is_public=draft.is_public,
        summary=summary,
        financial_highlights=keep("financial_highlights", draft.financial_highlights),
        signals=keep("signals", draft.signals),
        priorities=keep("priorities", draft.priorities),
        pain_points=keep("pain_points", draft.pain_points),
        talking_points=keep("talking_points", draft.talking_points),
        sources=sources,
        flags=out_flags,
        generated_at=datetime.now(UTC),
        model=model,
    )
