---
name: sec-filings
description: "Use for SEC research on US-listed companies and cite filing evidence."
---

Use only for US-listed companies. Call tools in this order: identify the company with `get_company_info` using its ticker (or `search_companies` by name, then `get_company_info`); call `get_company_facts`; call `get_recent_filings`. Prefer the latest 10-K and recent 8-Ks. Use `fetch_page` on filing URLs when filing details are needed.

Extract revenue and net income trends with period labels, segment information, major risk themes from the latest 10-K, and notable recent 8-K events. Do not infer facts missing from filings. Cite the source IDs returned by SEC tools or page extraction for each claim; never invent a filing URL or source ID.
