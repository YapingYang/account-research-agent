"""Offline tests for the isolated model spike."""

import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from account_research.config import Settings
from account_research.skills.loader import agent_skill_paths
from account_research.spike.run import run_spike, score_trial, write_results
from account_research.spike.tools import lookup_company_fact


def passing_messages() -> list[AIMessage | ToolMessage]:
    return [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "read_file",
                    "args": {"file_path": "/skills/fact-format/SKILL.md"},
                    "id": "read1",
                    "type": "tool_call",
                },
                {
                    "name": "lookup_company_fact",
                    "args": {"company": "Acme Corp"},
                    "id": "fact1",
                    "type": "tool_call",
                },
            ],
        ),
        ToolMessage(content="skill", tool_call_id="read1"),
        ToolMessage(
            content='{"fact":"Acme Corp was founded in 1999","source_id":"fixture-001"}',
            tool_call_id="fact1",
        ),
        AIMessage(content="FACT: Acme Corp was founded in 1999 [source: fixture-001]"),
    ]


def test_fixture_tool() -> None:
    assert lookup_company_fact("Acme Corp") == {
        "fact": "Acme Corp was founded in 1999",
        "source_id": "fixture-001",
    }
    assert lookup_company_fact("Other Inc") == {"error": "not found"}


def test_scores_passing_and_individual_failures() -> None:
    assert score_trial(passing_messages()) == {
        "tool_called": True,
        "skill_read": True,
        "format_ok": True,
    }
    assert score_trial([AIMessage(content="not formatted")]) == {
        "tool_called": False,
        "skill_read": False,
        "format_ok": False,
    }
    called_only = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "lookup_company_fact",
                    "args": {"company": "Acme Corp"},
                    "id": "x",
                    "type": "tool_call",
                }
            ],
        ),
        AIMessage(content="FACT: fact [source: fixture-001]"),
    ]
    assert score_trial(called_only) == {"tool_called": True, "skill_read": False, "format_ok": True}
    read_only = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "read_file",
                    "args": {"file_path": "fact-format/SKILL.md"},
                    "id": "x",
                    "type": "tool_call",
                }
            ],
        ),
        AIMessage(content="FACT: fact [source: wrong]"),
    ]
    assert score_trial(read_only) == {"tool_called": False, "skill_read": True, "format_ok": False}


def test_real_agent_skills_exclude_spike() -> None:
    assert all("spike" not in str(path) for path in agent_skill_paths())


def test_write_results_creates_valid_json_and_markdown(tmp_path: Path) -> None:
    results = {
        "trials_per_model": 1,
        "models": {
            "groq:test": {
                "trials": [
                    {
                        "tool_called": True,
                        "skill_read": True,
                        "format_ok": True,
                        "latency_seconds": 1.2,
                        "total_tokens": 12,
                        "error": None,
                    }
                ]
            }
        },
        "skipped_models": {},
    }
    write_results(results, tmp_path)
    assert json.loads((tmp_path / "model-spike-results.json").read_text()) == results
    markdown = (tmp_path / "model-spike-results.md").read_text()
    assert "groq:test" in markdown and "1/1" in markdown


def test_markdown_includes_header_limits_and_token_budget(tmp_path: Path) -> None:
    results = {
        "trials_per_model": 1,
        "groq_catalog": [{"id": "openai/gpt-oss-20b", "tool_calling": "Yes"}],
        "models": {
            "groq:openai/gpt-oss-20b": {
                "trials": [
                    {
                        "tool_called": True,
                        "skill_read": True,
                        "format_ok": True,
                        "latency_seconds": 1.0,
                        "total_tokens": 100,
                        "error": None,
                    }
                ],
                "rate_limits": {"rpm": "30", "tpm": "6000", "tpd": "100000"},
                "estimated_runs_per_day": 1000,
            }
        },
        "skipped_models": {},
    }
    write_results(results, tmp_path)
    markdown = (tmp_path / "model-spike-results.md").read_text()
    assert "Current Groq model catalog" in markdown
    assert "Median tokens per trivial run: 100" in markdown
    assert "Estimated runs/day at the daily token limit: 1000" in markdown


def test_spike_skips_gemini_without_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "account_research.spike.run._available_groq_models", lambda _settings: set()
    )
    results = run_spike(
        Settings(_env_file=None),
        ["gemini:gemini-3.7-flash"],
        trials=1,
        pause_seconds=0,
        output_dir=tmp_path,
    )
    assert results["skipped_models"] == {
        "gemini:gemini-3.7-flash": "GOOGLE_API_KEY is not configured"
    }
