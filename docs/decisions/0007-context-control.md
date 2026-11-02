# ADR 0007: Rate limits and approved export

## Status

Accepted

## Context

Groq free-tier limits can be exhausted across repeated agent calls even when each individual request is small. Restarting a whole run on a rate limit wastes the research and tokens. The agent also needs to produce a typed record while ensuring that no unreviewed output reaches disk.

## Decision

Keep the same call and model across bounded 429 retries. Before each call, estimate input tokens from the request and compare with available rate-limit headers; wait for the relevant reset when the remaining budget is insufficient. Preserve the most recent headers and include them in the run report. Bound waits with `max_rate_limit_waits` and `max_total_wait_seconds`. Set a per-call output cap. Do not switch models for rate limits. Use fallback only for unavailable models or repeated server errors, and only if the fallback's reported output-token limit meets `min_output_tpm`.

The agent submits drafts through `submit_draft`; application code checks source IDs against the per-run registry, permits one repair, and stores the validated record. It then calls zero-argument `export_account`. Deep Agents pauses at that tool using `interrupt_on` and a checkpointer. The CLI resumes the same thread with an approval decision. Rejection supplies feedback, clears the pending record, and allows one revision. Export validates the record before writing the brief, JSON record, and run report. `--yes` supplies approval for scripted use.

## Consequences

Rate-limit handling is bounded and observable, but header availability and provider semantics vary. Qwen 3.8 27B is not a practical free-tier fallback for full drafts because its observed output limit was 1,000 tokens per minute, below the 3,000-token minimum. Groq GPT-OSS 120B remains the configured default; Gemini can be selected with `--model` when credentials and quota are available.
