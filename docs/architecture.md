# Architecture

The future flow accepts a company name and optional domain/seller context, then researches it with one Deep Agent. Web and SEC tools populate a per-run source registry; a code-level citation check will validate claim URLs before a human approves export to `brief.md` and `account.json`.

```text
Input -> Main agent -> Researcher (web + allowlisted SEC MCP) -> Verifier -> Human approval -> Export
```

All research tools are read-only. Export is the only write capability. MCP processes are separately launched and tool access is deny-by-default.
