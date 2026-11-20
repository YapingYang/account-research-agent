# Demo Gap Assessment

## Verdict: Close

The repository is functionally close to a reliable live demo, but it is not yet a zero-risk, fully deterministic recorded demo without a preflight checklist and a few operational guardrails. The end-to-end path exists in code and the tests pass, but the live run still depends on external model providers, web search, SEC tooling, and the approval/resume loop being healthy at runtime.

## Scope reviewed

I reviewed the repo’s core execution path in:

- `src/account_research/config.py`
- `src/account_research/models/factory.py`
- `src/account_research/models/ratelimit.py`
- `src/account_research/agent/run.py`
- `src/account_research/interfaces/cli.py`
- `src/account_research/agent/builder.py`
- `src/account_research/agent/submission.py`
- `src/account_research/tools/export_account.py`
- `src/account_research/sources/registry.py`
- `src/account_research/checks/citations.py`
- `src/account_research/tools/web_search.py`
- `src/account_research/tools/fetch_page.py`
- `src/account_research/mcp/client.py`
- `src/account_research/schemas/account.py`

## Verification

I ran the project test suite:

```bash
uv run pytest -q
```

Result: 75 passed.

This confirms the code path is stable under the repo’s unit/integration tests, but it does not guarantee live-provider readiness.

## OpenAI support status

Current workspace status: supported.

The repo now supports `openai:<model-name>` via the provider:model pattern and reads `OPENAI_API_KEY` from env/.env. The relevant implementation points are:

- `Settings` in `src/account_research/config.py`
- `get_chat_model()` in `src/account_research/models/factory.py`
- `RateLimitState` / `RateLimitMiddleware` in `src/account_research/models/ratelimit.py`

If this repo were evaluated before the OpenAI patch, it would have been a blocker. In the current workspace, that blocker is resolved.

## Required env vars and defaults

These are the live-run requirements surfaced by the repo:

| Variable | Required for | Default in code | Notes |
|---|---|---:|---|
| `ARA_MODEL` | default model selection | `openai:gpt-4o-mini` | current default in `Settings` |
| `ARA_FALLBACK_MODEL` | fallback model | `groq:openai/gpt-oss-120b` | current default |
| `OPENAI_API_KEY` | OpenAI primary provider | none | required when `model` starts with `openai:` |
| `GROQ_API_KEY` | Groq fallback / Groq runs | none | required when model starts with `groq:` |
| `TAVILY_API_KEY` | web search and page fetch tools | none | required for research tools |
| `SEC_EDGAR_USER_AGENT` | SEC EDGAR MCP server | none | required when the SEC EDGAR MCP server is enabled |
| `GOOGLE_API_KEY` | Gemini support | none | optional provider, not required for this demo |
| `ARA_MCP_ENABLED` | enables SEC MCP path | `true` | default in config |
| `ARA_MCP_TIMEOUT_SECONDS` | MCP timeout | `30` | default |
| `ARA_SEARCH_BUDGET` | search cap | `4` | default |
| `ARA_MAX_PAGE_FETCHES` | page-fetch cap | `2` | default |
| `ARA_MAX_SEC_CALLS` | SEC calls cap | `4` | default |
| `ARA_MAX_MODEL_CALLS` | model-call cap | `15` | default |
| `ARA_MAX_REQUEST_TOKENS` | request-size ceiling | `6500` | default |
| `ARA_MAX_TOTAL_WAIT_SECONDS` | total wait budget | `300` | default |
| `ARA_MIN_OUTPUT_TPM` | output TPM check for model fallback | `3000` | default |
| `ARA_MAX_OUTPUT_TOKENS` | model output cap | `1500` | default |
| `ARA_OUTPUT_DIR` | export path | `output` | default |
| `ARA_SKILLS_DIR` | skills folder | `skills` | default |
| `ARA_MCP_CONFIG_PATH` | MCP config path | `mcp_servers.json` | default |
| `ARA_LOG_LEVEL` | logging | `INFO` | default |

Notes:

- The CLI also checks for `TAVILY_API_KEY` and `GROQ_API_KEY` / `OPENAI_API_KEY` depending on selected provider in `doctor()`.
- The SEC EDGAR server is enabled by default via `mcp_servers.json` and requires a valid user-agent string due to fair-access rules.

## End-to-end run path and live risk analysis

### What works today

The code path for a reliable run is present:

1. CLI receives `company`, `ticker`, `context`, optional model override.
2. `run_research()` builds the agent, loads MCP tools, creates a source registry, and wires the draft submission and export tools.
3. Agent tool loop runs research.
4. `submit_draft` validates each claim against `SourceRegistry` entries.
5. `export_account` writes `brief.md`, `account.json`, and `run-report.md` after approval.
6. `run_research()` captures telemetry: token usage, wait times, and headers.

The core control flow is implemented in:

- `src/account_research/agent/run.py`
- `src/account_research/agent/builder.py`
- `src/account_research/agent/submission.py`
- `src/account_research/tools/export_account.py`

### What is not a true offline/demo dry run

There is no production-grade replay/cached mode. The closest thing is the test-only spike and the tool diagnostics commands:

- `uv run ara tools-check ...`
- `uv run ara spike --models ...`

These are useful for tool health but they are not a deterministic “recorded demo” mode.

## Blockers (must fix for a live run), ranked

### 1) External provider availability and quota risk

- Symptom: model call fails with rate-limit or provider-side errors; one run stalls before a draft is produced.
- Root cause: `RateLimitMiddleware` + `run_research()` fallback logic in `src/account_research/models/ratelimit.py` and `src/account_research/agent/run.py`.
- Proposed fix: run a provider preflight before recording the demo, verify `OPENAI_API_KEY` and `GROQ_API_KEY`, and use a known-good model. Set a conservative fallback budget and pretest the exact model to be used. If provider quota is unstable, switch to a smaller demo company and shorter context.
- Estimated effort: low to medium (0.5–1 day).

### 2) No deterministic replay/cached demo mode

- Symptom: the demo cannot be reproduced exactly under provider outages or rate-limit throttling.
- Root cause: no saved fixture or replay mode; the flow is live-only by design, with no serialized inputs/results checkpoint for rehydration.
- Proposed fix: add a lightweight offline fixture mode that stores the final request, registry snapshot, and approved draft to a JSON file, plus a `--replay` mode that re-renders the brief without hitting the model. This does not require changing the research logic itself.
- Estimated effort: medium (1–2 days).

### 3) Large-company context and SEC noise can blow up request size or validation quality

- Symptom: request trim fails, output is incomplete, or too much context produces weak validation.
- Root cause: `RequestSizeMiddleware` in `src/account_research/models/budget.py` trims older tool output but still depends on live search and SEC results; `max_request_tokens` is a hard ceiling, and company size matters.
- Proposed fix: choose a mid-size company with a small, public footprint; seed the run with a focused domain and a specific sales angle; reduce the search budget and page-fetch count for the demo.
- Estimated effort: low (few hours).

### 4) SEC EDGAR MCP dependency may fail at runtime

- Symptom: no SEC source data appears, or the tool fails before the draft is valid.
- Root cause: `mcp_servers.json` enables `sec_edgar` by default, and `load_mcp_tools()` tries to start the server from the environment in `src/account_research/mcp/client.py`.
- Proposed fix: validate `SEC_EDGAR_USER_AGENT` and the local Python MCP server installation before the demo. If the server fails, the app marks MCP unavailable, but that is not a hard failure for the non-SEC flow unless the buyer expects SEC data.
- Estimated effort: low to medium (0.5–1 day).

## Risks (could fail live) with mitigations

### Risk: provider quota exhaustion on OpenAI or Groq

- Mitigation: keep a warm fallback model configured; verify the exact model names and quotas beforehand; do not start the demo without confirming the account is valid.
- Replay mode: no current replay mode exists. The minimal mitigation is to save the final request and output to disk for a fallback re-run.

### Risk: final export path or approval loop not resuming cleanly

- Mitigation: use the CLI interactive approval flow in a single session. The code supports a callback-based approval handler, so a UI wrapper can use the same approval flow without changing core logic.
- Replay mode: not currently available.

### Risk: validation rejects everything because source IDs are missing or mismatched

- Mitigation: keep source selection constrained; use the tool discovery and `tools-check` path before the live run; prefer a company with a clear web and SEC trail. This is exactly what `submit_draft` is designed to catch.
- Replay mode: no current replay mode.

### Risk: company too large or too noisy

- Mitigation: choose a mid-size public company with a manageable footprint. Avoid Apple/large enterprise names that trigger enormous context and search results.
- Replay mode: not available; but a saved “good prompt + example response” can be replayed for later debugging.

## Nice-to-haves (skip for now)

- Persistent SQLite-based checkpointer for long-running sessions.
- Web UI approval controls with a session state store.
- A generated demo transcript JSON for recordkeeping.
- More resilient error-specific retries around network timeouts.
- A “demo mode” that uses fixed source URLs and a canned prompt for a deterministic run.

## UI readiness

Yes: the core is already structured well for a Streamlit wrapper without changing the core logic.

What is already reusable:

- `run_research()` accepts `approval_handler` and `auto_approve` in `src/account_research/agent/run.py`.
- The CLI approval is implemented as `_approval_prompt()` in `src/account_research/interfaces/cli.py`.
- Export and brief rendering are isolated in `src/account_research/tools/export_account.py`.
- The research engine is a call to a single function with clean arguments: `company`, `domain`, `ticker`, `context`, `approval_handler`.

What a Streamlit wrapper would need:

1. A form for `company`, `ticker`, and `seller context`.
2. A status panel that shows:
   - provider in use
   - sources registered
   - tool calls and counts
   - validation passed/failed
   - rate-limit waits
   - output tokens / final token report
3. An approve/reject UI using the same `approval_handler` contract instead of CLI input.
4. A final export step that writes the same files as the CLI, without modifying the export tool itself.

### LangGraph interrupt and resume details

This is one of the strongest signs the core is already UI-friendly.

The agent is built with:

- `interrupt_on={"export_account": True}` in `src/account_research/agent/builder.py`
- `MemorySaver()` as the checkpointer
- a per-run `thread_id` in `invoke_config` inside `run_research()`

That means the approval state is already interrupt-driven and resumable in-process. The current limitation is that the memory checkpoint is in-memory, not disk-backed. For a real multi-user or persistent UI, you would want a SQLite or durable checkpoint store.

## Progress/telemetry that can already power a UI

The repo emits practical telemetry already:

- `TokenUsageHandler` in `src/account_research/observability/tokens.py`
  - model calls
  - input/output tokens
  - largest request size
  - rate-limit errors
- `RateLimitState` in `src/account_research/models/ratelimit.py`
  - observed `x-ratelimit-*` headers
  - wait count and elapsed wait time
- `ExportDiagnostics` in `src/account_research/tools/export_account.py`
  - skills read
  - model name
  - claim failure/drop counts
  - rate-limit waits
- `SourceRegistry` in `src/account_research/sources/registry.py`
  - all source IDs and URLs tracked during the run
- `CitationReport` in `src/account_research/checks/citations.py`
  - passed vs failed claims
  - valid source IDs

This is sufficient to power a status dashboard or a minimal Streamlit progress stream without changing the core logic.

## Exact commands to run the demo once blockers are fixed

Setup:

```bash
cd /Users/yayang/Projects/account-research-agent
uv sync
```

Ensure `.env` contains:

```env
OPENAI_API_KEY="..."
GROQ_API_KEY="..."  # fallback
TAVILY_API_KEY="..."
SEC_EDGAR_USER_AGENT="Your Name your.email@example.com"
LANGSMITH_API_KEY="..."   # optional for tracing
LANGSMITH_TRACING=true
LANGSMITH_PROJECT=account-research-agent
```

Preflight:

```bash
uv run ara doctor
uv run ara tools-check "MongoDB" --ticker MDB
```

Live run with approval:

```bash
uv run ara research "MongoDB" --ticker MDB --context "selling developer tools and platform services to engineering leaders" 
```

Then in the CLI prompt, type:

```text
[a]pprove
```

with the brief displayed.

Optional forced single-provider run:

```bash
uv run ara research "MongoDB" --ticker MDB --context "selling developer tools and platform services to engineering leaders" --model openai:gpt-4o-mini
```

## Recommended company for the demo

Recommended demo company: MongoDB.

Why:

- public company with strong product narrative and clear market positioning
- enough public information to produce a useful account brief without a huge context footprint
- accessible SEC and web information without Apple-scale complexity
- easier to explain to an interviewer without needing an enterprise mega-company context dump
- good fit for a sales/engineering tool narrative

Avoid Apple / extremely large enterprise names for a single recorded live demo, because the search and SEC context can become too noisy and expensive.

## Bottom line

This repo is close to a good recorded demo, but the live run should still be treated as a controlled external-service demo rather than fully deterministic software. The code path is strong, the approval/export model is solid, and the project is far enough along to be credible, but the demo should still be preceded by a strict environment preflight and a carefully chosen mid-size public company.
