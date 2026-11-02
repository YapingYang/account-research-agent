# ADR 0004: Default model

## Context

The project needs an open-weight model on Groq that reliably calls tools and reads the applicable skill. Free-tier limits make request count and pauses relevant; the comparison uses three trials per model by default.

## Options tested

- `groq:openai/gpt-oss-120b`
- `groq:llama-3.3-70b-versatile`
- `groq:qwen/qwen3-32b`
- `groq:openai/gpt-oss-20b`
- `groq:qwen/qwen3.8-27b`
- `gemini:gemini-3.7-flash` (skipped; `GOOGLE_API_KEY` was unavailable)

## Results summary

Groq's current `/models` response listed 11 active IDs. Three available models were run for three trials each: `openai/gpt-oss-120b`, `openai/gpt-oss-20b`, and `qwen/qwen3.8-27b`. Each passed tool calling, skill reading, and output formatting in all three trials. `qwen/qwen3.8-27b` was the faster of the two runner-up candidates (49.599 second median vs. 51.331 seconds for GPT-OSS 20B). The prior `llama-3.3-70b-versatile` and `qwen/qwen3-32b` IDs were absent from the current endpoint response. Gemini was skipped because `GOOGLE_API_KEY` is not configured.

Live Groq responses for the configured key returned request and token limits of 1,000 RPM and 8,000 TPM; the headers do not identify the account tier. They did not include a daily token limit, so TPD remains marked TODO in the results. Median trivial-run usage was 7,632 tokens for 120B, 7,714 for 20B, and 6,819 for Qwen 3.8. Daily run estimates remain TODO until TPD is copied from the Groq console.

## Decision

Use `groq:openai/gpt-oss-120b` as the default and leave `fallback_model` unset. Although Qwen 3.8 passed the spike checks, its observed free-tier output limit was only 1,000 output tokens per minute, below the 3,000 TPM needed for full account briefs. The live Apple run also showed that a fresh-run fallback after 429 wasted progress. Rate limits now trigger same-call waiting and retry. A fallback is eligible only for missing/decommissioned models or repeated server errors and only when output TPM headers confirm at least 3,000.

## Consequences

The configured default is `groq:openai/gpt-oss-120b` and `fallback_model` defaults to `None`. Gemini support remains available through the optional `gemini` extra as a different-vendor fallback candidate; it was not evaluated without `GOOGLE_API_KEY`. Model capability evidence is collected through an isolated fixture tool and in-memory skill files. Re-run the spike when skipped model IDs return to the Groq catalog or when Gemini credentials are configured.
