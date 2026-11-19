"""Validated submit_draft tool and per-run draft storage."""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.tools import StructuredTool

from account_research.checks.citations import CitationReport, check_citations, to_account_record
from account_research.schemas.account import AccountRecord, BriefDraft, Claim
from account_research.sources.registry import SourceRegistry
from account_research.sources.sec_metrics import add_sec_highlights


@dataclass
class DraftSubmission:
    """Mutable result populated by submit_draft."""

    record: AccountRecord | None = None
    report: CitationReport | None = None
    resubmissions: int = 0
    submit_calls: int = 0
    failed_claims: int = 0
    dropped_claims: int = 0
    revisions: int = 0
    export_paths: list[Path] = field(default_factory=list)

    def begin_revision(self) -> bool:
        """Clear a rejected pending record once so the agent can revise it."""
        if self.revisions >= 1:
            return False
        self.revisions += 1
        self.record = None
        self.report = None
        self.resubmissions = 0
        return True


_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
_WORD = re.compile(r"[a-z0-9$%.]+")


def _closest_evidence(claim: Claim, reason: str, registry: SourceRegistry) -> str:
    """Point a failed quote at the real sentence in the cited source that best supports it.

    Without this, the model is told only that its quote was not found and tends to resubmit
    the same paraphrase.
    """
    if "evidence_quote" not in reason:
        return ""
    wanted = set(_WORD.findall(f"{claim.text} {claim.evidence_quote}".casefold()))
    best, best_score = "", 0
    for source in registry.all():
        if source.id not in claim.source_ids:
            continue
        for sentence in _SENTENCE.split(source.content or ""):
            sentence = sentence.strip()
            if len(sentence.split()) < 5:
                continue
            score = len(wanted & set(_WORD.findall(sentence.casefold())))
            if score > best_score:
                best, best_score = sentence, score
    if not best:
        return " (cite a different source or remove the claim)"
    return f'\n  Closest text in the cited source: "{best[:240]}" (copy exact words from it)'


_SELLER_SECTIONS = ("priorities", "pain_points", "talking_points")


def _empty_seller_sections(draft: BriefDraft) -> list[str]:
    return [section for section in _SELLER_SECTIONS if not getattr(draft, section)]


def make_submit_draft_tool(
    registry: SourceRegistry,
    store: DraftSubmission,
    *,
    model: str,
    initial_flags: list[str] | None = None,
    seller_context: str | None = None,
) -> StructuredTool:
    """Create the one-repair citation-checking submit_draft tool."""

    def submit_draft(**values: Any) -> str:
        store.submit_calls += 1
        if store.record is not None:
            return "Draft already accepted."
        draft = add_sec_highlights(BriefDraft.model_validate(values), registry.all())
        report = check_citations(draft, registry)
        store.failed_claims += report.failed_count
        missing = _empty_seller_sections(draft) if seller_context else []
        if not report.failed_claims and not (missing and store.resubmissions == 0):
            store.report = report
            store.record = to_account_record(
                draft, report, registry, model=model, flags=list(initial_flags or [])
            )
            return "Draft accepted. Call export_account next."
        if store.resubmissions == 0:
            store.resubmissions = 1
            failed = "\n".join(
                f"- {item.section}: {item.claim.text!r}: {item.reason}"
                + _closest_evidence(item.claim, item.reason, registry)
                for item in report.failed_claims
            )
            valid_ids = ", ".join(report.valid_source_ids) or "(none)"
            parts = []
            if report.failed_claims:
                parts.append(
                    f"These claims failed citation checks:\n{failed}\n"
                    f"Valid source IDs: {valid_ids}. Fix these claims or remove them."
                )
            if missing:
                parts.append(
                    "These sections are empty: " + ", ".join(missing) + ". The seller context is: "
                    f"{seller_context!r}. Add claims from sources you already have (no new "
                    "research): priorities = goals leadership stated; pain_points = 'Possible "
                    "need: ...' hypotheses; talking_points = 3-5 openers linking a cited fact to "
                    "the seller context. Each cites a source ID and quotes its exact words. "
                    "Leave a section empty only if no gathered source supports it."
                )
            return "\n\n".join(parts) + "\n\nThen call submit_draft again with the full draft."
        store.report = report
        store.record = to_account_record(
            draft, report, registry, model=model, flags=list(initial_flags or [])
        )
        count = report.failed_count
        store.dropped_claims = count
        return f"Draft accepted with {count} claims removed. Call export_account next."

    return StructuredTool.from_function(
        submit_draft,
        name="submit_draft",
        description=(
            "Submit the completed account brief for citation validation. Arguments must follow "
            "the BriefDraft schema. Call this tool when ready; do not write the brief as a message."
        ),
        args_schema=BriefDraft,
    )
