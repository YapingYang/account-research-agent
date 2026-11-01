# Architecture

The future flow accepts a company name and optional domain/seller context, researches it with a main Deep Agent and a researcher subagent, and asks a verifier subagent to check support. A human approves the result before the export tool writes `brief.md` and `account.json`.

```text
Input -> Main agent -> Researcher (web + allowlisted SEC MCP) -> Verifier -> Human approval -> Export
```

All research tools are read-only. Export is the only write capability. MCP processes are separately launched and tool access is deny-by-default.
