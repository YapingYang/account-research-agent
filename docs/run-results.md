# Live research run history

All live company research attempts are listed here. Usage numbers are provider metadata; a missing value means the run did not report it or the earlier capture did not retain it. No run has produced a complete, approved live `AccountRecord`.

| Run | Result | Model | Calls | Input tokens | Output tokens | Largest request | Waits / wait time | Runtime | Claims passed / failed / dropped | Skills read | Flags |
|---|---|---|---:|---:|---:|---:|---|---:|---|---|---|
| Apple, initial structured-output attempt | Failed: Groq rejected JSON response format with tools | GPT-OSS 120B | — | — | — | — | — | ~3 min | — / — / — | — | — |
| Apple, ToolStrategy attempt | Failed: provider HTTP 400 | GPT-OSS 120B | — | — | — | — | — | ~3 min | — / — / — | — | — |
| Apple, JSON tool-call attempt | Failed: final draft emitted as unsupported `JSON` tool call | GPT-OSS 120B | 8 | 15,741 | 1,140 | 3,636 | — | 81.1 s | — / — / — | — | No draft |
| Apple, capture/validation attempt | Failed: no parseable draft | GPT-OSS 120B | 10 | 19,719 | 1,998 | 3,407 | — | 87.6 s | — / — / — | — | No draft |
| Apple, fallback attempt | Failed: restart on Qwen exhausted its output TPM allowance | GPT-OSS 120B → Qwen 3.8 27B | 9 | 22,261 | 1,222 | 4,354 | 2 errors; wait durations not recorded | 108.9 s | — / — / — | Not determined | No draft |
| Apple, Gemini full run (`--yes`) | Failed: repeated 429s with no usable rate-limit headers; wait cap stopped the run | Gemini 3.6 Flash | 29 | 88,823 | 11,672 | 10,106 | 5 / 300 s (unknown category) | 521.5 s | — / — / — | Not determined | No draft; raw latest headers `{}` |
| Apple, rejection/revision attempt | Failed before approval: one-second cap rejected a 60-second unknown reset | Gemini 3.6 Flash | 1 | 0 | 0 | 0 | 0 / 0 s (wait refused) | 5.7 s | — / — / — | Not determined | No draft |
| Ramp attempt (`--yes`) | Failed before draft: one-second cap rejected a 60-second unknown reset | Gemini 3.6 Flash | 2 | 4,241 | 217 | 4,241 | 0 / 0 s (wait refused) | 16.8 s | — / — / — | Not determined | No draft |
| Apple with MCP disabled (`--yes`) | Failed before draft: one-second cap rejected a 60-second unknown reset | Gemini 3.6 Flash | 1 | 0 | 0 | 0 | 0 / 0 s (wait refused) | 5.8 s | — / — / — | Not determined | No draft; SEC-unavailable flag path not observed live |

The latest complete run stopped after 300 seconds of waits. The wait category was `unknown`, headers were empty, and the model reported nine rate-limit errors. The next 60-second wait would exceed the configured cap, so the run ended without restarting on another model. No output files were written.

The Gemini model preflight was a separate minimal live request: Gemini 3.6 Flash called the `ping` tool and reported 50 tokens. Gemini 3.7 Flash returned HTTP 503 for high demand. The successful preflight confirms tool binding, not full-run free-tier quota availability.

Offline tests cover citation repair, rejection/revision/approval, no output before approval, `--yes`, and wait-cap behavior. See [verification](verification.md) for the acceptance matrix. `examples/apple/` and `examples/ramp/` remain empty because no live records were approved.

## 2 November 2026 budgeted Groq checks

Ollama was not installed, so the budgeted checks used Groq `openai/gpt-oss-120b`. Live attempts were run sequentially with the default 300-second total wait cap. A daily-quota error occurred on the final attempt; no lower-priority live check was started after it.

| Attempt | Result | Calls | Input / output tokens | Largest provider input / estimate | Waits / duration | Runtime | Claims passed / dropped | Skills read | Flags |
|---|---|---:|---:|---|---|---:|---|---|---|
| Apple, before general tool-result trimming | Request estimate over cap; no draft | 17 | 43,504 / 4,337 | 5,163 / 6,341 | 8 / 288.1 s | 331.9 s | — / — | Not reported | No draft |
| Apple, after general trimming | Agent ended normally without submitting | 8 | 24,399 / 1,491 | 4,808 / 6,063 | 4 / 119.0 s | 146.0 s | — / — | Not reported | No draft |
| Apple, after early-stop fix | Request estimate over cap; no draft | 17 | 45,761 / 4,583 | 5,769 / 6,480 | 8 / 251.6 s | 277.4 s | — / — | Not reported | No draft |
| Apple, after serialized-request estimator fix | Daily token quota exhausted | 16 | 38,302 / 2,905 | 5,015 / 6,407 | 7 / 274.4 s | 296.3 s | — / — | Not reported | No draft; no files |

The latest response's raw headers and daily reset indication are recorded in [verification.md](verification.md). No run generated an AccountRecord, so the fields for passed/dropped claims, skills read, and account flags are unavailable rather than inferred.
