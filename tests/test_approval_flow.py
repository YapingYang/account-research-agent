"""Offline approval, rejection, revision, and no-write-before-approval tests."""

from pathlib import Path
from typing import Any

import pytest
from langgraph.types import Command

from account_research.agent.run import ResearchRunError, run_research
from account_research.config import Settings
from account_research.sources.registry import SourceRegistry


def _draft(talking_point: str) -> dict[str, Any]:
    claim = {"text": talking_point, "source_ids": ["S1"]}
    return {
        "company_name": "Acme",
        "summary": {"text": "Acme makes finance software", "source_ids": ["S1"]},
        "talking_points": [claim],
    }


def _install_agent(monkeypatch: pytest.MonkeyPatch, revised: str = "For finance teams") -> None:
    class FakeAgent:
        def __init__(self, submit: Any, export: Any) -> None:
            self.submit = submit
            self.export = export

        def invoke(self, payload: Any, **_kwargs: Any) -> dict[str, Any]:
            if isinstance(payload, Command):
                decision = payload.resume["decisions"][0]
                if decision["type"] == "reject":
                    self.submit.invoke(_draft(revised))
                    return {"__interrupt__": [object()], "messages": []}
                self.export.invoke({})
                return {"messages": []}
            self.submit.invoke(_draft("Generic talking point"))
            return {"__interrupt__": [object()], "messages": []}

    def fake_build(
        _settings: Settings,
        registry: SourceRegistry,
        _mcp_tools: list[Any],
        *,
        extra_tools: list[Any],
        **_kwargs: Any,
    ) -> FakeAgent:
        registry.record("https://example.com", "Acme evidence", "search_result")
        return FakeAgent(extra_tools[0], extra_tools[1])

    monkeypatch.setattr("account_research.agent.run.build_agent", fake_build)


def test_reject_revision_then_approve_exports_revised_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_agent(monkeypatch, revised="Specific to finance teams")
    choices = iter([(False, "make the talking points more specific to finance teams"), (True, "")])
    previewed: list[str] = []

    def decide(record: Any) -> tuple[bool, str]:
        assert not (tmp_path / "acme" / "brief.md").exists()
        previewed.append(record.talking_points[0].text)
        return next(choices)

    result = run_research(
        Settings(model="gemini:test", mcp_enabled=False, output_dir=tmp_path, _env_file=None),
        "Acme",
        approval_handler=decide,
    )
    assert previewed == ["Generic talking point", "Specific to finance teams"]
    assert result.record.talking_points[0].text == "Specific to finance teams"
    assert "Specific to finance teams" in (tmp_path / "acme" / "brief.md").read_text()
    assert "Generic talking point" not in (tmp_path / "acme" / "brief.md").read_text()
    assert (tmp_path / "acme" / "account.json").exists()


def test_reject_twice_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_agent(monkeypatch)
    decisions = iter([(False, "revise"), (False, "still needs work")])
    with pytest.raises(ResearchRunError, match="Export rejected twice"):
        run_research(
            Settings(model="gemini:test", mcp_enabled=False, output_dir=tmp_path, _env_file=None),
            "Acme",
            approval_handler=lambda _record: next(decisions),
        )
    assert list(tmp_path.iterdir()) == []


def test_auto_approve_exports_without_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_agent(monkeypatch)
    result = run_research(
        Settings(model="gemini:test", mcp_enabled=False, output_dir=tmp_path, _env_file=None),
        "Acme",
        auto_approve=True,
    )
    assert result.output_paths
    assert (tmp_path / "acme" / "account.json").exists()
