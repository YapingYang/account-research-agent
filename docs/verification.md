# v0.1.0 verification

The offline suite passes. `GOOGLE_API_KEY` is present in the runtime environment. A minimal Gemini 3.6 Flash request successfully called a tool; Gemini 3.7 Flash returned 503. Full research with Gemini 3.6 Flash then received repeated 429 responses with no usable quota headers. No approved live Apple or Ramp outputs exist, so no examples were fabricated.

| Check | Command / scenario | Result | Evidence | Bug found / fix commit |
|---|---|---|---|---|
| Gemini model preflight | Minimal `bind_tools` request to Gemini 3.6 Flash | Passed | Returned `ping` tool call; 50 tokens. Gemini 3.7 Flash returned HTTP 503 for high demand | No fix needed |
| 1. Apple, approve | `ara research Apple --ticker AAPL --context "selling AI workflow tools to finance teams" --model gemini:gemini-3.6-flash --yes` | Failed: repeated rate limits; cap stopped run | 29 calls; 88,823 input / 11,672 output tokens; largest request 10,106; 5 waits / 300 seconds; 9 rate-limit errors; latest headers `{}`; no draft or files | No provider quota fix available |
| 2. Apple, reject and revise | Same company/context/model; piped reject feedback then approve, cap set to 1 second | Failed before approval: rate limit | 1 call, 0 reported tokens, 5.7 seconds; refused a 60-second wait against 1-second cap; no files | No provider quota fix available |
| 3. Ramp | `ara research Ramp --context "selling AI workflow tools to finance teams" --model gemini:gemini-3.6-flash --yes`, cap 1 second | Failed before draft: rate limit | 2 calls; 4,241 input / 217 output; largest request 4,241; refused 60-second wait against 1-second cap; no files | No provider quota fix available |
| 4. MCP disabled | Apple with `ARA_MCP_ENABLED=false`, Gemini 3.6 Flash, cap 1 second | Failed before draft: same rate limit | 1 call, 0 reported tokens, 5.8 seconds; refused a 60-second wait; no files. Offline tests cover the SEC-unavailable flag path | No provider quota fix available |
| 5. Wait cap | Set `max_total_wait_seconds=1` and trigger a limit | Passed offline and exercised live | `tests/test_ratelimit.py`; Apple check 2 and Ramp check 3 both stopped instead of waiting 60 seconds | Fixed header classifier precedence when daily and RPM headers both show zero; fixed CLI preview markup that hid `[sources: ...]`. Regression tests: `tests/test_ratelimit.py`, `tests/test_cli.py`. Fix commit: `ee9820a` |
| 6. Citation repair | Scripted agent submits an invalid claim, then repairs it | Passed offline replay | `tests/test_agent_run.py` exercises reject-then-fix and accepted export; `tests/test_submission.py` covers citation checks | No bug found |

## Offline check

`UV_CACHE_DIR=/tmp/ara-uv-cache make check` passed: Ruff lint and format, mypy (36 source files), and pytest (64 tests). The offline approval suite covers approve, reject/revise/approve, reject twice without output, `--yes`, and no files before approval. The default spike candidate uses Gemini 3.6 Flash when a Google key is configured.

## Current release evidence

There are no approved `examples/apple/` or `examples/ramp/` outputs yet. The live requirements for an SEC-cited Apple brief, revised Apple brief, private-company Ramp brief, and live MCP-disabled brief remain unverified. A Gemini tool call was verified, but the configured provider quota prevented full runs. Do not treat v0.1.0 as live-verified until the full-run checks pass or are explicitly waived.
