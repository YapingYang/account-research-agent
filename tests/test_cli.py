"""CLI smoke tests."""

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


def test_research_placeholder() -> None:
    result = runner.invoke(app, ["research", "Acme"])
    assert result.exit_code == 0
    assert "Not implemented yet (milestone 6)" in result.output
