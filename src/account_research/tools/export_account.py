"""Approved export tool for validated account records."""

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ValidationError

from account_research.observability.tokens import TokenReport
from account_research.schemas.account import AccountRecord, Claim


class _NoArguments(BaseModel):
    """The export tool deliberately accepts no model-provided record payload."""


@dataclass
class ExportDiagnostics:
    """Live metrics rendered with the exported record."""

    model: str
    token_report: TokenReport
    started_at: float
    rate_limit_waits: int = 0
    rate_limit_wait_seconds: float = 0.0
    rate_limit_headers: dict[str, str] = field(default_factory=dict)
    skills_read: list[str] = field(default_factory=list)
    claims_failed: int = 0
    claims_dropped: int = 0


def render_brief(record: AccountRecord) -> str:
    """Render all record sections and source details as a Markdown brief."""
    lines: list[str] = []
    if record.flags:
        lines.extend(["## Flags", *(f"- {flag}" for flag in record.flags), ""])
    lines.extend([f"# {record.company_name}", ""])
    if record.domain:
        lines.extend([f"**Domain:** {record.domain}", ""])
    if record.industry:
        lines.extend([f"**Industry:** {record.industry}", ""])
    if record.is_public is not None:
        lines.extend([f"**Public:** {'Yes' if record.is_public else 'No'}", ""])

    def add_claims(title: str, claims: list[Claim] | Claim | None) -> None:
        values = [claims] if isinstance(claims, Claim) else list(claims or [])
        lines.extend([f"## {title}", ""])
        if not values:
            lines.extend(["_No sourced claims._", ""])
            return
        lines.extend(f"- {claim.text} [sources: {', '.join(claim.source_ids)}]" for claim in values)
        lines.append("")

    add_claims("Summary", record.summary)
    for title, section in (
        ("Financial highlights", record.financial_highlights),
        ("Signals", record.signals),
        ("Priorities", record.priorities),
        ("Pain points", record.pain_points),
        ("Talking points", record.talking_points),
    ):
        add_claims(title, section)
    lines.extend(["## Sources", ""])
    if record.sources:
        lines.extend(
            f"- **{source.id}** — {source.title} ({source.kind}): {source.url}"
            for source in record.sources
        )
    else:
        lines.append("_No sources._")
    lines.append("")
    return "\n".join(lines)


def _run_report(record: AccountRecord, diagnostics: ExportDiagnostics) -> str:
    elapsed = max(0.0, time.perf_counter() - diagnostics.started_at)
    rows = [
        ("Model", diagnostics.model),
        ("Model calls", str(diagnostics.token_report.model_calls)),
        ("Input tokens", str(diagnostics.token_report.input_tokens)),
        ("Output tokens", str(diagnostics.token_report.output_tokens)),
        ("Largest request input", str(diagnostics.token_report.largest_request_input_tokens)),
        (
            "Largest request estimate",
            str(diagnostics.token_report.largest_request_estimate_tokens),
        ),
        ("Rate-limit waits", str(diagnostics.rate_limit_waits)),
        ("Total wait seconds", f"{diagnostics.rate_limit_wait_seconds:.1f}"),
        ("Runtime seconds", f"{elapsed:.1f}"),
        ("Claims passed", str(record_claim_count(record))),
        ("Claims failed", str(diagnostics.claims_failed)),
        ("Claims dropped", str(diagnostics.claims_dropped)),
        ("Skills read", ", ".join(diagnostics.skills_read) or "(none)"),
        ("Flags", "; ".join(record.flags) or "(none)"),
    ]
    table = ["# Research run report", "", "| Metric | Value |", "|---|---|"]
    table.extend(f"| {name} | {value} |" for name, value in rows)
    table.extend(
        [
            "",
            "## Latest rate-limit headers",
            "",
            "```json",
            json.dumps(diagnostics.rate_limit_headers, indent=2, sort_keys=True),
            "```",
            "",
        ]
    )
    return "\n".join(table)


def record_claim_count(record: AccountRecord) -> int:
    """Count claims retained in the validated record."""
    return sum(
        len(value) if isinstance(value, list) else int(value is not None)
        for value in (
            record.summary,
            record.financial_highlights,
            record.signals,
            record.priorities,
            record.pain_points,
            record.talking_points,
        )
    )


def make_export_account_tool(
    pending_record: Any,
    output_dir: Path,
    diagnostics: ExportDiagnostics,
) -> StructuredTool:
    """Create an export tool that writes only the pending validated record."""

    def export_account() -> str:
        """Export the pending record after the human approval interrupt is approved."""
        candidate = getattr(pending_record, "record", None)
        if candidate is None:
            return "Error: no pending AccountRecord is available to export."
        try:
            record = AccountRecord.model_validate(
                candidate.model_dump() if isinstance(candidate, AccountRecord) else candidate
            )
        except (ValidationError, TypeError, ValueError) as exc:
            return f"Error: pending AccountRecord is invalid; nothing was written: {exc}"

        slug = re.sub(r"[^a-z0-9]+", "-", record.company_name.casefold()).strip("-") or "company"
        target = output_dir / slug
        brief_path = target / "brief.md"
        account_path = target / "account.json"
        report_path = target / "run-report.md"
        brief_text = render_brief(record)
        account_text = record.model_dump_json(indent=2) + "\n"
        report_text = _run_report(record, diagnostics)
        target.mkdir(parents=True, exist_ok=True)
        payloads = (
            (brief_path, brief_text),
            (account_path, account_text),
            (report_path, report_text),
        )
        temporary: list[tuple[Path, Path]] = []
        try:
            for path, content in payloads:
                temp = path.with_suffix(path.suffix + ".tmp")
                temp.write_text(content, encoding="utf-8")
                temporary.append((temp, path))
            for temp, path in temporary:
                temp.replace(path)
        finally:
            for temp, _ in temporary:
                temp.unlink(missing_ok=True)
        pending_record.export_paths = [brief_path, account_path, report_path]
        return "Exported approved account files:\n" + "\n".join(
            map(str, pending_record.export_paths)
        )

    return StructuredTool.from_function(
        export_account,
        name="export_account",
        description=(
            "Export the validated pending AccountRecord after human approval. Takes no arguments; "
            "never supply account data to this tool."
        ),
        args_schema=_NoArguments,
        return_direct=True,
    )
