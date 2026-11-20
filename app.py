"""Streamlit demo UI for the account research agent."""

from __future__ import annotations

from typing import Any

import streamlit as st

from account_research.agent.run import (
    PendingApproval,
    ResearchRun,
    ResearchRunError,
    start_research,
)
from account_research.config import get_settings
from account_research.interfaces.brief_export import email_body, email_subject, render_brief_html
from account_research.interfaces.ui_data import (
    sec_filings,
    sec_metrics,
    source_rows,
    tool_usage,
)
from account_research.tools.export_account import render_brief

SECTIONS = (
    ("Financial highlights", "financial_highlights"),
    ("Signals", "signals"),
    ("Priorities", "priorities"),
    ("Pain points", "pain_points"),
    ("Talking points", "talking_points"),
)
_LINK = st.column_config.LinkColumn("Link", display_text="Open")
_CITED = st.column_config.CheckboxColumn("Cited", help="Used by a claim that passed validation")


def _init_ui_state() -> None:
    """Initialize the session state used by the demo UI."""
    for key, default in (
        ("company", ""),
        ("ticker", ""),
        ("context", ""),
        ("decision", None),  # None while awaiting approval, then "approved" or "discarded"
        ("last_result", None),
        ("last_error", ""),
        ("trace_url", None),
    ):
        st.session_state.setdefault(key, default)


def _cited_ids(result: Any) -> set[str]:
    return set(getattr(result.citations, "cited_source_ids", []))


def _sources_by_id(result: Any) -> dict[str, Any]:
    return {source.id: source for source in (result.all_sources or result.record.sources)}


def _citation_links(source_ids: list[str], by_id: dict[str, Any]) -> str:
    links = []
    for source_id in source_ids:
        source = by_id.get(source_id)
        links.append(f"[{source_id}]({source.url})" if source else source_id)
    return " ".join(links)


# ---------------------------------------------------------------------------- run


def _run_research_demo() -> None:
    """Run research with live progress and store the result in session state."""
    settings = get_settings()
    company = st.session_state["company"].strip()
    ticker = st.session_state["ticker"].strip() or None
    context = st.session_state["context"].strip() or None
    if not company:
        st.warning("Enter a company name to run the research agent.")
        return

    st.session_state.update(decision=None, last_error="", trace_url=None, last_result=None)

    with st.status("Researching…", expanded=True) as status:

        def progress(event: dict[str, Any]) -> None:
            event_type = str(event.get("type", "phase"))
            if event_type == "rate-limit-wait":
                status.write(
                    f":material/hourglass_top: Rate limited, waiting {event.get('seconds')}s"
                )
            elif event_type == "fallback-triggered":
                status.write(
                    f":material/swap_horiz: Falling back from {event.get('from_model')} "
                    f"to {event.get('to_model')}"
                )
            elif event_type == "model-selected":
                status.write(f":material/smart_toy: Model: {event.get('model')}")
                status.write(":material/database: Pulling SEC EDGAR data and loading tools")
            elif event_type == "trace-enabled":
                status.write(
                    f":material/monitoring: Tracing to LangSmith project {event.get('project')}"
                )
            elif event_type == "start":
                status.write(f":material/travel_explore: Researching {event.get('company')}")
            elif event_type == "awaiting-approval":
                status.write(
                    ":material/fact_check: Draft checked against sources; awaiting approval"
                )

        try:
            result = start_research(
                settings,
                company,
                ticker=ticker,
                context=context,
                progress_callback=progress,
            )
        except ResearchRunError as exc:
            status.update(label="Research failed", state="error")
            st.session_state["last_error"] = str(exc)
            return
        except Exception as exc:  # pragma: no cover - UI-level reporting
            status.update(label="Research failed", state="error")
            st.session_state["last_error"] = str(exc)
            return
        status.update(
            label=f"Draft ready for review after {result.elapsed_seconds:.0f}s",
            state="complete",
            expanded=False,
        )

    st.session_state["last_result"] = result
    st.session_state["trace_url"] = getattr(result, "trace_url", None)


# ---------------------------------------------------------------------------- views


def _render_overview(result: Any) -> None:
    record = result.record
    sources = result.all_sources or record.sources
    cited = _cited_ids(result)
    sec_count = sum(1 for source in sources if source.kind in {"sec", "sec_filing"})
    passed = result.citations.passed_count
    total = passed + result.citations.failed_count

    st.header(record.company_name, divider="gray")
    with st.container(horizontal=True):
        decision = st.session_state.get("decision")
        if decision == "discarded":
            st.badge("Discarded", icon=":material/delete:", color="gray")
        elif isinstance(result, ResearchRun):
            st.badge("Approved and exported", icon=":material/task_alt:", color="green")
        else:
            st.badge("Awaiting approval", icon=":material/pending:", color="orange")
        if record.is_public:
            st.badge("Public company", icon=":material/show_chart:", color="blue")
        if record.industry:
            st.badge(record.industry, icon=":material/category:", color="gray")
        if record.domain:
            st.badge(record.domain, icon=":material/language:", color="gray")
        st.badge(record.model, icon=":material/smart_toy:", color="violet")

    with st.container(horizontal=True):
        st.metric("Sources gathered", len(sources), border=True)
        st.metric("SEC data points", sec_count, border=True)
        st.metric("Sources cited", len(cited), border=True)
        st.metric("Claims kept", f"{passed}/{total}", border=True)
        st.metric("Runtime", f"{result.elapsed_seconds:.0f}s", border=True)


def _render_brief(result: Any) -> None:
    record = result.record
    by_id = _sources_by_id(result)
    removed_by_section: dict[str, int] = {}
    for item in result.citations.failed_claims:
        removed_by_section[item.section] = removed_by_section.get(item.section, 0) + 1
    if record.summary:
        st.info(
            f"{record.summary.text} {_citation_links(record.summary.source_ids, by_id)}",
            title="Summary",
            icon=":material/summarize:",
        )
    for title, field in SECTIONS:
        claims = getattr(record, field)
        with st.container(border=True):
            st.subheader(title)
            if not claims:
                removed = removed_by_section.get(field, 0)
                st.caption(
                    f"{removed} claim(s) removed because the cited sources did not support them. "
                    "See the Validation tab."
                    if removed
                    else "The agent found no sourced evidence for this section."
                )
            for claim in claims:
                st.markdown(f"- {claim.text} {_citation_links(claim.source_ids, by_id)}")

    freshness = [flag for flag in record.flags if "stale or undated" in flag]
    if freshness:
        with st.expander(
            f"{len(freshness)} review note(s) on source dates", icon=":material/schedule:"
        ):
            for flag in freshness:
                st.warning(flag)


def _render_sec(result: Any) -> None:
    sources = result.all_sources or result.record.sources
    cited = _cited_ids(result)
    metrics = sec_metrics(sources, cited)
    filings = sec_filings(sources, cited)
    if not metrics and not filings:
        st.info(
            "No SEC EDGAR data for this run. Enter a ticker for a US-listed company to pull "
            "reported financials.",
            icon=":material/info:",
        )
        return
    st.caption(
        "Reported figures pulled directly from SEC EDGAR (XBRL company facts). They are shown "
        "whether or not the model cited them in the brief."
    )
    if metrics:
        st.subheader("Reported financials")
        st.dataframe(
            metrics,
            hide_index=True,
            column_config={"Link": _LINK, "Cited in brief": _CITED},
            alt="Latest reported financial metrics from SEC EDGAR",
        )
    if filings:
        st.subheader("Recent filings")
        st.dataframe(
            filings,
            hide_index=True,
            column_config={"Link": _LINK, "Cited in brief": _CITED},
            alt="Recent SEC filings for the company",
        )


def _render_sources(result: Any) -> None:
    sources = result.all_sources or result.record.sources
    rows = source_rows(sources, _cited_ids(result))
    if not rows:
        st.info("No sources were registered for this run.", icon=":material/info:")
        return
    view = st.segmented_control(
        "Show",
        ["All", "Cited", "SEC EDGAR", "Web"],
        default="All",
        key="source_filter",
    )
    if view == "Cited":
        rows = [row for row in rows if row["Cited"]]
    elif view == "SEC EDGAR":
        rows = [row for row in rows if row["Type"] == "SEC EDGAR"]
    elif view == "Web":
        rows = [row for row in rows if row["Type"] != "SEC EDGAR"]
    st.caption(
        "Every source the agent gathered. IDs are numbered in the order sources were found "
        "(SEC data first, then web results); the brief cites only the ones its claims rely on."
    )
    st.dataframe(
        rows,
        hide_index=True,
        column_config={"Link": _LINK, "Cited": _CITED},
        alt="All sources gathered during the research run",
    )


def _render_activity(settings: Any, result: Any) -> None:
    usage = tool_usage(result.activity)
    with st.container(horizontal=True):
        st.metric(
            "Model calls",
            f"{result.token_report.model_calls}/{settings.max_model_calls}",
            border=True,
        )
        st.metric(
            "Web searches", f"{usage.get('web_search', 0)}/{settings.search_budget}", border=True
        )
        st.metric(
            "Pages read", f"{usage.get('fetch_page', 0)}/{settings.max_page_fetches}", border=True
        )
        st.metric("Agent SEC calls", f"{usage.get('sec', 0)}/{settings.max_sec_calls}", border=True)
        st.metric(
            "Tokens",
            f"{result.token_report.input_tokens + result.token_report.output_tokens:,}",
            border=True,
        )
    if not result.activity:
        st.caption("No tool activity was recorded.")
        return
    st.caption("Each research step in order, with the sources it registered.")
    rows = [
        {
            "Step": index,
            "Tool": step["tool"],
            "Input": step["input"],
            "Result": step["result"],
            "Sources": ", ".join(step["source_ids"][:12])
            + (" …" if len(step["source_ids"]) > 12 else ""),
        }
        for index, step in enumerate(result.activity, start=1)
    ]
    st.dataframe(rows, hide_index=True, alt="Agent research steps in order")


def _render_validation(result: Any) -> None:
    passed = result.citations.passed_claims
    failed = result.citations.failed_claims
    st.caption(
        "Every claim must cite a source and quote it; figures and fiscal periods are checked "
        "against the cited source text. Claims that fail are removed from the brief."
    )
    with st.container(horizontal=True):
        st.metric("Passed", len(passed), border=True)
        st.metric("Removed", len(failed), border=True)
    if failed:
        st.subheader("Removed claims")
        for item in failed:
            cited_ids = ", ".join(item.claim.source_ids) or "none"
            st.error(
                f"{item.claim.text}\n\nReason: {item.reason}. Cited: {cited_ids}.",
                icon=":material/block:",
            )
    if passed:
        with st.expander(
            f"{len(passed)} passed claim(s) with evidence", icon=":material/verified:"
        ):
            for claim in passed:
                quote = f" — “{claim.evidence_quote}”" if claim.evidence_quote else ""
                st.markdown(f"- {claim.text} ({', '.join(claim.source_ids)}){quote}")


def _decide(label: str, action: Any) -> None:
    """Run an approval action with visible progress, then refresh the page."""
    with st.status(label, expanded=True) as status:
        try:
            outcome = action()
        except Exception as exc:  # noqa: BLE001 - surface any agent failure in the UI
            status.update(label="Action failed", state="error")
            st.session_state["last_error"] = str(exc)
            return
        status.update(label="Done", state="complete")
    if isinstance(outcome, ResearchRun):
        st.session_state.update(last_result=outcome, decision="approved")
    elif isinstance(outcome, PendingApproval):
        st.session_state.update(last_result=outcome, decision=None)
    st.rerun()


def _render_review(result: PendingApproval) -> None:
    with st.container(border=True):
        st.subheader("Review and approve")
        st.caption(
            "Nothing has been exported yet. Approve to have the agent write the brief, record, "
            "and run report. Request changes to send your feedback back to the agent"
            + (" for one revision." if result.can_revise else "; this draft was already revised.")
        )
        feedback = st.text_area(
            "Feedback for the agent",
            key="feedback",
            placeholder="For example: lead with cost discipline and drop the stock-price claim.",
            disabled=not result.can_revise,
        )
        with st.container(horizontal=True):
            approve = st.button("Approve and export", icon=":material/check:", type="primary")
            revise = st.button(
                "Request changes",
                icon=":material/edit_note:",
                disabled=not result.can_revise or not feedback.strip(),
            )
            discard = st.button("Discard", icon=":material/delete:")
        if approve:
            _decide("Exporting the approved brief…", result.approve)
        elif revise:
            _decide("Agent is revising the brief…", lambda: result.reject(feedback))
        elif discard:
            st.session_state["decision"] = "discarded"
            st.rerun()


def _render_share(result: ResearchRun) -> None:
    record = result.record
    slug = "".join(ch if ch.isalnum() else "-" for ch in record.company_name.lower()).strip("-")
    markdown = render_brief(record)
    with st.container(border=True):
        st.subheader("Share the brief")
        st.success("Approved and exported.", icon=":material/task_alt:")
        with st.container(horizontal=True):
            st.download_button(
                "Brief (HTML)",
                render_brief_html(record),
                file_name=f"{slug}-brief.html",
                mime="text/html",
                icon=":material/download:",
                type="primary",
            )
            st.download_button(
                "Brief (Markdown)",
                markdown,
                file_name=f"{slug}-brief.md",
                mime="text/markdown",
                icon=":material/download:",
            )
            st.download_button(
                "Account record (JSON)",
                record.model_dump_json(indent=2),
                file_name=f"{slug}-account.json",
                mime="application/json",
                icon=":material/data_object:",
            )
        st.caption("Attach the HTML brief to an email, or open it in a browser and print to PDF.")
        with st.expander("Copy as email", icon=":material/mail:"):
            st.text_input("Subject", email_subject(record), key="email_subject")
            st.code(email_body(record), language=None, wrap_lines=True)
        with st.expander("Exported files", icon=":material/folder:"):
            for path in result.output_paths:
                st.code(path, language=None)


def _render_decision(result: Any) -> None:
    decision = st.session_state.get("decision")
    if decision == "discarded":
        st.warning("Draft discarded. Nothing was exported.", icon=":material/delete:")
    elif isinstance(result, ResearchRun):
        _render_share(result)
    else:
        _render_review(result)


# ---------------------------------------------------------------------------- page


def main() -> None:
    """Render the demo app."""
    st.set_page_config(
        page_title="Account research agent", page_icon=":material/travel_explore:", layout="wide"
    )
    _init_ui_state()
    settings = get_settings()

    with st.sidebar:
        st.header("Research inputs")
        with st.form("inputs", border=False):
            st.text_input("Company", key="company")
            st.text_input(
                "Ticker", key="ticker", help="Add a US ticker to pull SEC EDGAR financials."
            )
            st.text_area("Seller context", key="context", height=140)
            submitted = st.form_submit_button(
                "Run research", type="primary", icon=":material/play_arrow:", width="stretch"
            )
        st.caption(f"Model: `{settings.model}`")
        if st.session_state.get("trace_url"):
            st.link_button(
                "Open trace", st.session_state["trace_url"], icon=":material/monitoring:"
            )

    st.title("Account research agent")
    st.caption("Sourced account briefs with SEC data, citation checks, and human approval.")

    if submitted:
        _run_research_demo()

    if st.session_state.get("last_error"):
        st.error(st.session_state["last_error"], title="Research failed", icon=":material/error:")

    result = st.session_state.get("last_result")
    if result is None:
        st.info(
            "Enter a company in the sidebar and select **Run research**.",
            icon=":material/arrow_back:",
        )
        return

    _render_overview(result)
    sources = result.all_sources or result.record.sources
    brief, sec, all_sources, activity, validation = st.tabs(
        [
            ":material/description: Brief",
            ":material/account_balance: SEC financials",
            f":material/library_books: All sources ({len(sources)})",
            ":material/timeline: Agent activity",
            ":material/fact_check: Validation",
        ]
    )
    with brief:
        _render_brief(result)
    with sec:
        _render_sec(result)
    with all_sources:
        _render_sources(result)
    with activity:
        _render_activity(settings, result)
    with validation:
        _render_validation(result)
    _render_decision(result)


if __name__ == "__main__":
    main()
