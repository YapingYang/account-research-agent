# Account Research Agent

An open-source Python scaffold for producing sourced account briefs and CRM-ready records with human approval before export.

## Why

The project separates research, source verification, and approved export so account insights can be reviewed and traced to evidence.

## How it works

```text
[Diagram placeholder: company input -> research -> verification -> human approval -> export]
```

## MCP

MCP adds separately process-isolated access to SEC filings. The configured server is [`sec-edgar-mcp`](https://github.com/stefanoamorelli/sec-edgar-mcp), which is licensed AGPL-3.0. This project launches it as a process and does not import or copy its code. The allowlist is deny-by-default; an empty list exposes no tools and must be populated only after inspecting actual tool names.

## Skills

Skills are Markdown methods scoped to each agent: `main/account-brief` guides brief writing; `researcher/web-research` guides web research; `researcher/sec-filings` guides EDGAR work; and `verifier/source-verification` guides claim checking. They live under `skills/`; custom subagents receive only their own folder.

## Controls

Research tools will be read-only. The export tool will be the only writer and will require human approval. MCP tools are filtered through an explicit allowlist.

## Quickstart

Requires Python 3.11+ and `uv`.

```sh
uv sync --all-extras
cp .env.example .env
ara doctor
```

Set `GROQ_API_KEY` and `TAVILY_API_KEY` in `.env` to pass required-key diagnostics. Set a real-name and email `SEC_EDGAR_USER_AGENT` when enabling EDGAR.

## Project structure

```text
src/account_research/   Application package
skills/                 Agent-specific methods
tests/                  Offline tests
docs/                   Architecture and decisions
app/                    Optional Gradio demo
```

## Roadmap

1. ✓ Project scaffold and configuration
2. Model factory
3. Read-only web research tools
4. Researcher subagent and SEC MCP integration
5. Verifier subagent and skills
6. Main agent orchestration
7. Human-gated export
8. LangSmith tracing
9. Research quality improvements
10. Gradio demo

## License

MIT. The optional separately launched SEC EDGAR MCP server is AGPL-3.0; see its upstream license and the process-isolation decision in ADR 0002.
