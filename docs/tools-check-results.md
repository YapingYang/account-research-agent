# Live research tools check

Command run: `ara tools-check Apple --ticker AAPL` (1 November 2026).

The live run succeeded with Tavily and the SEC EDGAR MCP server. Approximate token counts use `ceil(returned characters / 4)` and include each returned tool output.

| Call | Returned chars | Approx. tokens |
|---|---:|---:|
| `web_search` | 2,319 | 580 |
| `fetch_page` | 3,098 | 775 |
| `get_company_info` | 432 | 108 |
| `search_companies` | 1,146 | 287 |
| `get_company_facts` | 1,672 | 418 |
| `get_recent_filings` | 3,072 | 768 |
| **Total tool output** | **11,739** | **2,936** |

The four allowlisted MCP schemas total about **218 tokens**; all six tool schemas (including web search and page fetch) total about **323 tokens** by the same approximation. The web fetch was truncated at its configured 3,000-character content budget (wrapper tags contribute to the reported total). Six sources were registered: one extracted page, four search results, and one SEC EDGAR page. Duplicate normalized URLs share one source ID.

The SEC tool discovery returned 21 tools. The allowlist covers company lookup, standardized facts, and recent filings; it leaves financial statement extraction, filing-content extraction, insider analysis, and XBRL discovery disabled.
