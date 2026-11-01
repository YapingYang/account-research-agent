# ADR 0003: Use skills for agent methods

## Status
Accepted

## Decision
Store focused operating guidance in per-agent `SKILL.md` files rather than growing system prompts.

## Rationale
Separate skill folders let each agent load only the methods relevant to its role; custom subagents do not inherit the main agent's skills.
