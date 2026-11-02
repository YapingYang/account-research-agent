"""Run research and assemble a citation-checked AccountRecord."""

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain_core.messages import HumanMessage, ToolMessage

from account_research.agent.builder import build_agent, seed_skill_files
from account_research.agent.submission import DraftSubmission, make_submit_draft_tool
from account_research.checks.citations import CitationReport
from account_research.config import Settings
from account_research.mcp.client import McpStatus, load_mcp_tools
from account_research.models.ratelimit import ModelUnavailableError, RateLimitState
from account_research.observability.tokens import TokenReport, TokenUsageHandler
from account_research.schemas.account import AccountRecord
from account_research.sources.registry import SourceRegistry


@dataclass
class ResearchRun:
    """Completed research record and diagnostics."""

    record: AccountRecord
    token_report: TokenReport
    elapsed_seconds: float
    skills_read: list[str]
    mcp_status: McpStatus
    rate_limit_waits: int
    rate_limit_wait_seconds: float
    citations: CitationReport
    claims_failed: int
    claims_dropped: int


class ResearchRunError(RuntimeError):
    """Failed research attempt with usage captured up to the failure."""

    def __init__(
        self,
        message: str,
        token_report: TokenReport,
        elapsed_seconds: float,
        *,
        rate_limit_waits: int = 0,
        rate_limit_wait_seconds: float = 0.0,
    ) -> None:
        super().__init__(message)
        self.token_report = token_report
        self.elapsed_seconds = elapsed_seconds
        self.rate_limit_waits = rate_limit_waits
        self.rate_limit_wait_seconds = rate_limit_wait_seconds


def _skills_read(state: dict[str, Any]) -> list[str]:
    read: set[str] = set()
    for message in state.get("messages", []):
        for call in getattr(message, "tool_calls", []):
            path = str(call.get("args", {}).get("file_path", ""))
            for skill in ("account-brief", "sec-filings"):
                if f"{skill}/SKILL.md" in path:
                    read.add(skill)
        if isinstance(message, ToolMessage) and message.name == "read_file":
            text = str(message.content)
            for skill in ("account-brief", "sec-filings"):
                if f"{skill}/SKILL.md" in text:
                    read.add(skill)
    return sorted(read)


def _copy_usage(total: TokenReport, current: TokenReport) -> None:
    total.model_calls += current.model_calls
    total.input_tokens += current.input_tokens
    total.output_tokens += current.output_tokens
    total.largest_request_input_tokens = max(
        total.largest_request_input_tokens, current.largest_request_input_tokens
    )
    total.rate_limit_errors += current.rate_limit_errors


def run_research(
    settings: Settings,
    company: str,
    *,
    domain: str | None = None,
    ticker: str | None = None,
    context: str | None = None,
) -> ResearchRun:
    """Run one tool-using agent and require a stored, citation-checked draft."""
    started = time.perf_counter()
    usage_total = TokenReport()
    rate_state = RateLimitState()
    models = [settings.model]
    if settings.fallback_model and settings.fallback_model != settings.model:
        models.append(settings.fallback_model)

    for index, model_name in enumerate(models):
        registry = SourceRegistry()
        mcp_tools, mcp_status = asyncio.run(load_mcp_tools(settings, registry))
        flags = []
        if not mcp_status.available and ticker:
            flags.append("SEC filings unavailable")
        store = DraftSubmission()
        submission_tool = make_submit_draft_tool(
            registry, store, model=model_name, initial_flags=flags
        )
        agent = build_agent(
            settings,
            registry,
            mcp_tools,
            model_name=model_name,
            rate_limit_state=rate_state,
            extra_tools=[submission_tool],
        )
        usage = TokenUsageHandler()
        invoke_config = {
            "recursion_limit": settings.recursion_limit,
            "callbacks": [usage],
            "configurable": {"thread_id": str(uuid4())},
        }
        request = (
            f"Research {company}."
            + (f" Domain: {domain}." if domain else "")
            + (f" Ticker: {ticker}." if ticker else "")
            + (f" Seller context: {context}." if context else "")
        )
        try:
            stream = agent.stream(
                {"messages": [HumanMessage(content=request)], "files": seed_skill_files(settings)},
                config=invoke_config,
                stream_mode="values",
            )
            state: dict[str, Any] = {}
            try:
                for update in stream:
                    state = update
                    if store.record is not None:
                        break
            finally:
                close = getattr(stream, "close", None)
                if close is not None:
                    close()
            _copy_usage(usage_total, usage.report)
            if store.record is None:
                raise ValueError(
                    "Agent finished without calling submit_draft; no AccountRecord was stored."
                )
            if store.report is None:
                raise RuntimeError("submit_draft stored a record without a citation report")
            return ResearchRun(
                record=store.record,
                token_report=usage_total,
                elapsed_seconds=time.perf_counter() - started,
                skills_read=_skills_read(state),
                mcp_status=mcp_status,
                rate_limit_waits=rate_state.wait_count,
                rate_limit_wait_seconds=rate_state.wait_seconds,
                citations=store.report,
                claims_failed=store.failed_claims,
                claims_dropped=store.dropped_claims,
            )
        except ModelUnavailableError as exc:
            _copy_usage(usage_total, usage.report)
            if index != 0 or len(models) < 2:
                raise ResearchRunError(
                    str(exc),
                    usage_total,
                    time.perf_counter() - started,
                    rate_limit_waits=rate_state.wait_count,
                    rate_limit_wait_seconds=rate_state.wait_seconds,
                ) from exc
            output_tpm = rate_state.output_tpm
            if output_tpm is None or output_tpm < settings.min_output_tpm:
                observed = "unavailable" if output_tpm is None else str(output_tpm)
                raise ResearchRunError(
                    f"Fallback {models[1]} skipped: output TPM from rate-limit headers is "
                    f"{observed}, below min_output_tpm={settings.min_output_tpm}.",
                    usage_total,
                    time.perf_counter() - started,
                    rate_limit_waits=rate_state.wait_count,
                    rate_limit_wait_seconds=rate_state.wait_seconds,
                ) from exc
            continue
        except Exception as exc:
            _copy_usage(usage_total, usage.report)
            raise ResearchRunError(
                str(exc),
                usage_total,
                time.perf_counter() - started,
                rate_limit_waits=rate_state.wait_count,
                rate_limit_wait_seconds=rate_state.wait_seconds,
            ) from exc
    raise ResearchRunError("Research did not run", usage_total, time.perf_counter() - started)


def save_record(record: AccountRecord, path: str) -> None:
    """Write the final code-assembled AccountRecord as JSON."""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8")
