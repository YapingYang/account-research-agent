# ADR 0001: Use Deep Agents

## Status
Accepted

## Decision
Use LangChain Deep Agents for the orchestrator and delegated research/verifier agents.

## Rationale
The project needs explicit subagents, per-agent skills, and a human approval gate before exporting. Deep Agents provides a framework aligned with that architecture while keeping application code config-driven.
