"""Per-run model-call and request-size budgets."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool

from account_research.agent.submission import DraftSubmission
from account_research.observability.tokens import TokenReport

FINAL_DRAFT_INSTRUCTION = (
    "The model-call budget is reached. Do no more research. Draft immediately using only the "
    "sources gathered so far and call submit_draft. Thin sections are acceptable; "
    "leave unsupported "
    "sections empty."
)
_SOURCE_TAG = re.compile(
    r"(?P<open><untrusted_content\b[^>]*>)(?P<body>.*?)(?P<close></untrusted_content>)",
    re.DOTALL,
)


class ModelCallBudgetExceeded(RuntimeError):
    """The final model instruction did not submit a draft."""


def estimate_request_tokens(request: ModelRequest[Any]) -> int:
    """Estimate serialized model request size at four characters per token."""
    message_data = []
    for message in request.messages:
        content = message.content
        message_data.append(
            {
                "role": getattr(message, "type", "message"),
                "name": getattr(message, "name", None),
                "content": content,
                "tool_calls": getattr(message, "tool_calls", None),
                "tool_call_id": getattr(message, "tool_call_id", None),
                "additional_kwargs": getattr(message, "additional_kwargs", {}),
            }
        )
    tool_data = []
    for item in request.tools:
        if isinstance(item, BaseTool):
            schema: Any = item.args_schema
            if schema is not None and hasattr(schema, "model_json_schema"):
                schema = schema.model_json_schema()
            tool_data.append(
                {"name": item.name, "description": item.description, "parameters": schema}
            )
        elif isinstance(item, dict):
            tool_data.append(item)
        else:
            tool_data.append(str(item))
    serialized = json.dumps(
        {
            "system": request.system_message.content if request.system_message else "",
            "messages": message_data,
            "tools": tool_data,
            "tool_choice": request.tool_choice,
        },
        ensure_ascii=False,
        default=str,
    )
    return (len(serialized) + 3) // 4


def _shorten_tool_content(content: Any, char_limit: int) -> Any:
    """Shorten source-bearing tool output while preserving source IDs and titles."""
    if not isinstance(content, str):
        return content
    tag = _SOURCE_TAG.search(content)
    if tag:
        body = tag.group("body")[:char_limit]
        suffix = "…[older source excerpt trimmed]" if len(tag.group("body")) > char_limit else ""
        return f"{tag.group('open')}{body}{suffix}{tag.group('close')}"
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        data = None
    entries = data if isinstance(data, list) else [data]
    if entries and all(
        isinstance(item, dict) and "id" in item and "title" in item for item in entries
    ):
        for item in entries:
            for key in ("snippet", "content", "text"):
                if isinstance(item.get(key), str) and len(item[key]) > char_limit:
                    item[key] = item[key][:char_limit] + "…[trimmed]"
                    break
        return json.dumps(data, ensure_ascii=False)
    if re.search(r"source_id|source_ids|\bS\d+\b", content, re.IGNORECASE):
        return content
    if len(content) > char_limit:
        return content[:char_limit] + "…[older tool result trimmed]"
    return content


class RequestSizeMiddleware(AgentMiddleware):
    """Trim older source results until the request estimate fits its configured cap."""

    def __init__(self, max_request_tokens: int, report: TokenReport) -> None:
        super().__init__()
        self.max_request_tokens = max_request_tokens
        self.report = report

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        messages = list(request.messages)
        estimate = estimate_request_tokens(request)
        if estimate > self.max_request_tokens:
            tool_indices = [
                index for index, message in enumerate(messages) if isinstance(message, ToolMessage)
            ]
            older_indices = tool_indices[:-1]
            for char_limit in (500, 250, 100, 0):
                for index in older_indices:
                    message = messages[index]
                    messages[index] = message.model_copy(
                        update={"content": _shorten_tool_content(message.content, char_limit)}
                    )
                request = request.override(messages=messages)
                estimate = estimate_request_tokens(request)
                if estimate <= self.max_request_tokens:
                    break
        if estimate > self.max_request_tokens:
            raise ValueError(
                f"Model request estimate {estimate} tokens exceeds "
                f"max_request_tokens={self.max_request_tokens} after trimming older tool results."
            )
        self.report.largest_request_estimate_tokens = max(
            self.report.largest_request_estimate_tokens, estimate
        )
        return handler(request)


class ModelCallBudgetMiddleware(AgentMiddleware):
    """Force one final submit instruction after the normal model-call budget."""

    def __init__(
        self,
        max_model_calls: int,
        report: TokenReport,
        submission: DraftSubmission,
    ) -> None:
        super().__init__()
        self.max_model_calls = max_model_calls
        self.report = report
        self.submission = submission
        self.final_instruction_sent = False
        self.final_repair_sent = False
        self.followup_calls = 0

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        if self.report.model_calls < self.max_model_calls:
            return handler(request)
        if self.submission.record is not None or self.submission.revisions > 0:
            if self.followup_calls >= 4:
                raise ModelCallBudgetExceeded(
                    "Reached the post-submission model-call allowance without completing export."
                )
            self.followup_calls += 1
            return handler(request)
        if (
            self.final_instruction_sent
            and self.submission.resubmissions > 0
            and not self.final_repair_sent
        ):
            self.final_repair_sent = True
            return handler(request)
        if self.final_instruction_sent:
            raise ModelCallBudgetExceeded(
                f"Reached max_model_calls={self.max_model_calls}; the final instruction "
                "did not produce a draft for submit_draft."
            )
        self.final_instruction_sent = True
        forced = request.override(
            messages=[*request.messages, HumanMessage(content=FINAL_DRAFT_INSTRUCTION)]
        )
        response = handler(forced)
        result = response.result if isinstance(response.result, list) else [response.result]
        submitted = any(
            isinstance(message, AIMessage)
            and any(call.get("name") == "submit_draft" for call in message.tool_calls)
            for message in result
        )
        if not submitted:
            raise ModelCallBudgetExceeded(
                f"Reached max_model_calls={self.max_model_calls}; the final instruction "
                "did not call submit_draft."
            )
        return response
