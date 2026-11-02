# ADR 0003: Use skills for agent methods

## Status
Superseded by [ADR 0005](0005-single-agent-code-citation-check.md)

## Decision
Store focused operating guidance in `SKILL.md` files rather than growing system prompts.

## Rationale
The current single agent loads only `account-brief` and `sec-filings`; the earlier per-agent structure was removed to reduce token and orchestration overhead.
