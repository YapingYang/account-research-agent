# ADR 0007: Per-run context control

## Status

Accepted

## Context

Groq's spike exposed a free-tier limit near 8,000 tokens per minute. The research agent sends its prompt, skill/tool schemas, retained messages, and tool results again on each model call. Context must therefore stay compact across the full run, including citation repair.

## Decision

Use Deep Agents' documented Pydantic `response_format` with the tool-based strategy where provider tool calling supports it. Groq GPT-OSS rejects JSON response formatting alongside tools and may return its final JSON as a tool call, so that provider uses a local JSON capture tool followed by Pydantic validation. Seed the two short skills into the in-memory `StateBackend` using virtual `/skills/...` paths and use an in-memory LangGraph checkpointer. Keep Deep Agents' large-tool-result offloading enabled with the state backend. Replace its default summarization middleware instance with one that summarizes at an approximate 4,800-token history threshold and keeps the eight newest messages; its summary prompt explicitly preserves the company, ticker, seller context, facts, and source IDs.

The app also caps searches, fetched pages, and tool output size. Usage callbacks report every model call, aggregate input/output tokens, and the largest single-request input. On an exhausted rate-limit error, retry the entire run once with the configured fallback model and include both attempts in the usage totals. Do not use model-level fallbacks because they would require validating tool-binding behavior across providers.

## Consequences

The default virtual filesystem avoids disk writes and large tool responses can be offloaded automatically. Summarization is an additional model call when the history crosses the threshold, trading some token usage for smaller subsequent requests. An earlier lower threshold caused repeated summarization and lost task context, so the threshold and retained-message count were raised. The threshold is a guardrail, not a guarantee: live reports record the largest actual request, and runs above the project's 7,000-input-token stop line are not followed by another live company run.
