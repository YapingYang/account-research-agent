# Account Research Agent

An open-source Python scaffold for producing sourced account briefs and CRM-ready records with human approval before export.

## Why

The single research agent uses bounded read-only web and SEC tools. A per-run source registry deduplicates URLs so claims can be traced to evidence before human-approved export.

## How it works

```text
[Diagram placeholder: company input -> research -> verification -> human approval -> export]
```

## MCP

MCP adds separately process-isolated access to SEC filings. The configured server is [`sec-edgar-mcp`](https://github.com/stefanoamorelli/sec-edgar-mcp), which is licensed AGPL-3.0. This project launches it as a process and does not import or copy its code. The allowlist is deny-by-default and enables company search, company information, company facts, and recent filings.

## Skills

Skills are Markdown methods under `skills/account-brief` and `skills/sec-filings`. They guide the single agent; skill bodies will be filled in during the prompt and output milestone.

## Controls

Research tools are read-only. Web searches and extracted content are bounded; fetched pages and MCP results are marked as untrusted input. The export tool is the only writer and requires human approval. MCP tools are filtered through an explicit allowlist.

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
skills/                 Single-agent methods
tests/                  Offline tests
docs/                   Architecture and decisions
```

## Roadmap

1. ✓ Project scaffold and configuration
2. Model factory
3. Read-only web research tools and SEC MCP integration
4. Agent prompts and skills
5. Main agent orchestration
6. Human-gated export
7. LangSmith tracing
8. Research quality improvements

## License

MIT. The optional separately launched SEC EDGAR MCP server is AGPL-3.0; see its upstream license and the process-isolation decision in ADR 0002.
