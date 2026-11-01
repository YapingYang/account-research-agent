# ADR 0002: Use MCP for SEC filings

## Status
Accepted

## Decision
Access SEC filing tools through the separately launched `sec-edgar-mcp` process.

## Rationale
MCP keeps SEC data access behind a clear process boundary and avoids importing or copying the server's AGPL-3.0 code. Its tools remain unavailable until their real names are inspected and explicitly allowlisted. Empty allowlists expose no tools.
