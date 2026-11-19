"""Run research, collect approval, and export a citation-checked record."""

import asyncio
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from account_research.agent.builder import build_agent, seed_skill_files
from account_research.agent.submission import DraftSubmission, make_submit_draft_tool
from account_research.checks.citations import CitationReport
from account_research.config import Settings
from account_research.mcp.client import McpStatus, load_mcp_tools, prefetch_sec_tools
from account_research.models.budget import FINAL_DRAFT_INSTRUCTION
from account_research.models.ratelimit import ModelUnavailableError, RateLimitState
from account_research.observability.tokens import TokenReport, TokenUsageHandler
from account_research.observability.tracing import configure_tracing
from account_research.schemas.account import AccountRecord, Source
from account_research.sources.registry import SourceRegistry
from account_research.tools.export_account import ExportDiagnostics, make_export_account_tool
from account_research.tools.factory import make_research_tools

ApprovalHandler = Callable[[AccountRecord], tuple[bool, str]]
logger = logging.getLogger(__name__)


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
    trace_url: str | None = None
    all_sources: list[Source] = field(default_factory=list)
    activity: list[dict[str, Any]] = field(default_factory=list)


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


_SOURCE_ID = re.compile(r'source_id="(S\d+)"|"id":\s*"(S\d+)"')
_ACTIVITY_TOOLS = {"web_search", "fetch_page", "submit_draft", "export_account"}


def _source_ids_in(text: str) -> list[str]:
    return list(dict.fromkeys(a or b for a, b in _SOURCE_ID.findall(text)))


def _describe_args(name: str, args: dict[str, Any]) -> str:
    if name == "web_search":
        return str(args.get("query", ""))
    if name == "fetch_page":
        return str(args.get("url", ""))
    if name == "submit_draft":
        return "Draft brief submitted for citation checks"
    if name == "export_account":
        return "Export approved brief"
    return ", ".join(f"{key}={value}" for key, value in args.items())


def _describe_result(name: str, text: str, ids: list[str]) -> str:
    if text.startswith(("Budget reached", "SEC call budget", "Page fetch budget")):
        return "Budget reached"
    if "does not read sec.gov" in text:
        return "Skipped (sec.gov URL)"
    if name in {"submit_draft", "export_account"}:
        return text.split("\n", 1)[0][:160]
    return f"{len(ids)} source(s)" if ids else "No sources"


def _tool_activity(state: dict[str, Any], sec_tool_names: set[str]) -> list[dict[str, Any]]:
    """Summarize the agent's research tool calls in order, with the source IDs each returned."""
    results = {
        message.tool_call_id: str(message.content)
        for message in state.get("messages", [])
        if isinstance(message, ToolMessage)
    }
    activity: list[dict[str, Any]] = []
    for message in state.get("messages", []):
        if not isinstance(message, AIMessage):
            continue
        for call in message.tool_calls:
            name = call.get("name", "")
            if name not in _ACTIVITY_TOOLS and name not in sec_tool_names:
                continue
            text = results.get(call.get("id") or "", "")
            ids = _source_ids_in(text)
            activity.append(
                {
                    "tool": name,
                    "input": _describe_args(name, call.get("args", {})),
                    "result": _describe_result(name, text, ids),
                    "source_ids": ids,
                    "phase": "agent",
                }
            )
    return activity


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


def _emit_progress(
    progress_callback: Callable[[dict[str, Any]], None] | None,
    event: dict[str, Any],
) -> None:
    """Emit a UI-friendly progress event when a callback is supplied."""
    if progress_callback is not None:
        progress_callback(event)


def _trace_run_parent(func: Any) -> Any:
    try:
        from langsmith import traceable

        return traceable(name="account_research_run", tags=["account-research-agent"])(func)
    except Exception:
        return func


def run_research(
    settings: Settings,
    company: str,
    *,
    domain: str | None = None,
    ticker: str | None = None,
    context: str | None = None,
    approval_handler: ApprovalHandler | None = None,
    auto_approve: bool = False,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> ResearchRun:
    """Research, pause before export, and resume after the human decision."""
    outcome = _start(
        settings,
        company,
        domain=domain,
        ticker=ticker,
        context=context,
        approval_handler=approval_handler,
        auto_approve=auto_approve,
        progress_callback=progress_callback,
        pause_for_approval=False,
    )
    assert isinstance(outcome, ResearchRun)
    return outcome


def start_research(
    settings: Settings,
    company: str,
    *,
    domain: str | None = None,
    ticker: str | None = None,
    context: str | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> "PendingApproval":
    """Research until a validated draft is ready, then pause for a human decision.

    Nothing is exported until the caller resolves the returned PendingApproval with
    approve(); reject(feedback) sends the feedback to the agent for one revision.
    """
    outcome = _start(
        settings,
        company,
        domain=domain,
        ticker=ticker,
        context=context,
        progress_callback=progress_callback,
        pause_for_approval=True,
    )
    if not isinstance(outcome, PendingApproval):
        raise RuntimeError("Research finished without pausing for approval.")
    return outcome


def _start(
    settings: Settings,
    company: str,
    *,
    domain: str | None,
    ticker: str | None,
    context: str | None,
    progress_callback: Callable[[dict[str, Any]], None] | None,
    pause_for_approval: bool,
    approval_handler: ApprovalHandler | None = None,
    auto_approve: bool = False,
) -> "ResearchRun | PendingApproval":
    # Tracing must be configured before entering the @traceable function: LangSmith decides
    # whether to trace (and caches the env lookups) as soon as the traced call starts.
    trace_status = configure_tracing(settings)
    trace_url = trace_status.get("url") if trace_status.get("enabled") else None
    outcome: ResearchRun | PendingApproval = _run_research_traced(
        settings,
        company,
        trace_status,
        domain=domain,
        ticker=ticker,
        context=context,
        approval_handler=approval_handler,
        auto_approve=auto_approve,
        progress_callback=progress_callback,
        pause_for_approval=pause_for_approval,
    )
    outcome.trace_url = trace_url
    return outcome


@dataclass
class _RunContext:
    """Everything needed to continue one model attempt across a human approval pause."""

    settings: Settings
    model_name: str
    agent: Any
    invoke_config: dict[str, Any]
    store: DraftSubmission
    registry: SourceRegistry
    mcp_tools: list[Any]
    mcp_status: McpStatus
    usage: TokenUsageHandler
    usage_total: TokenReport
    rate_state: RateLimitState
    diagnostics: ExportDiagnostics
    started: float
    prefetch_activity: list[dict[str, Any]]
    progress_callback: Callable[[dict[str, Any]], None] | None
    state: dict[str, Any] = field(default_factory=dict)
    final_instruction_sent: bool = False

    def activity(self) -> list[dict[str, Any]]:
        return self.prefetch_activity + _tool_activity(
            self.state, {tool.name for tool in self.mcp_tools}
        )

    def token_report(self) -> TokenReport:
        report = TokenReport()
        _copy_usage(report, self.usage_total)
        _copy_usage(report, self.usage.report)
        return report


class PendingApproval:
    """A citation-checked draft paused before export, waiting for a human decision."""

    def __init__(self, ctx: _RunContext) -> None:
        self._ctx = ctx
        self.elapsed_seconds = time.perf_counter() - ctx.started
        self.trace_url: str | None = None

    @property
    def record(self) -> AccountRecord:
        assert self._ctx.store.record is not None
        return self._ctx.store.record

    @property
    def citations(self) -> CitationReport:
        assert self._ctx.store.report is not None
        return self._ctx.store.report

    @property
    def all_sources(self) -> list[Source]:
        return self._ctx.registry.all()

    @property
    def activity(self) -> list[dict[str, Any]]:
        return self._ctx.activity()

    @property
    def token_report(self) -> TokenReport:
        return self._ctx.token_report()

    @property
    def output_paths(self) -> list[str]:
        return []

    @property
    def can_revise(self) -> bool:
        """Whether a rejection can still go back to the agent for a revision."""
        return self._ctx.store.revisions < 1

    def approve(self) -> ResearchRun:
        """Release the export: the agent writes the brief, record, and run report."""
        outcome = self._continue(Command(resume={"decisions": [{"type": "approve"}]}))
        assert isinstance(outcome, ResearchRun)
        outcome.trace_url = self.trace_url
        return outcome

    def reject(self, feedback: str) -> "PendingApproval":
        """Send reviewer feedback to the agent and pause again on its revised draft."""
        if not self._ctx.store.begin_revision():
            raise ValueError("This brief was already revised once; approve or discard it.")
        message = feedback.strip() or "Please revise the brief using the reviewer feedback."
        outcome = self._continue(
            Command(resume={"decisions": [{"type": "reject", "message": message}]})
        )
        if not isinstance(outcome, PendingApproval):
            raise RuntimeError("Revision finished without a new draft to review.")
        outcome.trace_url = self.trace_url
        return outcome

    def _continue(self, payload: Any) -> "ResearchRun | PendingApproval":
        ctx = self._ctx
        try:
            return _drive(ctx, payload, pause_for_approval=True)
        except Exception as exc:
            _copy_usage(ctx.usage_total, ctx.usage.report)
            raise _research_error(str(exc), ctx.usage_total, ctx.started, ctx.rate_state) from exc


def _drive(
    ctx: _RunContext,
    payload: Any,
    *,
    pause_for_approval: bool,
    approval_handler: ApprovalHandler | None = None,
    auto_approve: bool = False,
) -> "ResearchRun | PendingApproval":
    """Invoke the agent until export completes or, when pausing, a draft awaits approval."""
    store = ctx.store
    diagnostics = ctx.diagnostics
    rate_state = ctx.rate_state
    while True:
        state = ctx.agent.invoke(payload, config=ctx.invoke_config)
        ctx.state = state
        diagnostics.rate_limit_waits = rate_state.wait_count
        diagnostics.rate_limit_wait_seconds = rate_state.wait_seconds
        diagnostics.rate_limit_headers = rate_state.latest_headers
        diagnostics.skills_read = _skills_read(state)
        diagnostics.claims_failed = store.failed_claims
        diagnostics.claims_dropped = store.dropped_claims
        if store.export_paths:
            _emit_progress(
                ctx.progress_callback,
                {"type": "complete", "phase": "Exported", "message": "Brief exported"},
            )
            break
        interrupts = state.get("__interrupt__", [])
        if not interrupts:
            if not ctx.final_instruction_sent:
                ctx.final_instruction_sent = True
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
        if pause_for_approval:
            _emit_progress(
                ctx.progress_callback,
                {"type": "awaiting-approval", "phase": "Awaiting approval"},
            )
            return PendingApproval(ctx)
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
    _copy_usage(ctx.usage_total, ctx.usage.report)
    if store.record is None or store.report is None:
        raise RuntimeError("Export completed without a stored citation-checked record.")
    diagnostics.token_report = ctx.usage_total
    return ResearchRun(
        record=store.record,
        token_report=ctx.usage_total,
        elapsed_seconds=time.perf_counter() - ctx.started,
        skills_read=diagnostics.skills_read,
        mcp_status=ctx.mcp_status,
        rate_limit_waits=rate_state.wait_count,
        rate_limit_wait_seconds=rate_state.wait_seconds,
        rate_limit_headers=rate_state.latest_headers,
        citations=store.report,
        claims_failed=store.failed_claims,
        claims_dropped=store.dropped_claims,
        output_paths=[str(path) for path in store.export_paths],
        all_sources=ctx.registry.all(),
        activity=ctx.activity(),
    )


@_trace_run_parent
def _run_research_traced(
    settings: Settings,
    company: str,
    trace_status: dict[str, Any],
    *,
    domain: str | None = None,
    ticker: str | None = None,
    context: str | None = None,
    approval_handler: ApprovalHandler | None = None,
    auto_approve: bool = False,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    pause_for_approval: bool = False,
) -> "ResearchRun | PendingApproval":
    started = time.perf_counter()
    if trace_status.get("enabled"):
        _emit_progress(
            progress_callback,
            {
                "type": "trace-enabled",
                "phase": "Tracing",
                "project": trace_status.get("project"),
                "url": trace_status.get("url"),
            },
        )
    _emit_progress(progress_callback, {"type": "start", "phase": "Researching", "company": company})
    usage_total = TokenReport()
    rate_state = RateLimitState()
    models = [settings.model]
    if settings.fallback_model and settings.fallback_model != settings.model:
        models.append(settings.fallback_model)

    for model_index, model_name in enumerate(models):
        _emit_progress(
            progress_callback,
            {"type": "model-selected", "model": model_name, "phase": "Loading research tools"},
        )
        registry = SourceRegistry()
        mcp_tools, mcp_status = asyncio.run(load_mcp_tools(settings, registry))
        flags = ["SEC filings unavailable"] if not mcp_status.available and ticker else []
        if settings.mcp_enabled and not mcp_status.available:
            logger.error("SEC MCP tools failed to load: %s", mcp_status.reason)
        sec_prefetch = ""
        prefetch_activity: list[dict[str, Any]] = []
        if ticker and mcp_status.available:
            sec_prefetch = asyncio.run(
                _sec_prefetch_span(_prefetch_for(mcp_tools, registry, ticker))()
            )
            prefetch_activity = [
                {
                    "tool": "SEC prefetch",
                    "input": f"Company info, XBRL facts and recent filings for {ticker}",
                    "result": f"{len(_source_ids_in(sec_prefetch))} source(s)",
                    "source_ids": _source_ids_in(sec_prefetch),
                    "phase": "prefetch",
                }
            ]
        store = DraftSubmission()
        usage = TokenUsageHandler()
        diagnostics = ExportDiagnostics(
            model=model_name,
            token_report=usage.report,
            started_at=started,
        )
        submission_tool = make_submit_draft_tool(
            registry, store, model=model_name, initial_flags=flags, seller_context=context
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
        research_tools = make_research_tools(settings, registry)
        tool_names = [
            *(tool.name for tool in research_tools),
            *(tool.name for tool in mcp_tools),
            "submit_draft",
            "export_account",
        ]
        logger.info("Agent toolset: %s", ", ".join(tool_names))

        request = (
            f"Today's date is {date.today().isoformat()}.\n"
            f"Research {company}."
            + (f" Domain: {domain}." if domain else "")
            + (f" Ticker: {ticker}." if ticker else "")
            + (f" Seller context: {context}." if context else "")
            + (f"\n\n{sec_prefetch}" if sec_prefetch else "")
            + _research_plan(company, sec_prefetch_done=bool(sec_prefetch))
        )
        payload: Any = {
            "messages": [HumanMessage(content=request)],
            "files": seed_skill_files(settings),
        }
        ctx = _RunContext(
            settings=settings,
            model_name=model_name,
            agent=agent,
            invoke_config={
                "recursion_limit": settings.recursion_limit,
                "callbacks": [usage],
                "configurable": {"thread_id": str(uuid4())},
            },
            store=store,
            registry=registry,
            mcp_tools=mcp_tools,
            mcp_status=mcp_status,
            usage=usage,
            usage_total=usage_total,
            rate_state=rate_state,
            diagnostics=diagnostics,
            started=started,
            prefetch_activity=prefetch_activity,
            progress_callback=progress_callback,
        )
        try:
            return _drive(
                ctx,
                payload,
                pause_for_approval=pause_for_approval,
                approval_handler=approval_handler,
                auto_approve=auto_approve,
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
                    f"{observed}, below min_output_tpm={settings.min_output_tpm}. "
                    f"{model_name} failed with: {exc}",
                    usage_total,
                    started,
                    rate_state,
                ) from exc
            raise _research_error(str(exc), usage_total, started, rate_state) from exc
        except Exception as exc:
            _copy_usage(usage_total, usage.report)
            raise _research_error(str(exc), usage_total, started, rate_state) from exc
    raise _research_error("Research did not run", usage_total, started, rate_state)


def _prefetch_for(
    mcp_tools: list[Any], registry: SourceRegistry, ticker: str
) -> Callable[[], Any]:
    async def _prefetch() -> str:
        return await prefetch_sec_tools(mcp_tools, registry, ticker)

    return _prefetch


def _research_plan(company: str, *, sec_prefetch_done: bool) -> str:
    """Concrete next steps so small models search instead of re-querying SEC data."""
    sec_line = (
        "The SEC data above is already registered: cite its source IDs for financial figures and "
        "do not call SEC tools again or fetch sec.gov URLs. "
        if sec_prefetch_done
        else ""
    )
    return (
        "\n\nNext steps: "
        + sec_line
        + f'Run web_search for leadership commentary and recent news, e.g. "{company} earnings '
        f'call CFO commentary", "{company} quarterly results", and one or two queries aimed at '
        "the seller context. Use fetch_page on the 1-2 most relevant articles, then draft."
    )


def _sec_prefetch_span(prefetch: Any) -> Any:
    try:
        from langsmith import traceable

        @traceable(name="sec_prefetch")
        async def traced() -> str:
            return await prefetch()

        return traced
    except Exception:
        return prefetch
