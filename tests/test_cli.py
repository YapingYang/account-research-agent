"""CLI smoke tests."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from account_research.interfaces.cli import app

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0


def test_skills() -> None:
    result = runner.invoke(app, ["skills"])
    assert result.exit_code == 0
    assert "account-brief" in result.output


def test_research_writes_account_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from account_research.agent.run import ResearchRun
    from account_research.checks.citations import CitationReport
    from account_research.config import Settings
    from account_research.mcp.client import McpStatus
    from account_research.observability.tokens import TokenReport
    from account_research.schemas.account import AccountRecord, Claim

    claim = Claim(text="Example company", source_ids=["S1"])
    fake = ResearchRun(
        record=AccountRecord(
            company_name="Acme",
            summary=claim,
            generated_at=datetime.now(UTC),
            model="test",
        ),
        token_report=TokenReport(model_calls=1, input_tokens=10, output_tokens=5),
        citations=CitationReport([claim], [], ["S1"], ["S1"]),
        elapsed_seconds=0.1,
        skills_read=["account-brief"],
        mcp_status=McpStatus(True),
        rate_limit_waits=0,
        rate_limit_wait_seconds=0.0,
        claims_failed=0,
        claims_dropped=0,
    )
    monkeypatch.setattr("account_research.agent.run.run_research", lambda *a, **kw: fake)
    monkeypatch.setattr(
        "account_research.interfaces.cli.get_settings",
        lambda: Settings(output_dir=tmp_path, _env_file=None),
    )
    result = runner.invoke(app, ["research", "Acme"])
    assert result.exit_code == 0
    assert "Saved" in result.output
    assert '"company_name": "Acme"' in (tmp_path / "draft_acme.json").read_text()
