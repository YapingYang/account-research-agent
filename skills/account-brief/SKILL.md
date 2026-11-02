---
name: account-brief
description: "Use before drafting a sourced company brief or preparing sales talking points."
---

## BriefDraft sections

- **Snapshot:** concise company and product overview; name industry and public status only when supported.
- **Financial highlights:** public companies only. Prefer revenue and net income trends with period labels.
- **Signals:** material company events from the last 12 months.
- **Priorities:** stated goals and strategic initiatives.
- **Pain points:** frame possible needs as hypotheses, never established facts without evidence.
- **Talking points:** 3–5 concise ideas tied to the seller context provided by the user.

## Evidence and length

Every claim cites at least one source ID returned by a tool. Use one fact per claim and one sentence per claim. Keep each claim section to at most five claims. Talking points must also cite evidence for company facts. Cite source IDs only; code attaches URLs.

When evidence is thin, return fewer claims or empty sections. Never pad gaps with guesses. Mark unknowns as unavailable rather than inferring them.

When the brief is ready, call `submit_draft` with the complete BriefDraft. Do not write the brief as a normal message. If citation validation rejects claims, fix or remove them and resubmit once. After it accepts the draft, call `export_account` next; it takes no arguments.
