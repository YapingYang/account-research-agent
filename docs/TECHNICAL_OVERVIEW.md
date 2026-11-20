# Account Research Agent — Technical Overview

> Code-verified snapshot of the working tree on 2026-11-17 (branch `main`, HEAD `49f52a1`, plus
> ~3,200 lines of uncommitted changes across 32 files). Every statement cites the code. Labels:
> **[PARTIAL]** implemented but incomplete, **[PLANNED]** referenced but not built,
> **[UNUSED]** present but never called, **[UNVERIFIED]** implemented but not exercised live.
> Where docs and code disagree, the code wins and the discrepancy is noted (see §18).

Test result at time of writing: **`pytest -q` → 121 passed, 13 warnings (3.3 s)**. The warnings
are a third-party `asyncio.iscoroutinefunction` deprecation inside `langsmith`.

---

## 1. Summary

The Account Research Agent produces a sourced, citation-checked sales brief on one company. A
single LangChain **Deep Agents** agent researches the company with Tavily web search, page
extraction and (for US tickers) an allowlisted **SEC EDGAR MCP** server. Every retrieved item goes
into a run-scoped source registry with a stable ID (`S1`, `S2`, …). The agent submits a structured
draft through `submit_draft`. Application code, not the LLM, then validates every claim against
the registered source text: source IDs must exist, numbers and fiscal periods must match, and the
evidence quote must appear in the source. It removes what fails and builds an `AccountRecord`. A
LangGraph interrupt then pauses before `export_account` for human approval. Only after approval
are `brief.md`, `account.json` and `run-report.md` written. The system has two front ends: a Typer
CLI (`ara`) and a Streamlit demo UI (`app.py`).

---

## 2. Use cases and boundaries

### Intended users and problems solved
- **Sellers and account executives** preparing for a meeting, for example "meeting their CFO about
  finance automation". It replaces manual research with a cited brief: financial highlights,
  signals, priorities, pain-point hypotheses and talking points tied to the seller's context.
- **Demo and evaluation audiences.** The Streamlit UI shows the agent's steps, every gathered
  source, validation results and the approval gate (`app.py`).

### Supported inputs and how each changes behavior

| Input | CLI | UI | Effect in code |
|---|---|---|---|
| Company name (required) | positional `company` | sidebar "Company" | Goes into the request text (`_run_research_traced`, `agent/run.py` ~L566) and the research plan queries (`_research_plan`, L637). Its slug names the output folder (`make_export_account_tool`). |
| Ticker | `--ticker` | "Ticker" | Enables the **SEC prefetch** (`prefetch_sec_tools`) when MCP is available. Without MCP, adds the flag `"SEC filings unavailable"` (`run.py` ~L517). |
| Seller context | `--context` | "Seller context" | Added to the request text. Passed to `make_submit_draft_tool(seller_context=…)`: if priorities, pain points or talking points are empty, the first submission is returned asking the model to fill them (`submission.py`, `_empty_seller_sections`). |
| Domain | `--domain` | — (not exposed) | Appended to the request text only. |
| Model | `--model` | — (fixed by `ARA_MODEL`) | Overrides `settings.model`. |
| Budgets | `--search-budget`, `--max-page-fetches`, `--max-search-results`, `--no-mcp` | — | `settings.model_copy(update=…)` in `interfaces/cli.py::research`. |
| Replay | `--replay PATH` | — | Loads a saved snapshot instead of running a model (`_replay_research`). |

### What it explicitly does not do (enforced in code)
- **No writes before approval.** `export_account` is the only writer, and `interrupt_on={"export_account": True}` pauses before it runs (`agent/builder.py::build_agent` L88). It takes no arguments (`_NoArguments`, `tools/export_account.py`), so the model cannot supply record content.
- **No private-network fetches.** `_validate_public_url` rejects non-HTTP(S) URLs and any host that resolves to a non-global IP (`tools/fetch_page.py`).
- **No sec.gov page fetches.** `fetch_page` returns a refusal without spending budget (`fetch_page.py::fetch`).
- **No non-allowlisted SEC tools.** `filter_tools` drops every MCP tool not in `allowed_tools` (`mcp/allowlist.py`).
- **No subagents.** `register_harness_profile(... general_purpose_subagent enabled=False)` and `subagents=[]` (`builder.py` L39–44, L113). The `task` tool is still injected by Deep Agents (§7) [UNVERIFIED whether it can do anything without subagents].
- **No CRM, email sending or Slack.** Output is local files plus UI downloads only (§3).
- **No unvalidated claims in output.** Failed claims are dropped (`checks/citations.py::to_account_record`).

---

## 3. User interaction

### 3.1 CLI (`ara`, `interfaces/cli.py`; entry point `[project.scripts] ara`)

| Command | Arguments / options | Behavior |
|---|---|---|
| `ara --version` | — | Prints `__version__` (`0.1.0`). |
| `ara doctor` | — | Prints redacted settings (`Settings.redacted`), MCP server status, skills. Exit 1 if a required key is missing (provider key for the selected model, `TAVILY_API_KEY`, `SEC_EDGAR_USER_AGENT` when the SEC server is enabled). |
| `ara skills` | — | Table of discovered `SKILL.md` files (`skills/loader.py::discover_skills`). |
| `ara research COMPANY` | `--domain --ticker --context --model --yes --replay --no-mcp --search-budget --max-page-fetches --max-search-results` | Full research run via `run_research`. Without `--yes`, uses the interactive approval prompt. |
| `ara mcp-tools` | — | Discovers raw MCP tools, prints schemas and ≈token cost (chars/4). |
| `ara tools-check COMPANY` | `--ticker` | Exercises search → fetch → each allowlisted MCP tool **without an LLM**. Prints sizes and registered sources. |
| `ara spike` | `--trials --pause-seconds --models` | Test-only model capability spike (`spike/run.py`). |

**Interactive approval** (`_approval_prompt`): prints `render_brief(record)`, then
`[a]pprove / [r]eject`. Reject asks for one-line feedback, which is sent back to the agent. The
run allows one revision (`DraftSubmission.begin_revision`). A second rejection raises `Export
rejected twice` (`run.py::_drive`).

**CLI outputs:** the exported paths, the rendered brief, a token report JSON, claims
passed/failed/dropped, flags and rate-limit headers. The CLI also writes a **replay snapshot**
`output/replay/<slug>.json` (`_save_run_snapshot`).
- Key validation before a live run: `_check_runtime_keys` (Anthropic, OpenAI or Groq key, plus Tavily).
- On failure, `ResearchRunError` carries the token report, elapsed time and rate-limit diagnostics, which are printed.

### 3.2 Streamlit UI (`app.py`)

**Layout**
- **Sidebar:** a `st.form` with Company, Ticker and Seller context, a **Run research** button, the model caption and an **Open trace** link when tracing is enabled.
- **Main area:** title, then the results.
  - **During a run:** an `st.status` box shows progress events from `progress_callback`. The handled event types are `trace-enabled`, `start`, `model-selected`, `rate-limit-wait`, `fallback-triggered` and `awaiting-approval` (`_run_research_demo`).
  - **After a run:**
    - **Overview** (`_render_overview`): status badge (Awaiting approval / Approved and exported / Discarded), public/industry/domain/model badges, and metrics (sources gathered, SEC data points, sources cited, claims kept, runtime).
    - **Tabs:**
      - *Brief* (`_render_brief`): summary plus section cards with clickable citations. An empty section explains whether claims were removed or none were found.
      - *SEC financials* (`_render_sec`): XBRL metrics table and recent filings table, built from all SEC sources whether or not they were cited (`interfaces/ui_data.py::sec_metrics`, `sec_filings`).
      - *All sources (N)* (`_render_sources`): every registered source with a Cited marker, filterable All / Cited / SEC EDGAR / Web.
      - *Agent activity* (`_render_activity`): budget-usage metrics plus a step table (tool, input, result, source IDs) from `ResearchRun.activity`.
      - *Validation* (`_render_validation`): passed/removed counts, removed claims with reasons, and passed claims with their evidence quotes.
    - **Decision panel** (`_render_decision`) under the tabs.

**Approve/reject flow** (real human-in-the-loop, `_render_review` / `_decide`)
- **Run research** calls `start_research(...)`, which returns a `PendingApproval`. Nothing has been written yet.
- **Approve and export** → `PendingApproval.approve()` resumes the graph with `Command(resume={"decisions":[{"type":"approve"}]})`. The agent executes `export_account` and returns a `ResearchRun`.
- **Request changes** (needs feedback text, one revision) → `PendingApproval.reject(feedback)` resumes with a reject decision. The agent revises, and the result is a new `PendingApproval`.
- **Discard** → sets `decision="discarded"`; nothing is exported.
- **After approval** (`_render_share`): downloads for the brief as HTML (`render_brief_html`), Markdown (`render_brief`) and JSON, a "Copy as email" expander (`email_subject`, `email_body`), and the exported paths.

**Session state and resume**
- Keys: `company`, `ticker`, `context`, `decision`, `last_result`, `last_error`, `trace_url`, `feedback`, `source_filter`, `email_subject`.
- `last_result` holds the live `PendingApproval`. That object keeps the LangGraph agent, its in-memory `MemorySaver` checkpointer, the thread ID and the source registry (`_RunContext`, `run.py` L286).
- Resuming works across Streamlit reruns of **the same browser session and server process** only. A reload or restart loses the paused run, because the checkpointer is in memory.

### 3.3 Output artifacts

| File | Writer | Format | Location |
|---|---|---|---|
| `brief.md` | `export_account` → `render_brief` | Markdown: flags, header fields, sections with `[sources: S5, 2026-07-31]`, source list | `<output_dir>/<company-slug>/` |
| `account.json` | `export_account` | `AccountRecord.model_dump_json(indent=2)` (schema §9) | same |
| `run-report.md` | `export_account` → `_run_report` | Markdown table: model, calls, tokens, waits, runtime, claims passed/failed/dropped, skills read, flags, last rate-limit headers | same |
| `replay/<slug>.json` | CLI `_save_run_snapshot` | usage plus full record (for `--replay`) | `<output_dir>/replay/` |
| HTML / MD / JSON downloads | UI `_render_share` | `render_brief_html` (self-contained, escaped) | browser download |

- Writes are atomic: each file goes to `*.tmp` first, then all are renamed into place (`make_export_account_tool`).
- The slug is `re.sub(r"[^a-z0-9]+","-", name.casefold())`.
- `output_dir` defaults to `output/`.

---

## 4. Architecture

### 4.1 Component diagram

```mermaid
flowchart TB
  subgraph UI[Front ends]
    CLI[ara CLI<br/>interfaces/cli.py]
    ST[Streamlit app.py]
  end
  CLI -->|run_research| RUN
  ST -->|start_research / approve / reject| RUN
  subgraph RUN[agent/run.py]
    TR[configure_tracing] --> PF[SEC prefetch<br/>prefetch_sec_tools]
    PF --> AG[build_agent → Deep Agent graph]
    AG --> DRV[_drive loop]
  end
  subgraph AGENT[Deep Agent + middleware]
    MW1[ModelCallBudgetMiddleware] --> MW2[RequestSizeMiddleware] --> MW3[RateLimitMiddleware] --> MW4[SummarizationMiddleware]
    MW4 --> LLM[(Chat model<br/>models/factory.py)]
  end
  AG --- AGENT
  subgraph TOOLS[Tools]
    WS[web_search<br/>Tavily search]
    FP[fetch_page<br/>Tavily extract]
    SEC[SEC MCP tools<br/>wrapped + allowlisted]
    SD[submit_draft]
    EX[export_account<br/>interrupt]
  end
  LLM --> TOOLS
  WS & FP & SEC --> REG[(SourceRegistry<br/>S1..Sn)]
  SD --> SH[add_sec_highlights] --> CC[check_citations] --> REC[to_account_record]
  REG --> SH
  REG --> CC
  REC --> PEND[DraftSubmission.record]
  EX -->|after human approval| FILES[(brief.md · account.json · run-report.md)]
  PEND --> EX
  SECSRV[[sec-edgar-mcp<br/>stdio subprocess]] --- SEC
  TAV[[Tavily API]] --- WS & FP
```

### 4.2 Request lifecycle (Streamlit path; CLI is identical except for approval)

1. **Input**: `app.py::_run_research_demo` reads session state and calls `agent/run.py::start_research(settings, company, ticker=, context=, progress_callback=)`.
2. **Tracing setup** (outside the traced span): `_start` → `observability/tracing.py::configure_tracing`.
3. **Traced parent span**: `_run_research_traced`, decorated by `_trace_run_parent` (`@traceable(name="account_research_run")`).
4. **Model loop**: for each of `[settings.model, settings.fallback_model?]`:
   1. `SourceRegistry()` is created (L514).
   2. `asyncio.run(load_mcp_tools(settings, registry))` → `mcp/client.py::discover_raw_tools`, `filter_tools`, `_wrap_tool`.
   3. **SEC prefetch** when a ticker is given and MCP is available: `prefetch_sec_tools` calls `get_company_info`, `get_company_facts` and `get_recent_filings`, registers sources, refunds the SEC budget, and returns tagged text (wrapped in a `sec_prefetch` trace span).
   4. Per-run objects are created: `DraftSubmission`, `TokenUsageHandler`, `ExportDiagnostics`, `make_submit_draft_tool`, `make_export_account_tool`.
   5. `agent/builder.py::build_agent` → `get_chat_model` → `create_deep_agent(...)`.
   6. The request message is built: date, company, domain, ticker, context, prefetch text, and `_research_plan`.
   7. `_RunContext` is created → `_drive(ctx, payload, pause_for_approval=True)`.
5. **Agent loop** (`_drive` → `agent.invoke`): the LLM plans and calls `web_search` / `fetch_page` / SEC tools. Each tool registers sources and returns `<untrusted_content source_id=…>` blocks.
6. **Draft**: the LLM calls `submit_draft` (`agent/submission.py`):
   1. `add_sec_highlights` (`sources/sec_metrics.py`) adds the latest SEC figures.
   2. `check_citations` (`checks/citations.py`) validates every claim.
   3. On failures or empty seller sections the draft is returned once with repair hints (`_closest_evidence`).
   4. Otherwise `to_account_record` is stored in `DraftSubmission.record`.
7. **Interrupt**: the LLM calls `export_account`. `HumanInTheLoopMiddleware` interrupts. `_drive` sees `__interrupt__` and returns `PendingApproval(ctx)`.
8. **Human decision**: `approve()` / `reject(feedback)` → `_continue` → `_drive` with a `Command(resume=…)`.
9. **Export**: `export_account()` re-validates `AccountRecord`, writes the three files atomically and sets `export_paths`. `_drive` builds the `ResearchRun` (with `all_sources`, `activity`).
10. **Fallback** (`ModelUnavailableError` only): the next model is tried if the primary's `x-ratelimit-limit-output-tokens` ≥ `min_output_tpm`. Otherwise the run raises a `ResearchRunError` that includes the primary's error.

### 4.3 Fixed code vs. LLM decisions

| Decided by fixed code | Decided by the LLM |
|---|---|
| Which tools exist; SEC allowlist; budgets and caps | Which tools to call, in what order, with what queries/URLs |
| SEC prefetch (3 calls) and the suggested research plan text | Whether to follow the plan; which sources to read |
| Source IDs, dedup, dates and freshness | Which source IDs to cite |
| Claim validation (IDs, numbers, periods, quotes), quote repair, drops | Claim wording, section placement, evidence quote |
| Adding SEC financial highlights (`add_sec_highlights`) | All other claim content |
| One repair round; empty-section request (with seller context) | How to repair |
| Interrupt before export; approval resume | — (approval is human) |
| Export content (only the stored validated record) | When to call `export_account` |

The boundary sits at the **`submit_draft` tool**. The LLM's draft enters as `BriefDraft`, and
everything after it is deterministic Python.

---

## 5. Repository structure

```
app.py                         Streamlit demo UI (start/approve/reject/share)
mcp_servers.json               SEC EDGAR MCP server launch + 4-tool allowlist
pyproject.toml / uv.lock       Dependencies, extras, ruff/mypy/pytest config, `ara` entry point
Makefile, .github/workflows/ci.yml   lint/format/typecheck/test (Python 3.11 + 3.12)
skills/
  account-brief/SKILL.md       Section guidance for BriefDraft
  sec-filings/SKILL.md         SEC tool order and extraction guidance
  spike/fact-format/SKILL.md   Test-only skill for the model spike
src/account_research/
  config.py                    Settings (pydantic-settings, ARA_ prefix, .env)
  logging.py                   configure_logging [UNUSED]
  agent/builder.py             build_agent, seed_skill_files, subagent disable
  agent/run.py                 run_research/start_research, PendingApproval, _drive, activity log
  agent/submission.py          submit_draft tool, DraftSubmission, repair hints
  checks/citations.py          check_citations, quote repair, to_account_record, freshness flags
  interfaces/cli.py            Typer CLI
  interfaces/ui_data.py        Display rows for UI tables (no Streamlit import)
  interfaces/brief_export.py   HTML brief + email subject/body renderers
  mcp/config.py                MCP config models + loader
  mcp/allowlist.py             Deny-by-default tool filter
  mcp/client.py                MCP discovery, tool wrapping, SEC source parsing, prefetch
  models/factory.py            get_chat_model for anthropic/groq/openai/ollama/gemini
  models/budget.py             Model-call budget + request-size trimming middleware
  models/ratelimit.py          Rate-limit header state, waits/backoff, billing fail-fast
  observability/tokens.py      TokenReport + TokenUsageHandler callback
  observability/tracing.py     LangSmith/Langfuse configuration
  prompts/main.md, __init__.py System prompt + loader
  schemas/account.py           Claim, Source, BriefDraft, AccountRecord
  skills/loader.py             Skill discovery + production skill paths
  sources/registry.py          SourceRegistry, URL normalization, date coercion, freshness
  sources/sec_metrics.py       XBRL metric parsing/formatting + add_sec_highlights
  spike/run.py, spike/tools.py Test-only model capability spike + fixture tool
  tools/factory.py             make_research_tools (web_search + fetch_page)
  tools/web_search.py          Tavily search tool
  tools/fetch_page.py          Tavily extract tool with SSRF/sec.gov guards
  tools/export_account.py      Approved export tool, render_brief, run report
  tools/budget.py              ToolCallBudget + budget messages
  tools/_text.py               truncate, wrap_untrusted
tests/                         121 offline tests + fixtures/snowflake_rejected_quotes.json
docs/                          ADRs 0001–0007, architecture, verification, run results, spike results
```

---

## 6. Module reference

### config (`config.py`)
- **Responsibility**: all runtime settings. Defined by `Settings(BaseSettings)` with `env_prefix="ARA_"`, `env_file=".env"`, `extra="ignore"`.
- **Key API**:
  - `Settings.request_token_cap(model=None) -> int`: an explicit `max_request_tokens` wins; otherwise 6,500 for `groq:` models and 32,000 for everything else.
  - `Settings.redacted() -> dict`: masks the Anthropic, Groq, OpenAI, Google and Tavily keys.
  - `get_settings()`: cached with `lru_cache`.
- **Validation**: `validate_positive_limits` rejects non-positive limits. Field constraints include `search_budget` 1–25 and `max_search_results` 1–20. Full list in §14.

### cli (`interfaces/cli.py`)
- See §3.1.
- **Dependencies**: `agent.run`, `mcp.client`, `mcp.config`, `skills.loader`, `tools.factory`, `tools.export_account`.
- **Edge case**: `_replay_research` builds `CitationReport([], [], ids, ids)`, so a replay prints `Claims passed=0` whatever the record holds.

### app (`app.py`)
- See §3.2.
- **Dependencies**: `agent.run` (`start_research`, `PendingApproval`, `ResearchRun`), `interfaces.ui_data`, `interfaces.brief_export`, `tools.export_account.render_brief`.
- **Implementation notes**:
  - Approve/revise actions run inside `st.status` and then call `st.rerun()`.
  - All views are duck-typed over `PendingApproval` and `ResearchRun`, which share `record`, `citations`, `all_sources`, `activity`, `token_report` and `elapsed_seconds`.

### agent/run (`agent/run.py`)
- **Responsibility**: run orchestration, model fallback, approval pause/resume, diagnostics.
- **Public API**:
  - `run_research(settings, company, *, domain, ticker, context, approval_handler, auto_approve, progress_callback) -> ResearchRun`: blocking, used by the CLI.
  - `start_research(settings, company, *, domain, ticker, context, progress_callback) -> PendingApproval`: pauses at the interrupt, used by the UI.
  - `PendingApproval`: `.record`, `.citations`, `.all_sources`, `.activity`, `.token_report`, `.can_revise`, `.approve() -> ResearchRun`, `.reject(feedback) -> PendingApproval`.
  - `ResearchRun` (dataclass): `record`, `token_report`, `elapsed_seconds`, `skills_read`, `mcp_status`, `rate_limit_*`, `citations`, `claims_failed`, `claims_dropped`, `output_paths`, `trace_url`, `all_sources`, `activity`.
  - `ResearchRunError(message, token_report, elapsed_seconds, rate_limit_*)`.
- **Internals**:
  - `_drive` is the invoke loop (shown below).
  - `_tool_activity` turns `state["messages"]` into activity rows for `web_search`, `fetch_page`, `submit_draft`, `export_account` and the SEC tools. Source IDs are extracted with the regex `source_id="(S\d+)"|"id":\s*"(S\d+)"`.
  - `_research_plan` builds the "Next steps" text appended to the request.
  - `_skills_read` detects `read_file` calls on the skill paths.

```python
# _drive (simplified)
while True:
    state = ctx.agent.invoke(payload, config=ctx.invoke_config)
    if store.export_paths: break                      # exported → ResearchRun
    if not state.get("__interrupt__"):
        if not ctx.final_instruction_sent:           # agent stopped early: nudge once
            payload = {"messages":[HumanMessage(FINAL_DRAFT_INSTRUCTION or "Call export_account now.")]}; continue
        raise ValueError("Agent ended without ...")
    if pause_for_approval: return PendingApproval(ctx)
    ...approval_handler / auto_approve → Command(resume={"decisions":[...]})
```

- **Edge cases**:
  - A fresh `thread_id` (`uuid4`) is created per model attempt.
  - Usage is folded into `usage_total` on export or error (`_copy_usage`).
  - `PendingApproval._continue` wraps any exception in `ResearchRunError`.

### agent/builder (`agent/builder.py`)
- **`build_agent(settings, registry, mcp_tools, *, model_name, model, rate_limit_state, extra_tools, submission, usage_report, trace_events) -> CompiledGraph`**
  - Tools: `make_research_tools` + `mcp_tools` + `extra_tools` (`submit_draft`, `export_account`).
  - Middleware order: `ModelCallBudgetMiddleware` → `RequestSizeMiddleware` (both only when `submission` and `usage_report` are given) → `RateLimitMiddleware` → `SummarizationMiddleware`.
  - Other arguments to `create_deep_agent`: `skills=["/skills/"]`, `backend=StateBackend()`, `interrupt_on={"export_account": True}`, `checkpointer=MemorySaver()`, `subagents=[]`.
  - `SummarizationMiddleware` triggers at `max(4800, 0.75 × request_token_cap)` tokens (4,875 for Groq, 24,000 otherwise) and keeps the last 8 messages.
- **`seed_skill_files(settings)`**: loads `skills/account-brief` and `skills/sec-filings` into virtual files `/skills/<name>/SKILL.md` for the `StateBackend`.

### prompts and skills
- `prompts/__init__.py::load_prompt(name)` accepts only `"main"`.
- Content is summarized in §7.
- `tests/test_prompts.py` caps the prompt at 1,400 characters; it is currently 1,367.
- `skills/loader.py`:
  - `discover_skills(root)` parses YAML frontmatter and requires `name` and `description`.
  - `agent_skill_paths()` returns the fixed production order and excludes `spike/`.

### tools — web_search (`tools/web_search.py::make_web_search`)
- **Inputs**: `query: str`.
- **Output**: a JSON list of `{id, title, url, date, freshness, snippet}`.
- **Budget**: `ToolCallBudget(search_budget)`. When exhausted it returns `BUDGET_REACHED` ("Budget reached. Draft and call submit_draft now.").
- **Tavily parameters** (`_search_kwargs`):
  - Always: `max_results=max_search_results`, `timeout=request_timeout_seconds`.
  - A query containing latest/earnings/news/financial/quarter/fiscal/announc is "fresh": `topic="finance"`, `time_range="year"`.
  - Otherwise: `topic=default_search_topic` ("general") and `time_range=default_search_time_range` ("year").
  - No domain filters are set.
- **Registration**: `registry.record(url, title, "search_result", published_date=item.published_date, content=truncate(content, snippet_chars), date_source="tavily"|"none")`.

### tools — fetch_page (`tools/fetch_page.py::make_fetch_page`)
- **Input**: `url`.
- **Guards**:
  - sec.gov hosts get a refusal and no budget is spent.
  - `PAGE_BUDGET_REACHED` when the budget is exhausted.
  - `_validate_public_url` performs the SSRF check.
- **Processing**:
  - `client.extract(urls=[url], format="text", extract_depth="basic")`.
  - `_main_text` keeps only lines with six or more words, which drops navigation chrome.
  - `truncate(…, max_page_chars)` adds the suffix `…[truncated]`.
  - Registered as `web_page`, so it upgrades an earlier `search_result` record for the same URL. Returns `wrap_untrusted`.

### tools — submit_draft (`agent/submission.py::make_submit_draft_tool(registry, store, *, model, initial_flags, seller_context)`)
- **Arguments**: `args_schema=BriefDraft`.
- **Flow**:
  - `add_sec_highlights` → `check_citations`.
  - Accepted if there are no failures, and either there are no empty seller sections or the repair round is already used.
  - First failure: returns per-claim reasons plus the closest real source sentence (`_closest_evidence`), the valid IDs, and the empty-section request. Sets `resubmissions=1`.
  - Second failure: accepts the draft with failures dropped (`dropped_claims`).
- **`DraftSubmission`**: `record`, `report`, `resubmissions`, `submit_calls`, `failed_claims`, `dropped_claims`, `revisions`, `export_paths`. `begin_revision()` allows one human-requested revision.

### tools — export_account (`tools/export_account.py`)
- **`make_export_account_tool(pending_record, output_dir, diagnostics)`**:
  - Zero-argument tool with `return_direct=True`.
  - Re-validates the pending record, writes the three files atomically and records `export_paths`.
- **`render_brief(record)`**: the Markdown brief.
- **`_run_report`**: the run-report table.
- **`record_claim_count`**: number of claims kept.

### MCP client and SEC prefetch (`mcp/client.py`)
- **`discover_raw_tools(settings) -> (tools, McpStatus)`**:
  - Launches the configured stdio servers through `langchain_mcp_adapters.MultiServerMCPClient`. Environment comes from `env_from_settings` (e.g. `SEC_EDGAR_USER_AGENT`).
  - Applies `mcp_timeout_seconds`.
  - Fails closed: any error returns `McpStatus(False, reason)`. A missing `sec_edgar_mcp` module logs an install hint once.
- **`load_mcp_tools`**: allowlist filter, then `_wrap_tool` with one shared `ToolCallBudget(max_sec_calls)`.
- **`_wrap_tool`**:
  - Calls the raw tool with a timeout.
  - Tries `_enhanced_sec_source_registration` first. Each structured source becomes its own `<untrusted_content>` block, so the model cites the exact metric or filing.
  - Otherwise falls back to `_record_sec_sources`.
  - The budget lives in `metadata["sec_budget"]`; when exhausted it returns `SEC_BUDGET_REACHED`.
- **`_unwrap_mcp_payload`**: `ast.literal_eval` of the adapter's repr, then `json.loads` of each `text` block. Falls back to a regex plus un-escaping.
- **`_enhanced_sec_source_registration`**:
  - **metrics**: one source per XBRL metric. URL `https://www.sec.gov/edgar/browse/?CIK=…&metric=…&period=…&form=…`. Content `"<Name>: <value> <unit>; period=…; form=…; fiscal_year=…; fiscal_period=… (Q2 fiscal 2027, Q2 FY2027)."`. Also sets `published_date=period`, `form_type`, `fiscal_period` and `date_source="sec"`.
  - **filings**: only 10-K, 10-Q and 8-K, at most 8 (`_MAX_FILING_SOURCES`). URL `/Archives/edgar/data/<cik>/<accession>/`, `published_date=filing_date`.
  - **company profile**: URL `cgi-bin/browse-edgar?action=getcompany&CIK=…`, content `"SEC company profile: k=v; …"`.
- **`prefetch_sec_tools(tools, registry, ticker)`**:
  - Calls `get_company_info`, `get_company_facts` and `get_recent_filings(days=365, limit=20)`.
  - Refunds those calls to the SEC budget (`ToolCallBudget.refund`).
  - Returns a text block (sections truncated to 1,200 / 3,000 / 2,000 characters) that is prepended to the agent's request.
- **[UNUSED]** `_parse_sec_sources` is defined but never called.

### Source registry (`sources/registry.py`)
- **`SourceRegistry(freshness_window_days=365)`**:
  - `record(url, title, kind, *, published_date, content, form_type, fiscal_period, date_source) -> Source`.
  - `contains(url)`, `all()`.
- **`normalize_url`**: lowercases the scheme and host, strips the trailing slash and the fragment, drops `utm_*`, `fbclid`, `gclid`, `dclid`, `mc_cid` and `mc_eid`, and sorts query parameters.
- **Dedup and merge** on the normalized URL:
  - A higher `_KIND_RANK` (search_result < web_page < sec / sec_filing) upgrades `kind` and `title`.
  - Missing date, form and period values are filled in.
  - **New content that isn't already contained is appended** (capped at the last 20,000 characters).
- **IDs** are sequential `S{n}` in first-seen order.
- **Known bug**: when a later record adds a date, `updates["freshness_status"]` is computed **after** `model_copy`, so freshness is never updated for merged records (`record`, ~L108–113).

### Date resolution and freshness (`sources/registry.py`, `checks/citations.py`)
- **Date resolution order**:
  1. The explicit `published_date`: Tavily's `published_date`, the SEC period or filing date.
  2. Otherwise `_date_from_url` parses `/YYYY/MM/DD/` from the URL path (`date_source="url"`).
  3. Otherwise undated.
- `_coerce_date` accepts dates, datetimes and several string formats.
- `date_source` is one of `sec`, `tavily`, `url`, `page_meta` or `none`. **`page_meta` is [UNUSED]**: nothing produces it.
- **Freshness** (`_freshness_for`): within `freshness_window_days` (365) is `fresh`, older is `stale`, no date is `undated`. **`Settings.freshness_window_days` is not passed to `SourceRegistry()`** (`run.py` L514), so the window is always the hard-coded 365 [PARTIAL].
- **Flag**: `_stale_or_undated_flag` adds "Claim uses stale or undated source(s)…" for recency-sensitive claims. A claim counts as recency-sensitive when its text matches tokens like quarter, fiscal, revenue, q1–q4 or 202 (`_recency_sensitive`).

### Citations and validation (`checks/citations.py`)
- **`check_citations(draft, registry) -> CitationReport`**, plus **`to_account_record(draft, report, registry, *, model, flags)`**.
- Details in §10.
- **`CitationReport`** fields: `passed_claims`, `failed_claims`, `valid_source_ids`, `cited_source_ids`, `repaired_claims`.

### SEC metrics (`sources/sec_metrics.py`)
- `parse_sec_metric(source) -> SecMetric | None`.
- `format_amount(value, unit)`, e.g. `$8.69B`, `-$1.40/share`.
- `add_sec_highlights(draft, sources)`: adds at most 4 claims from the latest-period 10-Q/10-K metrics, in the order Revenues, NetIncomeLoss, OperatingIncomeLoss, EPS diluted (else basic), Cash, Assets, Liabilities. Metrics already cited are skipped.
- Each added claim cites its metric source with `evidence_quote = "<Name>: <value> <unit>"`.

### Schemas (`schemas/account.py`)
- See §9.

### Models factory (`models/factory.py::get_chat_model(model_string, settings, *, http_client, rate_limit_state) -> BaseChatModel`)
- See §12.

### Rate limiting (`models/ratelimit.py`)
- **`RateLimitState`**:
  - Stores `x-ratelimit-*` and `retry-after` headers. `capture_response` is an httpx response hook for Groq and OpenAI.
  - `classify(error)` maps an error to a quota bucket; `reset_for(limit)` gives the bucket's reset time.
  - Exposes `remaining_tokens`, `remaining_requests()`, `remaining_output_tokens()` and `output_tpm`.
  - `wait()` enforces the run-wide wait cap (`RateLimitWaitExceeded`).
- **`RateLimitMiddleware.wrap_model_call`**: details in §11.
- **Exceptions**: `ModelUnavailableError`, `RateLimitWaitExceeded`, `DailyQuotaExceeded`.
- **[UNUSED]** the `default_wait_seconds` argument and `Settings.rate_limit_default_wait_seconds` are stored but never read.
- `RateLimitState.wait` `print`s to stdout.

### Model budgets (`models/budget.py`)
- `estimate_request_tokens(request)`: serialized JSON of system, messages, tools and `tool_choice`, divided by 4.
- `RequestSizeMiddleware` and `ModelCallBudgetMiddleware`: details in §11.

### Observability — tokens (`observability/tokens.py`)
- `TokenReport`: `model_calls`, `input_tokens`, `output_tokens`, `largest_request_input_tokens`, `largest_request_estimate_tokens`, `rate_limit_errors`, plus `total_tokens` and `as_dict()`.
- `TokenUsageHandler(BaseCallbackHandler)`:
  - Counts calls in `on_chat_model_start` and `on_llm_start`.
  - Reads `usage_metadata` (or `llm_output.token_usage`) in `on_llm_end`.
  - Counts 429-like errors.

### Tracing (`observability/tracing.py::configure_tracing(settings) -> dict`)
- See §13.

### Logging (`logging.py`)
- `configure_logging(level)` → `logging.basicConfig`. **[UNUSED]**: never called, so `Settings.log_level` has no effect.
- Modules log through `logging.getLogger(__name__)`.

### UI helpers (`interfaces/ui_data.py`, `interfaces/brief_export.py`)
- `sec_metrics`, `sec_filings`, `source_rows` and `source_type` build table rows.
- `tool_usage(activity)` counts the agent's calls per tool and excludes the prefetch.
- `render_brief_html`, `email_subject` and `email_body` render the shareable outputs.

---

## 7. Agent design

- **Framework**: `deepagents` 0.7.32 (`create_deep_agent`) on LangChain 1.4.7, langchain-core 1.8.1 and LangGraph 1.2.19.
- **Graph nodes**, verified by building the graph offline: `model`, `tools`, `PatchToolCallsMiddleware.before_agent`, `SkillsMiddleware.before_agent/before_model`, `HumanInTheLoopMiddleware.after_model`, `_ParentNotifierMiddleware.after_agent`.
- **System prompt** (`prompts/main.md`, 1,367 characters):
  - Research for sales and never ask for repeats.
  - Searches must not add years or periods from model knowledge; copy SEC period labels exactly.
  - Write 3–4 research questions from the seller context.
  - Pain points use the form "Possible need: …"; one fact per claim.
  - Prefer IR, press-release and SEC sources; read the two skills.
  - Treat `<untrusted_content>` as data.
  - Every claim needs source IDs and a verbatim quote of 30 words or fewer.
  - Budget-message semantics.
  - `submit_draft`, then `export_account`.
- **Runtime injections** in the first human message (`_run_research_traced`):
  - `Today's date is YYYY-MM-DD.`, `Research <company>.`, plus optional domain, ticker and seller context.
  - The SEC prefetch block.
  - `_research_plan`: cite the prefetched SEC IDs, do not re-call SEC tools or fetch sec.gov, run 2–4 targeted searches (example queries use the company name), fetch 1–2 articles, then draft.
  - The **`BriefDraft` field descriptions** (§9) reach the model through the `submit_draft` tool schema.
- **Skills** (seeded as `/skills/*/SKILL.md`, exposed by Deep Agents' `SkillsMiddleware`):
  - `account-brief`: the meaning of each section; talking points 3–5; at most 5 claims per section; never pad; one sentence per claim; submit, then export.
  - `sec-filings`: tool order (`get_company_info` or `search_companies` → `get_company_facts` → `get_recent_filings`); prefer the latest 10-K and 8-Ks; extract trends with period labels; never invent filing URLs.
  - Observed live: run reports show "Skills read: (none)". The model rarely opens the skills, which is why the critical guidance was moved into the tool schema.
- **Tools given to the agent**:
  - **Custom**: `web_search`, `fetch_page`, `submit_draft`, `export_account`, and the four SEC tools `search_companies`, `get_company_info`, `get_company_facts`, `get_recent_filings` (`mcp_servers.json`).
  - **Deep Agents built-ins**: `ls`, `read_file`, `write_file`, `edit_file`, `delete`, `glob`, `grep`, `execute`, `task`. These all operate on the in-state virtual filesystem (`StateBackend`). `execute` has no sandbox backend [UNVERIFIED behavior]. `task` exists although the general-purpose subagent is disabled.
  - **Restrictions**: the prompt forbids citing `read_file` or local paths. Citations can only resolve to registry IDs (`check_citations`).
- **Middleware**: see §11. The built-in `HumanInTheLoopMiddleware` implements `interrupt_on`.
- **Interrupts**: `interrupt_on={"export_account": True}`. A resume decision is `{"type":"approve"}` or `{"type":"reject","message":…}`.
- **Checkpointer and threads**: `MemorySaver()`, with one `thread_id = uuid4()` per model attempt.
- **Recursion limit**: `settings.recursion_limit` (50), set in `invoke_config`.

---

## 8. Data sources and retrieval

### SEC EDGAR via MCP
- **Server config** (`mcp_servers.json`): `sec_edgar`, enabled, stdio, `python -m sec_edgar_mcp.server`, environment `SEC_EDGAR_USER_AGENT`. Package `sec-edgar-mcp` 1.1.0, installed through the `edgar` extra.
- **Allowlist**: `search_companies`, `get_company_info`, `get_company_facts`, `get_recent_filings`. ADR 0006 records that the server exposes 21 tools.
- **Prefetch**: when a ticker is given, three calls run before the agent starts (§6). Their calls are refunded, so the agent keeps its full `max_sec_calls` (4).
- **Parsing and registration**: per source as described in §6. Fields set on each source are `url`, `title`, `kind="sec"`, `content`, `published_date`, `form_type`, `fiscal_period` and `date_source="sec"`.
- **Model-facing output**: one `<untrusted_content source_id title url>` block per structured source, truncated to `mcp_result_chars` (3,000).

### Web (Tavily, `tavily-python` 0.8.5)
- **Search parameters**: see §6. No `include_domains` or `exclude_domains`. Default `max_results=5`, snippets truncated to 300 characters.
- **Extraction**: `extract_depth="basic"`, `format="text"`. Navigation lines are stripped, then the text is truncated to 3,000 characters.
- **Untrusted wrapping**: `wrap_untrusted` HTML-escapes the ID, title, URL and text inside `<untrusted_content …>` tags.

### Source registry
- **Data model**: `Source` (§9). Dedup, ID assignment and merge behavior are described in §6.
- **Stored fields**: id, url, title, retrieved_at, kind, published_date, freshness_status, content, form_type, fiscal_period, date_source.

---

## 9. Data models (`schemas/account.py`, `config.py`)

**`Claim`**

| Field | Type | Rules / description (sent to the model) |
|---|---|---|
| `text` | `str` | "One sentence, one fact or idea." |
| `source_ids` | `list[SourceId]` | `min_length=1`; each item matches `^S\d+$` |
| `evidence_quote` | `str` | default `""`, `max_length=220`; "Exact words copied from the cited source text (5-30 words)… For SEC metrics, the metric line." |

**`Source`**: `id: SourceId`; `url: HttpUrl`; `title: str`; `retrieved_at: datetime`;
`kind: Literal["search_result","web_page","sec_filing","sec"]`; `published_date: date|None`;
`freshness_status: Literal["fresh","stale","undated"]="undated"`; `content: str=""`;
`form_type: str|None`; `fiscal_period: str|None`;
`date_source: Literal["sec","tavily","url","page_meta","none"]="none"`. Property `freshness`.

**`BriefDraft`** (model-authored; the `submit_draft` argument schema):
`company_name: str`, `domain`, `industry: str|None`, `is_public: bool|None`,
`summary: Claim` (**required**, "Two-sentence company snapshot."), plus these lists of `Claim`:

| Section | Description sent to the model |
|---|---|
| `financial_highlights` | Reported results with period labels |
| `signals` | 2–4 events from the last 12 months |
| `priorities` | 2–4 stated leadership goals, quoting what was said |
| `pain_points` | 2–3 "Possible need: …" hypotheses quoting the supporting fact |
| `talking_points` | 3–5 openers linking a quoted fact to the seller context |

**`AccountRecord`** (code-assembled): the `BriefDraft` fields with `summary: Claim|None`, plus
`sources: list[Source]` (cited sources only), `flags: list[str]`, `generated_at: datetime`,
`model: str`.

**Other dataclasses**: `CitationReport`, `FailedClaim(section, index, claim, reason)`,
`DraftSubmission`, `ResearchRun`, `PendingApproval`, `_RunContext`, `TokenReport`,
`ExportDiagnostics`, `McpStatus(available, reason)`, `ToolCallBudget(maximum)`, `SecMetric`,
`MCPServerConfig` / `MCPConfig` (pydantic, `extra="forbid"`; `MCPServerConfig.enabled` defaults to
`False`), and `SkillMetadata` (TypedDict).

**Settings**: see §14.

> Discrepancy: `docs/account_record.schema.json` (generated by `make schema`) predates
> `evidence_quote` and the field descriptions. Regenerate it with `make schema`.

---

## 10. Validation and controls

### 10.1 Claim validation pipeline (`check_citations`, in order)

1. **Has source IDs**: otherwise `no source_ids`.
2. **IDs exist in the registry**: otherwise `unknown source ID(s): …`.
3. **Market-data rule**: claims mentioning market cap or stock/share price must cite dated sources (`_claim_support_reason`).
4. **Numeric values** (`_claim_values`, `_source_values`, `_value_supported`):
   - Values parsed from the claim: `$` amounts with units (k, m, b, t and the spelled-out forms) and percentages.
   - Values parsed from the source: any number, with ISO dates removed first.
   - A value matches when it is within the claim's rounding tolerance (`0.5×10^-decimals×scale`). Sign is ignored, so "net loss $487.3M" matches `-487291000`.
   - Otherwise: `value mismatch: missing value(s) in source text: …`.
5. **Fiscal periods** (`_missing_periods`): the claim's quarters (`Q1`–`Q4`) and fiscal years (`FY27`, `fiscal 2027`, `fiscal_year=2027`) must each appear in the source; they are checked separately.
6. **Evidence quote** (`_quote_found`):
   - Normalizes curly quotes, dashes, case and whitespace.
   - Splits the quote on ellipses (`...`, `…`); the fragments must appear in order.
   - At least 3 words in total; an empty quote passes.
   - **SEC exception**: when all cited sources are SEC and the claim text, or the quote's values, verify numerically, no quote match is needed.
7. **Quote repair** (`_repair_quote`):
   - When the quote isn't found, accept the claim only if one source sentence contains at least 75% of the quote's content words (at least 4 of them) and every number in the quote.
   - The claim then carries that real sentence as its `evidence_quote` (`repaired_claims`).
   - Validated on 16 real rejected Snowflake claims: 4 repaired, 8 still rejected (`tests/test_replay_snowflake.py`).

**Repair cycle** (`submit_draft`): the first failing submission returns the reasons, the closest
real sentence for each failed quote, and the valid IDs. The second submission is accepted with
failures dropped. **Drop behavior** (`to_account_record`): failed claims are removed; a failed
summary becomes `None`; each drop adds a flag `Dropped <section> claim: … (<reason>)`; only cited
sources are attached; freshness flags are deduplicated.

**Duplicate detection**: duplicate *sources* are merged by normalized URL. Duplicate *claims* are
**not** detected in code; "do not restate facts" is prompt-only.

### 10.2 Controls table

| Control | What it prevents | Mechanism | Enforced in | File | Known gaps |
|---|---|---|---|---|---|
| Source-ID check | Uncited or invented citations | IDs must exist in registry | Code | `checks/citations.py` | — |
| Numeric match | Wrong or hallucinated figures | Rounding-tolerant numeric compare | Code | `checks/citations.py` | Only `$` amounts and % in claims; bare numbers ("692 customers") not checked |
| Period match | Wrong quarter/year | Quarter and FY tokens must appear in source | Code | `checks/citations.py` | Calendar years and months not checked |
| Evidence quote | Unsupported claims | Ellipsis-aware substring | Code | `checks/citations.py` | Quote may support less than the claim text says |
| Quote repair | False rejections of paraphrases | ≥75% content-word overlap + numbers | Code | `checks/citations.py` | Fuzzy; could accept a quote whose sentence doesn't support the claim's framing |
| SEC highlights | Briefs missing reported financials | Deterministic claims from XBRL | Code | `sources/sec_metrics.py` | Latest period only; max 4 metrics |
| Empty-section request | Empty priorities/pain/talking sections | One returned submission with instructions | Code (trigger) + LLM (content) | `agent/submission.py` | Only with seller context; model may still leave them empty |
| Freshness flags | Stale data presented as current | Date + 365-day window + recency keywords | Code | `sources/registry.py`, `checks/citations.py` | Window setting not wired; merged-record freshness bug |
| Market-data dating | Undated price/market-cap claims | Requires dated sources | Code | `checks/citations.py` | Keyword-based |
| Untrusted wrapping | Prompt injection via sources | `<untrusted_content>` + prompt rule | Code (wrap) + prompt (obey) | `tools/_text.py`, `prompts/main.md` | Model compliance not guaranteed |
| SSRF guard | Fetching internal hosts | DNS → `is_global` check | Code | `tools/fetch_page.py` | DNS rebinding between check and Tavily fetch (Tavily fetches, not the app) |
| MCP allowlist | Exposing risky SEC tools | Deny-by-default filter | Code | `mcp/allowlist.py` | — |
| Approval gate | Unreviewed output on disk | LangGraph interrupt before `export_account` | Code | `agent/builder.py`, `agent/run.py` | UI pause lost on reload |
| Zero-arg export | Model writing arbitrary content | `_NoArguments`, writes stored record only | Code | `tools/export_account.py` | — |
| Seller-context separation | Seller claims presented as facts | Prompt rule | Prompt only | `prompts/main.md` | Not checked in code |
| One fact per claim / no restating | Padding, duplicates | Prompt + schema text | Prompt only | `prompts/main.md`, `schemas/account.py` | Not checked in code |

---

## 11. Budgets, limits and reliability

| Budget | Default | Enforcement | When exceeded |
|---|---|---|---|
| Model calls | `max_model_calls=15` | `ModelCallBudgetMiddleware` | Appends `FINAL_DRAFT_INSTRUCTION` once and requires `submit_draft`. Allows one repair after resubmission and 4 follow-up calls after submission. Otherwise `ModelCallBudgetExceeded`. |
| Web searches | `search_budget=8` | `ToolCallBudget` in `web_search` | Returns `BUDGET_REACHED` ("Draft and call submit_draft now."). |
| Page fetches | `max_page_fetches=4` | `ToolCallBudget` in `fetch_page` | `PAGE_BUDGET_REACHED` (continue with search results). |
| Agent SEC calls | `max_sec_calls=4` (prefetch refunded) | Shared `ToolCallBudget` | `SEC_BUDGET_REACHED` (use web_search). |
| Request size | 6,500 (Groq) / 32,000 (others) estimated tokens | `RequestSizeMiddleware` | Trims older tool results at 500 → 250 → 100 → 0 characters, preserving source IDs. If still over: `ValueError("Model request estimate … exceeds max_request_tokens …")`. |
| Conversation length | trigger `max(4800, 0.75×cap)` | `SummarizationMiddleware` | Summarizes older turns and keeps the last 8 messages. |
| Output per call | `max_output_tokens=1500` (`anthropic_max_tokens=16000` for Claude) | Model params | Truncated output. |
| Recursion | `recursion_limit=50` | LangGraph | `GraphRecursionError` → `ResearchRunError`. |
| Agent stops early | — | `_drive` | One "draft now / export now" nudge, then error. |

**Rate-limit handling** (`RateLimitMiddleware.wrap_model_call`):
- **Before each call**, it estimates request tokens and checks the remembered headers:
  - Remaining daily tokens = 0 → `DailyQuotaExceeded`.
  - Remaining requests = 0, too few tokens, or too few output tokens → wait for the reset (or exponential backoff 2 s → 15 s cap), bounded by `max_rate_limit_waits` (6) and `max_total_wait_seconds` (300).
- **On an exception**:
  - Billing codes (`insufficient_quota`, `credit_balance_exhausted`, `billing_hard_limit`, `billing_error`, "credit balance is too low") → `ModelUnavailableError` immediately (no retry).
  - 429 → classify, wait `retry-after`, the reset or backoff, then retry the same call. A daily limit → `DailyQuotaExceeded`.
  - 404, "model not found" or "decommissioned" → `ModelUnavailableError`.
  - 5xx → up to 3 attempts with a 1 s sleep, then `ModelUnavailableError`.
- **Header coverage**: only `x-ratelimit-*` and `retry-after` are parsed, and capture is wired through an httpx hook for Groq and OpenAI only. **Anthropic (`anthropic-ratelimit-*`) headers are not captured**, so pre-call throttling is inactive for Claude; the Anthropic SDK's own retries (`max_retries`) handle 429s [PARTIAL].

**Model fallback** (`_run_research_traced`): triggered only by `ModelUnavailableError` from the
first model. The next model is used only if `rate_state.output_tpm ≥ min_output_tpm` (3000).
Otherwise the run raises "Fallback … skipped …" with the primary's error. The default
`fallback_model` is `None`.
- [PARTIAL] `output_tpm` comes from `x-ratelimit-limit-output-tokens`, which Anthropic and OpenAI do not send, so fallback is effectively Groq-only.

**Graceful degradation**
- **MCP**: unavailable MCP → `McpStatus(False)`; research continues web-only with the flag "SEC filings unavailable".
- **Tool calls**: SEC calls have per-call timeouts; tool exceptions surface to the agent as tool errors [UNVERIFIED].
- **Prefetch**: a failed call is recorded as `"error: …"` text.
- **Run failures**: any error is wrapped as `ResearchRunError` with diagnostics. The UI shows "Research failed".

---

## 12. Model layer (`models/factory.py`)

| Provider | Model string | Class | Parameter handling |
|---|---|---|---|
| **anthropic** (default) | `anthropic:claude-opus-5-5` | `ChatAnthropic` (langchain-anthropic 1.7.7, anthropic 2.3.0) | No temperature. `thinking={"type":"adaptive","block_binding":{"prefix_mismatch_behavior":"drop_block"}}` (needed because trimming and summarization edit history). `output_config={"effort": anthropic_effort}`. `max_tokens=16000`. Betas `thinking-binding-controls-2026-08-01` and, except for Haiku, `server-side-fallback-2026-07-01` + `fallbacks:"default"`. SDK `max_retries`. |
| groq | `groq:openai/gpt-oss-120b`, … | `ChatGroq` | `max_tokens=max_output_tokens`. httpx hook captures rate-limit headers. `max_retries=0` when middleware handles retries. |
| openai | `openai:gpt-4o-mini`, … | `ChatOpenAI` | `temperature=0.0` (dropped if rejected). Header hook. |
| ollama | `ollama:gpt-oss:20b` | `ChatOllama` (`local` extra) | `num_predict`, timeouts. |
| gemini | `gemini:gemini-3.6-flash` | `ChatGoogleGenerativeAI` (`gemini` extra) | `max_output_tokens`, retries. |

- **Switching**: set `ARA_MODEL=<provider>:<model>` plus the provider key in `.env`, or pass `ara research --model`. The UI reads `ARA_MODEL` at start-up.
- **Default history**: Groq GPT-OSS-120B (ADR 0004), then `openai:gpt-4o-mini` in the uncommitted tree, now `anthropic:claude-opus-5-5`.
- **[UNVERIFIED]** The Anthropic path has been exercised only offline: the request payload test and a full agent loop against a fake client in `tests/test_agent_run.py::test_anthropic_model_runs_agent_loop_offline`. No live Claude run has been performed.

---

## 13. Observability

- **Token and usage tracking**:
  - `TokenUsageHandler` is passed as the only callback in `invoke_config["callbacks"]` (`run.py` ~L583).
  - `RequestSizeMiddleware` records `largest_request_estimate_tokens`.
  - Totals appear in `run-report.md`, the CLI output, the UI Activity tab and `ResearchRunError`.
- **LangSmith** (`configure_tracing`, `tracing_backend="langsmith"` default):
  - **Enabling**: sets `LANGSMITH_TRACING`, `LANGCHAIN_TRACING_V2`, the project (`LANGSMITH_PROJECT` env, else `langsmith_project`) and the key (env, else `.env`). It then clears `langsmith.utils.get_env_var`'s `lru_cache` (`_reset_langsmith_env_cache`) so late-set environment variables take effect.
  - **Enabled before the traced span**: `run_research` and `start_research` call `configure_tracing` first, then the `@traceable` `_run_research_traced`. This fixes an earlier bug where the cached "off" state disabled tracing.
  - **Trace structure**:
    - Parent run `account_research_run`, tag `account-research-agent`.
    - Child `sec_prefetch` (`@traceable`).
    - Automatic LangChain/LangGraph child runs for each model and tool call.
    - **Resume calls** (`approve` / `reject`) run outside the parent span and create separate top-level traces [PARTIAL].
  - **Trace URL**: `https://smith.langchain.com/o/default/projects/<project>` (a generic project link, not a run link).
- **Langfuse** [PARTIAL]:
  - `configure_tracing` builds `Langfuse(...)` and a `CallbackHandler()` when the keys are present and the `tracing` extra is installed.
  - **The callback is never added to `invoke_config["callbacks"]`**, so no Langfuse traces are produced.
- **Logging**: module loggers only. `configure_logging` is never called [UNUSED]. Rate-limit waits `print` to stdout.

---

## 14. Configuration

All settings use the `ARA_` prefix, except the keys marked "(env)", and load from `.env`
(`Settings`).

| Setting | Env var | Default | Purpose | Req. |
|---|---|---|---|---|
| model | `ARA_MODEL` | `anthropic:claude-opus-5-5` | Primary model | — |
| fallback_model | `ARA_FALLBACK_MODEL` | `None` | Fallback for unavailable model | opt |
| anthropic_effort | `ARA_ANTHROPIC_EFFORT` | `medium` | Claude effort (low…max) | opt |
| anthropic_max_tokens | `ARA_ANTHROPIC_MAX_TOKENS` | 16000 | Claude output + thinking cap | opt |
| max_model_calls | `ARA_MAX_MODEL_CALLS` | 15 | Model-call budget | opt |
| max_request_tokens | `ARA_MAX_REQUEST_TOKENS` | `None` → 6,500 Groq / 32,000 other | Request-size cap | opt |
| search_budget | `ARA_SEARCH_BUDGET` | 8 (1–25) | Web searches per run | opt |
| max_page_fetches | `ARA_MAX_PAGE_FETCHES` | 4 | Page extractions | opt |
| max_sec_calls | `ARA_MAX_SEC_CALLS` | 4 | Agent SEC calls | opt |
| max_search_results | `ARA_MAX_SEARCH_RESULTS` | 5 (1–20) | Tavily results per search | opt |
| snippet_chars | `ARA_SNIPPET_CHARS` | 300 | Search snippet length | opt |
| max_page_chars | `ARA_MAX_PAGE_CHARS` | 3000 | Page text cap | opt |
| mcp_result_chars | `ARA_MCP_RESULT_CHARS` | 3000 | MCP output cap | opt |
| freshness_window_days | `ARA_FRESHNESS_WINDOW_DAYS` | 365 | Fresh/stale window — **not wired** [UNUSED] | opt |
| default_search_time_range | `ARA_DEFAULT_SEARCH_TIME_RANGE` | `year` | Tavily time range | opt |
| default_search_topic | `ARA_DEFAULT_SEARCH_TOPIC` | `general` | Tavily topic | opt |
| recursion_limit | `ARA_RECURSION_LIMIT` | 50 | LangGraph limit | opt |
| request_timeout_seconds | `ARA_REQUEST_TIMEOUT_SECONDS` | 60 | Model/Tavily timeout | opt |
| max_retries | `ARA_MAX_RETRIES` | 3 | SDK retries | opt |
| max_rate_limit_waits | `ARA_MAX_RATE_LIMIT_WAITS` | 6 | Waits per call | opt |
| max_total_wait_seconds | `ARA_MAX_TOTAL_WAIT_SECONDS` | 300 | Run-wide wait cap | opt |
| rate_limit_default_wait_seconds | `ARA_RATE_LIMIT_DEFAULT_WAIT_SECONDS` | 60 | **[UNUSED]** | opt |
| rate_limit_backoff_base_seconds | `ARA_RATE_LIMIT_BACKOFF_BASE_SECONDS` | 2 | Backoff base | opt |
| rate_limit_backoff_cap_seconds | `ARA_RATE_LIMIT_BACKOFF_CAP_SECONDS` | 15 | Backoff cap | opt |
| tracing_backend | `ARA_TRACING_BACKEND` | `langsmith` | langsmith / langfuse / none | opt |
| langsmith_project | `ARA_LANGSMITH_PROJECT` | `account-research-demo` | Project name | opt |
| langfuse_host | `ARA_LANGFUSE_HOST` | cloud.langfuse.com | Langfuse host | opt |
| min_output_tpm | `ARA_MIN_OUTPUT_TPM` | 3000 | Fallback eligibility | opt |
| max_output_tokens | `ARA_MAX_OUTPUT_TOKENS` | 1500 | Non-Claude output cap | opt |
| output_dir | `ARA_OUTPUT_DIR` | `output` | Export folder | opt |
| skills_dir | `ARA_SKILLS_DIR` | `skills` | Skills root | opt |
| mcp_config_path | `ARA_MCP_CONFIG_PATH` | `mcp_servers.json` | MCP config | opt |
| mcp_enabled | `ARA_MCP_ENABLED` | `True` | Enable SEC MCP | opt |
| mcp_timeout_seconds | `ARA_MCP_TIMEOUT_SECONDS` | 30 | MCP start/call timeout | opt |
| log_level | `ARA_LOG_LEVEL` | `INFO` | **[UNUSED]** | opt |
| anthropic_api_key | `ANTHROPIC_API_KEY` (env) | — | Claude | **req** for `anthropic:` |
| openai_api_key | `OPENAI_API_KEY` (env) | — | OpenAI | req for `openai:` |
| groq_api_key | `GROQ_API_KEY` (env) | — | Groq | req for `groq:` |
| google_api_key | `GOOGLE_API_KEY` (env) | — | Gemini | req for `gemini:` |
| tavily_api_key | `TAVILY_API_KEY` (env) | — | Web search/extract | **req** (research) |
| sec_edgar_user_agent | `SEC_EDGAR_USER_AGENT` (env) | — | SEC fair-access UA | req if SEC MCP enabled (`doctor`) |
| langsmith_api_key | `LANGSMITH_API_KEY` (env) | — | LangSmith | opt |
| langfuse_public_key / secret_key | `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` (env) | — | Langfuse | opt |

- **`.env.example`** lists the `ARA_*` overrides plus the provider, Tavily, SEC and LangSmith keys.
- **Optional extras** (`pyproject.toml`):

| Extra | Packages |
|---|---|
| `edgar` | `sec-edgar-mcp` |
| `local` | `langchain-ollama` |
| `gemini` | `langchain-google-genai` |
| `tracing` | `langsmith`, `langfuse` |

- `langchain-anthropic` is **not declared**. It is installed only transitively, through `deepagents` [PARTIAL]; see §18.

---

## 15. Technology stack

| Technology | Version (installed) | Purpose | Where used | Status |
|---|---|---|---|---|
| Python | 3.14.6 (local); CI 3.11/3.12; `requires-python >=3.11` | Runtime | — | in use |
| deepagents | 0.7.32 | Agent harness, skills, HITL, virtual FS | `agent/builder.py`, `spike/run.py` | in use |
| LangGraph | 1.2.19 | Graph, interrupts, `MemorySaver`, `Command` | `agent/run.py`, `builder.py` | in use |
| LangChain / core | 1.4.7 / 1.8.1 | Middleware API, tools, callbacks | throughout | in use |
| langchain-anthropic / anthropic | 1.7.7 / 2.3.0 | Claude provider (default) | `models/factory.py` | in use [UNVERIFIED live] |
| langchain-groq | 1.1.3 | Groq provider | factory | in use (prior default) |
| langchain-openai | 1.8.0 | OpenAI provider | factory | in use (no current key) |
| langchain-google-genai | extra | Gemini | factory | partial (quota-blocked per ADR 0004) |
| langchain-ollama | extra | Local models | factory | unverified |
| langchain-mcp-adapters / mcp | 0.3.2 / 1.30.0 | MCP client | `mcp/client.py` | in use |
| sec-edgar-mcp | 1.1.0 (extra) | SEC EDGAR server (stdio) | `mcp_servers.json` | in use |
| tavily-python | 0.8.5 | Search + extract | `tools/` | in use |
| pydantic / pydantic-settings | 2.14.2 / 2.16.2 | Schemas, settings | `schemas/`, `config.py` | in use |
| Streamlit | 1.67.0 | Demo UI | `app.py` | in use |
| Typer / Rich | 0.27.3 / 15.0.0 | CLI | `interfaces/cli.py` | in use |
| PyYAML | 6.0.3 | Skill frontmatter | `skills/loader.py` | in use |
| LangSmith | 0.14.16 | Tracing | `observability/tracing.py`, `agent/run.py` | in use (key currently rejected) |
| Langfuse | extra | Tracing | `tracing.py` | partial (callback not attached) |
| httpx | 0.28.1 | Header capture hook | factory | in use |
| python-dotenv | declared | — | not imported in `src/` | unused |
| pytest, ruff, mypy, pre-commit | dev group | QA | CI, Makefile | in use |

---

## 16. Testing

**Result**: `121 passed, 13 warnings` (all offline; no network). `tests/conftest.py` forces
`ARA_TRACING_BACKEND=none` and the `LANGSMITH_TRACING*` variables to false, so tests never send
traces.

| Area | Tests | Notes (mocked vs. live) |
|---|---|---|
| Citation validation | `test_citations.py` (15), `test_replay_snowflake.py` (2) | Values, periods, quotes, ellipses, repair, repair hints, empty-section request; **replay of 16 real rejected live claims** (`fixtures/snowflake_rejected_quotes.json`) |
| Research tools + MCP | `test_research_tools.py` (13) | Fake Tavily, fake DNS, fake MCP tools; SEC repr payload parsing; SSRF; sec.gov refusal; nav stripping; registry merge |
| Model factory | `test_model_factory.py` (13) | All providers mocked; **offline Anthropic payload checks** (no temperature, thinking/`drop_block`, effort, betas, Haiku no-fallback) |
| Agent run | `test_agent_run.py` (11) | `ScriptedToolChatModel` fake LLM; repair/drop; tokens; fallback; **pause → approve**, **reject → revision**; **full loop through real `ChatAnthropic` with a fake client** |
| Rate limiting | `test_ratelimit.py` (10) | Header parsing, waits, 429 retry, daily quota, **out-of-credit fail-fast** |
| Schemas | `test_schema.py` (9) | Validation rules |
| Spike | `test_spike.py` (6) | Scoring logic |
| Budgets | `test_model_budgets.py` (5) | Trimming, final-draft instruction |
| Config | `test_config.py` (5) | Defaults, env overrides, per-provider cap |
| UI data / export | `test_ui_data.py` (4), `test_brief_export.py` (2), `test_export_account.py` (4) | Row building, HTML escaping, email, atomic export |
| CLI | `test_cli.py` (4), `test_approval_flow.py` (3) | Typer runner, approval prompt |
| Misc | `test_submission.py` (3), `test_sec_metrics.py` (3), `test_skills_loader.py` (3), `test_prompts.py` (3), `test_mcp_config.py` (3) | |

**Notable gaps**
- **No live test** of any provider in CI. The default Claude path has never run live.
- `app.py` has no automated test. It was checked manually with `streamlit.testing.AppTest` scripts that are not committed.
- No test for the registry freshness-merge bug, Langfuse, or `execute` / `task` built-ins.
- CI runs `ruff format --check` and `mypy src/`, which the current tree does not fully pass: several modules have pre-existing E501 / I001 lint findings and a mypy `no-redef` error in `factory.py`.

---

## 17. Development history

`git log --oneline` shows 9 commits, all on 2026-11-01 and 2026-11-02, plus a large uncommitted
working tree.

| Commit | What changed | Why (evident from code/docs) |
|---|---|---|
| `e5b27ef` Milestone 1: project scaffold | 59 files: package layout, CLI, settings, schemas, ADRs | Initial structure |
| `2c86eeb` Implement account research agent milestones | 70 files: single agent, tools, registry, MCP, skills, spike | ADR 0005: moved from multi-agent to a single agent to cut token and orchestration overhead |
| `ee9820a` Approval-gated research and rate-limit controls | `interrupt_on` export, `RateLimitMiddleware`, `submit_draft` repair | ADR 0007: no unreviewed output; same-call 429 retry instead of restarting runs |
| `b463622` Document live verification and Gemini quota limits | docs | Gemini free-tier 429s |
| `e63e817` Add run budgets and trim oversized model requests | `ModelCallBudgetMiddleware`, `RequestSizeMiddleware`, tool budgets | Groq 8k TPM limit |
| `1724a3f` Prompt draft submission when an agent stops early | `_drive` final-instruction nudge | Agents ending without `submit_draft` |
| `79683db` Estimate serialized model payloads for request caps | `estimate_request_tokens` JSON-based | More accurate caps |
| `5288020` Record Groq daily quota and incomplete live verification | docs + `DailyQuotaExceeded` | Groq daily quota blocked live runs |
| `49f52a1` docs: verified offline status and quota blocker | docs | — |

**Uncommitted work** (this session, `git diff --stat`: 32 files, +3,240 / −251):
- **Default model and Streamlit UI**:
  - Default changed to OpenAI, then to Anthropic Claude Opus 5.5.
  - `app.py` UI: tabs, SEC financials, all sources, activity log, real approve/revise/discard, downloads, email copy.
- **Reliability fixes**:
  - Provider-aware request cap (6,500 vs 32,000).
  - LangSmith tracing enabled before the traced span, with the env-var cache cleared.
  - SEC MCP payload parsing fixed: previously none of the three calls registered a source.
  - Registry merges new content for known URLs.
  - SEC prefetch no longer spends the agent's SEC budget; tool-specific budget messages.
  - sec.gov fetch refusal and navigation-line stripping.
  - Out-of-credit fail-fast.
- **Validation**:
  - Rounding-aware numeric matching, separate quarter and FY checks, ellipsis-aware quotes.
  - SEC numeric verification, quote repair, closest-evidence hints.
  - Automatic SEC highlights.
  - Empty seller-section request; schema field descriptions.

---

## 18. Known limitations, technical debt and risks

- **Live verification**:
  - **The default Claude path is unverified live.** The OpenAI account ran out of credit and the LangSmith key is rejected (401/403), so no traces were confirmed.
  - **Model variance**: claim yield varied widely between identical gpt-4o-mini runs (2 of 6 to 7 of 8 claims kept).
- **Bugs**:
  - **Freshness bug**: `SourceRegistry.record` computes `freshness_status` after `model_copy`, so merged records keep stale freshness.
  - **Replay** prints `Claims passed=0` (`_replay_research`).
- **Settings and features that don't work**:
  - **Unwired settings**: `freshness_window_days`, `rate_limit_default_wait_seconds`, `log_level`.
  - **Unused code**: `_parse_sec_sources`, `configure_logging`, `page_meta` date source, `python-dotenv` dependency.
  - **Langfuse** produces no traces (callback not attached).
- **Provider gaps**:
  - Anthropic rate-limit headers are not parsed, so there is no pre-call throttling for Claude.
  - Fallback is effectively Groq-only (`output_tpm` header).
  - `langchain-anthropic` is not a declared dependency.
  - **Preserved thinking**: trimming and summarization edit history; `drop_block` avoids 400s but discards earlier reasoning (cost/quality impact [UNVERIFIED]).
- **UI state**: a paused run lives in memory (`MemorySaver` + Streamlit session), so it is lost on reload or restart, and there is no multi-user persistence.
- **Validation limits**: bare counts and calendar dates aren't value-checked; quote repair is fuzzy; claim duplication isn't detected.
- **Skills are rarely read**; critical guidance depends on the tool schema and the prompt.
- **Doc/code discrepancies**:
  - README, `docs/architecture.md`, ADR 0004 and ADR 0007 say Groq is the default; the code defaults to Anthropic.
  - ADR 0005 says five SEC tools are allowlisted; `mcp_servers.json` has four (ADR 0006 matches the code).
  - `docs/account_record.schema.json` is stale (no `evidence_quote`).
  - ADR 0005 mentions the multi-agent era.
- **CI health**: pre-existing lint (E501 / I001) and mypy findings would fail `make check`.
- **Security**: page content is wrapped as untrusted, but prompt-injection resistance depends on the model. Fetches go through Tavily, so the SSRF check protects only the URL the agent requests.

---

## 19. Extension points

| Extension | Where | How |
|---|---|---|
| **New data source (tool)** | `tools/` + `tools/factory.py::make_research_tools` | Write `make_<tool>(settings, registry) -> BaseTool`. Register every item with `registry.record(...)` (choose a `kind`; extend `Source.kind` and `_KIND_RANK` if new). Return `wrap_untrusted(source, text)`. Add a `ToolCallBudget` and a setting. Add the tool name to `_ACTIVITY_TOOLS` in `run.py` for the UI log. |
| **New MCP server/tools** | `mcp_servers.json` | Add a server with `allowed_tools`. Inspect it with `ara mcp-tools`. Add structured parsing in `_enhanced_sec_source_registration` (or a sibling) so each record becomes a citable source. |
| **New output target (CRM, Slack, email send)** | `tools/export_account.py` or post-approval in `app.py::_render_share` / `PendingApproval.approve` | Keep writes behind the approval interrupt: either add another zero-argument tool listed in `interrupt_on`, or run the integration in application code after `approve()` returns the validated `ResearchRun.record`. `interfaces/brief_export.py` already renders HTML and email text. |
| **New model provider** | `models/factory.py::get_chat_model` | Add a `provider` branch returning a `BaseChatModel`. Add the key to `Settings` and `redacted()`, and to `_check_runtime_keys` / `doctor` in the CLI. Add a cap rule in `request_token_cap` if needed. For header-based throttling, capture its rate-limit headers into `RateLimitState`. |
| **New validation rule** | `checks/citations.py::_claim_support_reason` (per claim) or `check_citations` | Return a reason string to fail the claim. It is then reported in `submit_draft` feedback, dropped by `to_account_record`, and shown in the UI Validation tab. Add replay cases to `tests/fixtures/` to measure false rejections. |
| **New brief section** | `schemas/account.py` (`BriefDraft`, `AccountRecord`), `checks/citations.py::_CLAIM_SECTIONS`, `render_brief`, `brief_export.SECTIONS`, `app.py::SECTIONS` | Add the field with a `description` (it reaches the model through the tool schema). |
| **Deterministic enrichment** | `agent/submission.py` (before `check_citations`) | Follow `add_sec_highlights`: build `Claim`s from registry data so they pass the same checks. |
