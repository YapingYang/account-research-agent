# ADR 0005: Single-agent research tools and source tracking

## Status

Accepted

## Context

The first implementation milestone showed that the multi-agent structure adds orchestration and token overhead before there is a tested need for separate researcher and verifier agents. Research also needs reproducible source attribution, bounded context, and a safe failure mode when optional MCP services are unavailable.

## Decision

Use one agent with two production skills: `account-brief` and `sec-filings`. Construct web tools and their source registry per research run. Cap search calls, results, snippets, extracted pages, and MCP responses. Wrap retrieved content as untrusted text, reject non-public fetch URLs, and deduplicate source URLs after removing tracking parameters. A code-level citation check ensures claims can only cite registered source URLs. Start MCP servers as separate processes, filter by an explicit deny-by-default allowlist, and treat startup or call timeouts as unavailable tools.

Use Tavily search and extraction with the existing SDK; use the SEC EDGAR MCP server as a separate process. The `tools-check` command exercises tools without an LLM, while `mcp-tools` reports discovered tool schemas and approximate token costs.

## Consequences

Tool behavior and sources are isolated per run. Five read-only SEC tools are currently allowlisted: company search, company information, company facts, recent filings, and financial statements. Offline tests mock all network integrations. `submit_draft` permits one citation repair; after a second failure it drops invalid claims and records a flag. The approval and export flow is specified in ADR 0007.
