"""Offline agent run test with scripted tool calls and citation repair."""

from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
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
        _env_file=None,
    )
    result = run_research(settings, "Acme")

    assert result.record.summary is not None
    assert result.record.summary.source_ids == ["S1"]
    assert result.record.signals == []
    assert not result.record.flags
    assert result.citations.passed_count == 1
    assert result.claims_failed == 1
    assert result.claims_dropped == 0
    assert result.skills_read == ["account-brief"]
    assert result.token_report.model_calls == 5
    assert result.token_report.input_tokens == 1000
    assert result.token_report.output_tokens == 100
    assert result.token_report.largest_request_input_tokens == 200
    assert result.token_report.as_dict()["total_tokens"] == 1100


def test_never_submitting_draft_is_a_clear_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeAgent:
        def stream(self, *_args: Any, **_kwargs: Any) -> Any:
            yield {"messages": []}

    monkeypatch.setattr("account_research.agent.run.build_agent", lambda *_a, **_k: FakeAgent())
    settings = Settings(model="groq:test", fallback_model=None, mcp_enabled=False, _env_file=None)
    with pytest.raises(ResearchRunError, match="without calling submit_draft"):
        run_research(settings, "Acme")


def test_rate_limit_never_uses_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    built_models: list[str] = []

    class FakeAgent:
        def stream(self, *_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("429 rate limit")
            yield {}

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
    monkeypatch: pytest.MonkeyPatch, fallback_limit: int, should_run: bool
) -> None:
    built: list[str] = []

    class FakeAgent:
        def __init__(self, tool: Any, unavailable: bool = False) -> None:
            self.tool = tool
            self.unavailable = unavailable

        def stream(self, *_args: Any, **_kwargs: Any) -> Any:
            if self.unavailable:
                raise ModelUnavailableError("model not found")
            self.tool.invoke(
                {"name": "submit_draft", "args": _draft("S1"), "id": "1", "type": "tool_call"}
            )
            yield {"messages": []}

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
            return FakeAgent(extra_tools[0], unavailable=True)
        registry.record("https://example.com", "source", "search_result")
        return FakeAgent(extra_tools[0])

    monkeypatch.setattr("account_research.agent.run.build_agent", fake_build)
    settings = Settings(
        model="groq:primary",
        fallback_model="groq:fallback",
        min_output_tpm=3000,
        mcp_enabled=False,
        _env_file=None,
    )
    if should_run:
        result = run_research(settings, "Acme")
        assert result.record.model == "groq:fallback"
    else:
        with pytest.raises(ResearchRunError, match="below min_output_tpm"):
            run_research(settings, "Acme")
    assert built == (["groq:primary", "groq:fallback"] if should_run else ["groq:primary"])
    if should_run:
        assert result.record.summary is not None
