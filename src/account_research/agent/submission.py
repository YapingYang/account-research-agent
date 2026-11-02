"""Validated submit_draft tool and per-run draft storage."""

from dataclasses import dataclass
from typing import Annotated, Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, StructuredTool
from langgraph.types import Command
from pydantic import create_model

from account_research.checks.citations import CitationReport, check_citations, to_account_record
from account_research.schemas.account import AccountRecord, BriefDraft
from account_research.sources.registry import SourceRegistry


@dataclass
class DraftSubmission:
    """Mutable result populated by submit_draft."""

    record: AccountRecord | None = None
    report: CitationReport | None = None
    resubmissions: int = 0
    failed_claims: int = 0
    dropped_claims: int = 0


def make_submit_draft_tool(
    registry: SourceRegistry,
    store: DraftSubmission,
    *,
    model: str,
    initial_flags: list[str] | None = None,
) -> StructuredTool:
    """Create the one-repair citation-checking submit_draft tool."""

    tool_args = create_model(
        "SubmitDraftArgs",
        __base__=BriefDraft,
        tool_call_id=(Annotated[str, InjectedToolCallId], ...),
    )

    def submit_draft(
        tool_call_id: Annotated[str, InjectedToolCallId], **values: Any
    ) -> str | Command[Any]:
        if store.record is not None:
            return "Draft already accepted."
        draft = BriefDraft.model_validate(values)
        report = check_citations(draft, registry)
        store.failed_claims += report.failed_count
        if not report.failed_claims:
            store.report = report
            store.record = to_account_record(
                draft, report, registry, model=model, flags=list(initial_flags or [])
            )
            return Command(
                goto="__end__",
                update={
                    "messages": [ToolMessage(content="Draft accepted.", tool_call_id=tool_call_id)]
                },
            )
        if store.resubmissions == 0:
            store.resubmissions = 1
            failed = "\n".join(
                f"- {item.section}: {item.claim.text!r}: {item.reason}"
                for item in report.failed_claims
            )
            valid_ids = ", ".join(report.valid_source_ids) or "(none)"
            return (
                f"These claims failed citation checks:\n{failed}\n"
                f"Valid source IDs: {valid_ids}. Fix these claims and call submit_draft again, "
                "or remove them."
            )
        store.report = report
        store.record = to_account_record(
            draft, report, registry, model=model, flags=list(initial_flags or [])
        )
        count = report.failed_count
        store.dropped_claims = count
        return Command(
            goto="__end__",
            update={
                "messages": [
                    ToolMessage(
                        content=f"Draft accepted with {count} claims removed.",
                        tool_call_id=tool_call_id,
                    )
                ]
            },
        )

    return StructuredTool.from_function(
        submit_draft,
        name="submit_draft",
        description=(
            "Submit the completed account brief for citation validation. Arguments must follow "
            "the BriefDraft schema. Call this tool when ready; do not write the brief as a message."
        ),
        args_schema=tool_args,
    )
