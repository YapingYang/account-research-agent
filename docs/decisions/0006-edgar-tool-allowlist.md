# ADR 0006: SEC EDGAR MCP tool allowlist

## Status

Accepted

## Context

The installed `sec-edgar-mcp` server was inspected with `ara mcp-tools`. It exposed 21 tools, including read-only lookup and filing tools as well as deeper extraction and analysis tools.

## Decision

Enable only `search_companies`, `get_company_info`, `get_company_facts`, and `get_recent_filings`. Together they cover company discovery by name, ticker/company identity lookup, standardized facts, and recent filing lists. `get_company_facts` meets the required facts-or-financials coverage, so the separate financial statement tool is unnecessary. Filing-content, insider, XBRL-discovery, financial statement, and analysis tools remain excluded until a research workflow needs them.

## Consequences

The four selected tools add about 218 estimated schema tokens per agent call using the `characters / 4` approximation. An empty or changed allowlist exposes no additional tools. Reinspect the server with `ara mcp-tools` before expanding access.
