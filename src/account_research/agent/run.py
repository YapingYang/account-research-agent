"""Run research, collect approval, and export a citation-checked record."""

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.types import Command

from account_research.agent.builder import build_agent, seed_skill_files
from account_research.agent.submission import DraftSubmission, make_submit_draft_tool
from account_research.checks.citations import CitationReport
from account_research.config import Settings
from account_research.mcp.client import McpStatus, load_mcp_tools
from account_research.models.budget import FINAL_DRAFT_INSTRUCTION
from account_research.models.ratelimit import ModelUnavailableError, RateLimitState
from account_research.observability.tokens import TokenReport, TokenUsageHandler
from account_research.schemas.account import AccountRecord
from account_research.sources.registry import SourceRegistry
from account_research.tools.export_account import ExportDiagnostics, make_export_account_tool

ApprovalHandler = Callable[[AccountRecord], tuple[bool, str]]


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
    rate_limit_headers: dict[str, str]
    citations: CitationReport
    claims_failed: int
    claims_dropped: int
    output_paths: list[str]


class ResearchRunError(RuntimeError):
    """Failed research attempt with usage and quota diagnostics."""

    def __init__(
        self,
        message: str,
        token_report: TokenReport,
        elapsed_seconds: float,
        *,
        rate_limit_waits: int = 0,
        rate_limit_wait_seconds: float = 0.0,
        rate_limit_headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.token_report = token_report
        self.elapsed_seconds = elapsed_seconds
        self.rate_limit_waits = rate_limit_waits
        self.rate_limit_wait_seconds = rate_limit_wait_seconds
        self.rate_limit_headers = dict(rate_limit_headers or {})


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
    total.largest_request_estimate_tokens = max(
        total.largest_request_estimate_tokens, current.largest_request_estimate_tokens
    )
    total.rate_limit_errors += current.rate_limit_errors


def _research_error(
    message: str,
    usage: TokenReport,
    started: float,
    rate_state: RateLimitState,
) -> ResearchRunError:
    return ResearchRunError(
        message,
        usage,
        time.perf_counter() - started,
        rate_limit_waits=rate_state.wait_count,
        rate_limit_wait_seconds=rate_state.wait_seconds,
        rate_limit_headers=rate_state.latest_headers,
    )


def run_research(
    settings: Settings,
    company: str,
    *,
    domain: str | None = None,
    ticker: str | None = None,
    context: str | None = None,
    approval_handler: ApprovalHandler | None = None,
    auto_approve: bool = False,
) -> ResearchRun:
    """Research, pause before export, and resume after the human decision."""
    started = time.perf_counter()
    usage_total = TokenReport()
    rate_state = RateLimitState()
    models = [settings.model]
    if settings.fallback_model and settings.fallback_model != settings.model:
        models.append(settings.fallback_model)

    for model_index, model_name in enumerate(models):
        registry = SourceRegistry()
        mcp_tools, mcp_status = asyncio.run(load_mcp_tools(settings, registry))
        flags = ["SEC filings unavailable"] if not mcp_status.available and ticker else []
        store = DraftSubmission()
        usage = TokenUsageHandler()
        diagnostics = ExportDiagnostics(
            model=model_name,
            token_report=usage.report,
            started_at=started,
        )
        submission_tool = make_submit_draft_tool(
            registry, store, model=model_name, initial_flags=flags
        )
        export_tool = make_export_account_tool(store, settings.output_dir, diagnostics)
        agent = build_agent(
            settings,
            registry,
            mcp_tools,
            model_name=model_name,
            rate_limit_state=rate_state,
            extra_tools=[submission_tool, export_tool],
            submission=store,
            usage_report=usage.report,
        )
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
        payload: Any = {
            "messages": [HumanMessage(content=request)],
            "files": seed_skill_files(settings),
        }
        state: dict[str, Any] = {}
        final_instruction_sent = False
        try:
            while True:
                state = agent.invoke(payload, config=invoke_config)
                diagnostics.rate_limit_waits = rate_state.wait_count
                diagnostics.rate_limit_wait_seconds = rate_state.wait_seconds
                diagnostics.rate_limit_headers = rate_state.latest_headers
                diagnostics.skills_read = _skills_read(state)
                diagnostics.claims_failed = store.failed_claims
                diagnostics.claims_dropped = store.dropped_claims
                if store.export_paths:
                    break
                interrupts = state.get("__interrupt__", [])
                if not interrupts:
                    if not final_instruction_sent:
                        final_instruction_sent = True
                        instruction = (
                            FINAL_DRAFT_INSTRUCTION
                            if store.record is None
                            else "The validated draft is ready. Call export_account now."
                        )
                        payload = {"messages": [HumanMessage(content=instruction)]}
                        continue
                    raise ValueError(
                        "Agent ended without a submitted draft and approved export_account call; "
                        "no files were written."
                    )
                if store.record is None:
                    raise ValueError(
                        "Agent paused before a validated draft was pending; no files were written."
                    )
                if approval_handler is None and not auto_approve:
                    raise ValueError("Export approval is required; no files were written.")
                if auto_approve:
                    approve, feedback = True, ""
                else:
                    approve, feedback = approval_handler(store.record)  # type: ignore[misc]
                if approve:
                    payload = Command(resume={"decisions": [{"type": "approve"}]})
                else:
                    if not feedback.strip():
                        feedback = "Please revise the brief using the reviewer feedback."
                    if not store.begin_revision():
                        raise ValueError("Export rejected twice; run ended without writing files.")
                    payload = Command(
                        resume={"decisions": [{"type": "reject", "message": feedback.strip()}]}
                    )
            _copy_usage(usage_total, usage.report)
            if store.record is None or store.report is None:
                raise RuntimeError("Export completed without a stored citation-checked record.")
            diagnostics.token_report = usage_total
            elapsed = time.perf_counter() - started
            return ResearchRun(
                record=store.record,
                token_report=usage_total,
                elapsed_seconds=elapsed,
                skills_read=diagnostics.skills_read,
                mcp_status=mcp_status,
                rate_limit_waits=rate_state.wait_count,
                rate_limit_wait_seconds=rate_state.wait_seconds,
                rate_limit_headers=rate_state.latest_headers,
                citations=store.report,
                claims_failed=store.failed_claims,
                claims_dropped=store.dropped_claims,
                output_paths=[str(path) for path in store.export_paths],
            )
        except ModelUnavailableError as exc:
            _copy_usage(usage_total, usage.report)
            if model_index == 0 and len(models) > 1:
                output_tpm = rate_state.output_tpm
                if output_tpm is not None and output_tpm >= settings.min_output_tpm:
                    continue
                observed = "unavailable" if output_tpm is None else str(output_tpm)
                raise _research_error(
                    f"Fallback {models[1]} skipped: output TPM from rate-limit headers is "
                    f"{observed}, below min_output_tpm={settings.min_output_tpm}.",
                    usage_total,
                    started,
                    rate_state,
                ) from exc
            raise _research_error(str(exc), usage_total, started, rate_state) from exc
        except Exception as exc:
            _copy_usage(usage_total, usage.report)
            raise _research_error(str(exc), usage_total, started, rate_state) from exc
    raise _research_error("Research did not run", usage_total, started, rate_state)
