"""Citation-aware submit_draft tool behavior."""

from typing import Any

from account_research.agent.submission import DraftSubmission, make_submit_draft_tool
from account_research.sources.registry import SourceRegistry


def _draft(source_id: str) -> dict[str, Any]:
    return {
        "company_name": "Acme",
        "summary": {"text": "Acme makes software", "source_ids": [source_id]},
    }


def _tool() -> tuple[Any, DraftSubmission]:
    registry = SourceRegistry()
    registry.record("https://example.com", "Acme", "search_result")
    store = DraftSubmission()
    return make_submit_draft_tool(registry, store, model="test"), store


def _call(args: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {"name": "submit_draft", "args": args, "id": call_id, "type": "tool_call"}


def test_submit_accepts_valid_draft_and_stores_once() -> None:
    tool, store = _tool()
    assert "Draft accepted" in str(tool.invoke(_call(_draft("S1"), "1")))
    original = store.record
    assert original is not None
    response = tool.invoke(_call(_draft("S999"), "2"))
    assert getattr(response, "content", response) == "Draft already accepted."
    assert store.record is original


def test_submit_rejects_then_accepts_fixed_draft() -> None:
    tool, store = _tool()
    response = tool.invoke(_call(_draft("S999"), "1"))
    response = getattr(response, "content", response)
    assert "unknown source ID" in response
    assert "Valid source IDs: S1" in response
    assert "call submit_draft again" in response
    assert store.record is None
    assert "Draft accepted" in str(tool.invoke(_call(_draft("S1"), "2")))
    assert store.record is not None
    assert store.failed_claims == 1
    assert store.dropped_claims == 0


def test_submit_rejects_twice_then_drops_bad_claims() -> None:
    tool, store = _tool()
    tool.invoke(_call(_draft("S999"), "1"))
    response = tool.invoke(_call(_draft("S998"), "2"))
    assert "Draft accepted with 1 claims removed" in str(getattr(response, "content", response))
    assert store.record is not None
    assert store.record.summary is None
    assert store.dropped_claims == 1
    assert any("Dropped summary claim" in flag for flag in store.record.flags)
