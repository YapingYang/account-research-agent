"""Approved export tool and Markdown rendering tests."""

from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace

from account_research.observability.tokens import TokenReport
from account_research.schemas.account import AccountRecord, Claim, Source
from account_research.tools.export_account import (
    ExportDiagnostics,
    make_export_account_tool,
    render_brief,
)


def _record() -> AccountRecord:
    source = Source(
        id="S1",
        url="https://example.com/filing",
        title="Annual report",
        retrieved_at="2026-11-02T00:00:00Z",
        kind="sec_filing",
    )
    claim = Claim(text="Acme provides finance automation", source_ids=["S1"])
    return AccountRecord(
        company_name="Acme",
        generated_at=datetime.now(UTC),
        summary=claim,
        signals=[claim],
        priorities=[claim],
        pain_points=[claim],
        talking_points=[claim],
        financial_highlights=[claim],
        sources=[source],
        flags=["SEC filings unavailable"],
        model="gemini:gemini-3.7-flash",
    )


def _tool(record: object, output_dir: Path):
    return make_export_account_tool(
        SimpleNamespace(record=record, export_paths=[]),
        output_dir,
        ExportDiagnostics("gemini:gemini-3.7-flash", TokenReport(), perf_counter()),
    )


def test_brief_rendering_includes_claims_ids_sources_and_flags() -> None:
    brief = render_brief(_record())
    assert "## Flags" in brief
    assert "SEC filings unavailable" in brief
    assert "Acme provides finance automation [sources: S1]" in brief
    assert "Annual report" in brief
    assert "https://example.com/filing" in brief
    assert "sec_filing" in brief


def test_export_writes_record_only_from_pending_record(tmp_path: Path) -> None:
    record = _record()
    tool = _tool(record, tmp_path)
    assert tool.args == {}
    result = tool.invoke({})
    brief = tmp_path / "acme" / "brief.md"
    account = tmp_path / "acme" / "account.json"
    report = tmp_path / "acme" / "run-report.md"
    assert "Exported approved account files" in str(getattr(result, "content", result))
    assert brief.exists() and account.exists() and report.exists()
    assert '"company_name": "Acme"' in account.read_text()
    assert "finance automation" in brief.read_text()


def test_export_without_pending_record_returns_error_and_writes_nothing(tmp_path: Path) -> None:
    result = _tool(None, tmp_path).invoke({})
    assert "no pending AccountRecord" in str(getattr(result, "content", result))
    assert list(tmp_path.iterdir()) == []


def test_invalid_account_record_writes_nothing(tmp_path: Path) -> None:
    invalid = {"company_name": "", "sources": "not-a-list"}
    result = _tool(invalid, tmp_path).invoke({})
    assert "pending AccountRecord is invalid" in str(getattr(result, "content", result))
    assert list(tmp_path.iterdir()) == []
