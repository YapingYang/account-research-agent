"""Offline model-call and request-size budget tests."""

from datetime import UTC, datetime
from typing import Any

import pytest
from langchain.agents.middleware import ModelRequest, ModelResponse
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr

from account_research.agent.submission import DraftSubmission
from account_research.models.budget import (
    ModelCallBudgetExceeded,
    ModelCallBudgetMiddleware,
    RequestSizeMiddleware,
)
from account_research.observability.tokens import TokenReport
from account_research.schemas.account import AccountRecord


class _Model(BaseChatModel):
    _unused: list[Any] = PrivateAttr(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "budget-test"

    def _generate(
        self, messages: list[Any], stop: list[str] | None = None, **kwargs: Any
    ) -> ChatResult:
        del messages, stop, kwargs
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])


def test_request_trimming_preserves_old_source_ids_and_titles_and_leaves_newest_untouched() -> None:
    old = ToolMessage(
        content=(
            '<untrusted_content source_id="S1" title="Old filing" url="https://example.com/old">'
            + "A" * 1500
            + "</untrusted_content>"
        ),
        tool_call_id="old",
    )
    newest = ToolMessage(
        content='<untrusted_content source_id="S2" title="Latest" url="https://example.com/new">'
        + "LATEST RESULT"
        + "</untrusted_content>",
        tool_call_id="new",
    )
    old_skill = ToolMessage(content="B" * 1500, tool_call_id="skill", name="read_file")
    request = ModelRequest(
        model=_Model(),
        system_prompt="brief",
        messages=[
            HumanMessage(content="Research"),
            old,
            old_skill,
            HumanMessage(content="Next"),
            newest,
        ],
    )
    report = TokenReport()
    middleware = RequestSizeMiddleware(max_request_tokens=600, report=report)
    seen: list[ModelRequest[Any]] = []

    response = middleware.wrap_model_call(
        request,
        lambda value: seen.append(value) or ModelResponse(result=[AIMessage(content="ok")]),
    )

    assert response.result[0].content == "ok"
    sent_messages = seen[0].messages
    old_sent = sent_messages[1].content
    skill_sent = sent_messages[2].content
    newest_sent = sent_messages[4].content
    assert 'source_id="S1"' in old_sent
    assert 'title="Old filing"' in old_sent
    assert "A" * 100 in old_sent
    assert len(old_sent) < len(old.content)
    assert len(skill_sent) < len(old_skill.content)
    assert newest_sent == newest.content
    assert report.largest_request_estimate_tokens <= 600


def test_request_trimming_fails_if_non_source_request_alone_exceeds_cap() -> None:
    request = ModelRequest(
        model=_Model(), system_prompt="x" * 2000, messages=[HumanMessage(content="small")]
    )
    middleware = RequestSizeMiddleware(max_request_tokens=10, report=TokenReport())
    with pytest.raises(ValueError, match="exceeds max_request_tokens=10"):
        middleware.wrap_model_call(
            request, lambda _request: ModelResponse(result=[AIMessage(content="never")])
        )


def test_model_call_budget_sends_one_final_instruction_and_requires_submit_draft() -> None:
    report = TokenReport(model_calls=2)
    store = DraftSubmission()
    middleware = ModelCallBudgetMiddleware(2, report, store)
    received: list[ModelRequest[Any]] = []

    response = middleware.wrap_model_call(
        ModelRequest(model=_Model(), messages=[HumanMessage(content="Research Acme")]),
        lambda request: (
            received.append(request)
            or ModelResponse(
                result=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "submit_draft",
                                "args": {},
                                "id": "submit",
                                "type": "tool_call",
                            }
                        ],
                    )
                ]
            )
        ),
    )
    assert response.result[0].tool_calls[0]["name"] == "submit_draft"
    assert "call submit_draft" in received[0].messages[-1].content
    assert middleware.final_instruction_sent


def test_model_call_budget_fails_when_final_instruction_does_not_submit() -> None:
    middleware = ModelCallBudgetMiddleware(1, TokenReport(model_calls=1), DraftSubmission())
    with pytest.raises(
        ModelCallBudgetExceeded, match="final instruction did not call submit_draft"
    ):
        middleware.wrap_model_call(
            ModelRequest(model=_Model(), messages=[HumanMessage(content="Research Acme")]),
            lambda _request: ModelResponse(result=[AIMessage(content="Here is the answer")]),
        )


def test_model_call_budget_allows_submission_and_approval_followups() -> None:
    store = DraftSubmission(
        record=AccountRecord(company_name="Acme", model="test", generated_at=datetime.now(UTC))
    )
    middleware = ModelCallBudgetMiddleware(1, TokenReport(model_calls=5), store)
    response = middleware.wrap_model_call(
        ModelRequest(model=_Model(), messages=[HumanMessage(content="Export")]),
        lambda _request: ModelResponse(result=[AIMessage(content="call export")]),
    )
    assert response.result[0].content == "call export"
    assert not middleware.final_instruction_sent
