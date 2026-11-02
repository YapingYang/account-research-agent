# ADR 0001: Use Deep Agents

## Status

Accepted; implementation details are maintained in [ADR 0005](0005-single-agent-code-citation-check.md).

## Decision

Use one Deep Agent for account research, with skills and tools attached to that agent. A checkpointed `export_account` interrupt provides the human approval gate before files are written. There are no delegated subagents.
