# Contributing

## Add a tool
Create one module under `src/account_research/tools/`, keep research tools read-only, and add it to the appropriate registry. Only the human-gated export tool may write.

## Keep research bounded
Keep research tools read-only. Bound returned content, mark retrieved text as untrusted, and register source URLs in the per-run registry. Do not add custom subagents.

## Add a prompt
Add a named Markdown file under `prompts/` and update `load_prompt` and its tests.

## Add a skill
Create `<skill>/SKILL.md` under `skills/`, include YAML frontmatter with `name` and one-sentence `description`, and add discovery coverage.

## Add an MCP server
Add a typed entry to `mcp_servers.json`, inspect the server's actual tool names, explicitly populate its allowlist, and document the decision in an ADR. Keep third-party code in a separately launched process; do not import or copy AGPL server code.
