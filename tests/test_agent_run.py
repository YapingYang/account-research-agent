"""Offline agent run test with scripted tool calls and citation repair."""

from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.types import Command
from pydantic import PrivateAttr

from account_research.agent.run import ResearchRunError, run_research
from account_research.config import Settings
from account_research.models.ratelimit import ModelUnavailableError
from account_research.tools import fetch_page as fetch_module
from account_research.tools import web_search as search_module


def _call(name: str, args: dict[str, Any], call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
        usage_metadata={"input_tokens": 200, "output_tokens": 20, "total_tokens": 220},
    )


def _draft(summary_id: str, signal_id: str | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "company_name": "Acme",
        "summary": {"text": "Acme builds workflow software", "source_ids": [summary_id]},
    }
    if signal_id:
        data["signals"] = [{"text": "Acme launched a feature", "source_ids": [signal_id]}]
    return data


class ScriptedToolChatModel(BaseChatModel):
    """Minimal fake model that returns scripted tool calls in order."""

    _responses: list[AIMessage] = PrivateAttr()

    def __init__(self, responses: list[AIMessage]) -> None:
        super().__init__()
        self._responses = list(responses)

    @property
    def _llm_type(self) -> str:
        return "scripted-tool-chat-model"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedToolChatModel":
        del tools, kwargs
        return self

    def _generate(
        self,
        messages: list[Any],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        del messages, stop, run_manager, kwargs
        if not self._responses:
            raise AssertionError("scripted fake model ran out of responses")
        return ChatResult(generations=[ChatGeneration(message=self._responses.pop(0))])


def test_fake_run_repairs_then_drops_bad_citation_and_reports_tokens(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeTavily:
        def __init__(self, **kwargs: Any) -> None:
            del kwargs

        def search(self, query: str, **kwargs: Any) -> dict[str, Any]:
            del query, kwargs
            return {
                "results": [
                    {"url": "https://example.com", "title": "Acme", "content": "Acme facts"}
                ]
            }

        def extract(self, **kwargs: Any) -> dict[str, Any]:
            del kwargs
            return {"results": [{"title": "Acme page", "raw_content": "Acme page text"}]}

    monkeypatch.setattr(search_module, "TavilyClient", FakeTavily)
    monkeypatch.setattr(fetch_module, "TavilyClient", FakeTavily)
    monkeypatch.setattr(
        fetch_module.socket,
        "getaddrinfo",
        lambda *_args: [(2, 1, 6, "", ("93.184.216.34", 0))],
    )

    fake_model = ScriptedToolChatModel(
        [
            _call("web_search", {"query": "Acme company official"}, "1"),
            _call("fetch_page", {"url": "https://example.com"}, "2"),
            _call("read_file", {"file_path": "/skills/account-brief/SKILL.md"}, "3"),
            _call("submit_draft", _draft("S1", "S999"), "4"),
            _call("submit_draft", _draft("S1"), "5"),
            _call("export_account", {}, "6"),
        ]
    )
    monkeypatch.setattr(
        "account_research.agent.builder.get_chat_model", lambda *_args, **_kwargs: fake_model
    )
    settings = Settings(
        model="groq:test",
        fallback_model=None,
        tavily_api_key="test",
        mcp_enabled=False,
        output_dir=tmp_path,
        _env_file=None,
    )

    def approve(record: Any) -> tuple[bool, str]:
        assert record.company_name == "Acme"
        assert not (tmp_path / "acme" / "brief.md").exists()
        return True, ""

    result = run_research(settings, "Acme", approval_handler=approve)

    assert result.record.summary is not None
    assert result.record.summary.source_ids == ["S1"]
    assert result.record.signals == []
    assert not result.record.flags
    assert result.citations.passed_count == 1
    assert result.claims_failed == 1
    assert result.claims_dropped == 0
    assert result.skills_read == ["account-brief"]
    assert result.token_report.model_calls == 6
    assert result.token_report.input_tokens == 1200
    assert result.token_report.output_tokens == 120
    assert result.token_report.largest_request_input_tokens == 200
    assert result.token_report.as_dict()["total_tokens"] == 1320
    assert (tmp_path / "acme" / "brief.md").exists()
    assert (tmp_path / "acme" / "account.json").exists()


def test_never_submitting_draft_is_a_clear_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeAgent:
        def invoke(self, *_args: Any, **_kwargs: Any) -> Any:
            return {"messages": []}

    monkeypatch.setattr("account_research.agent.run.build_agent", lambda *_a, **_k: FakeAgent())
    settings = Settings(model="groq:test", fallback_model=None, mcp_enabled=False, _env_file=None)
    with pytest.raises(ResearchRunError, match="without an approved export_account"):
        run_research(settings, "Acme")


def test_rate_limit_never_uses_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    built_models: list[str] = []

    class FakeAgent:
        def invoke(self, *_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("429 rate limit")

    def fake_build(
        _settings: Settings,
        registry: Any,
        _tools: list[Any],
        *,
        model_name: str,
        **_kwargs: Any,
    ) -> FakeAgent:
        built_models.append(model_name)
        return FakeAgent()

    monkeypatch.setattr("account_research.agent.run.build_agent", fake_build)
    settings = Settings(
        model="groq:primary",
        fallback_model="groq:fallback",
        mcp_enabled=False,
        _env_file=None,
    )
    with pytest.raises(ResearchRunError, match="429"):
        run_research(settings, "Acme")
    assert built_models == ["groq:primary"]


@pytest.mark.parametrize("fallback_limit,should_run", [(4000, True), (1000, False)])
def test_model_not_found_uses_only_eligible_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    fallback_limit: int,
    should_run: bool,
) -> None:
    built: list[str] = []

    class FakeAgent:
        def __init__(self, submit_tool: Any, export_tool: Any, unavailable: bool = False) -> None:
            self.submit_tool = submit_tool
            self.export_tool = export_tool
            self.unavailable = unavailable

        def invoke(self, payload: Any, **_kwargs: Any) -> Any:
            if self.unavailable:
                raise ModelUnavailableError("model not found")
            if isinstance(payload, Command):
                self.export_tool.invoke({})
                return {"messages": []}
            self.submit_tool.invoke(_draft("S1"))
            return {"__interrupt__": [object()], "messages": []}

    def fake_build(
        _settings: Settings,
        registry: Any,
        _tools: list[Any],
        *,
        model_name: str,
        rate_limit_state: Any,
        extra_tools: list[Any],
    ) -> FakeAgent:
        built.append(model_name)
        if model_name == "groq:primary":
            rate_limit_state.capture_headers(
                {"x-ratelimit-limit-output-tokens": str(fallback_limit)}
            )
            return FakeAgent(extra_tools[0], extra_tools[1], unavailable=True)
        registry.record("https://example.com", "source", "search_result")
        return FakeAgent(extra_tools[0], extra_tools[1])

    monkeypatch.setattr("account_research.agent.run.build_agent", fake_build)
    settings = Settings(
        model="groq:primary",
        fallback_model="groq:fallback",
        min_output_tpm=3000,
        output_dir=tmp_path,
        mcp_enabled=False,
        _env_file=None,
    )
    if should_run:
        result = run_research(settings, "Acme", approval_handler=lambda _r: (True, ""))
        assert result.record.model == "groq:fallback"
    else:
        with pytest.raises(ResearchRunError, match="below min_output_tpm"):
            run_research(settings, "Acme")
    assert built == (["groq:primary", "groq:fallback"] if should_run else ["groq:primary"])
    if should_run:
        assert result.record.summary is not None
