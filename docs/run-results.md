# Live research run results

Commands were run on 1 November 2026 with the configured Groq, Tavily, and SEC credentials. Token counts come from provider usage metadata; largest request is the largest individual `input_tokens` value observed.

| Company | Result | Model calls | Input tokens | Output tokens | Largest request input | Rate-limit errors | Runtime | Claims passed / failed / dropped | Skills read |
|---|---|---:|---:|---:|---:|---:|---:|---|---|
| Apple (AAPL) | Stopped after provider rate limits | 9 | 22,261 | 1,222 | 4,354 | 2 | 108.9 s | N/A / N/A / N/A (no draft completed) | Not determined |
| Ramp | Not run | — | — | — | — | — | — | — | — |

The Apple attempt began with `groq:openai/gpt-oss-120b`; after a rate-limit response, the run retried from scratch with `groq:qwen/qwen3.8-27b`. Groq rejected the fallback request because its expected 1,566 output tokens exceeded the account's 1,000 output-tokens-per-minute limit. The run stopped here as required; Ramp was not started.

The largest observed input request was 4,354 tokens, below the 7,000-token stop line. Across both attempts the provider reported 22,261 input tokens and 1,222 output tokens. Input usage is aggregated over nine calls, so it counts the conversation history being sent again on successive calls. Provider usage metadata does not break that total down into system prompt, skill text, tool definitions, tool results, and retained history. The main prompt is 925 characters (141 whitespace-delimited words); the two skills are 169 and 113 words. Tool outputs are capped at 3,000 characters and fetched pages at three per run. The failed run did not produce a final draft, so citation and skill-read outcomes are unavailable.

Earlier Apple implementation attempts exposed two model-output issues before the rate-limited run: Groq JSON response formatting cannot be combined with tool calling, and GPT-OSS sometimes emits a final draft as a `json` tool call. The final implementation uses tool-based structured output for other providers and a local JSON capture/validation path for Groq GPT-OSS. The later run reached that provider's output-token rate limit before a brief could be completed.

Two intermediate live GPT-OSS attempts were also measured during implementation. One emitted an unregistered `JSON` call after 8 model calls (15,741 input tokens, 1,140 output tokens, 3,636 largest request input, 81.1 seconds). Another ended without a parseable draft after 10 calls (19,719 input tokens, 1,998 output tokens, 3,407 largest request input, 87.6 seconds). Neither was a successful company run; they exposed the output-format and context-retention issues corrected before the final rate-limited attempt.
