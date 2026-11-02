# ADR 0001: Use Deep Agents

## Status
Superseded by [ADR 0005](0005-single-agent-code-citation-check.md)

## Decision
Use LangChain Deep Agents for the orchestrator and delegated research/verifier agents.

## Rationale
The project needs explicit subagents, per-agent skills, and a human approval gate before exporting. Deep Agents provides a framework aligned with that architecture while keeping application code config-driven.
