# Live verification status

Verification was stopped after Groq explicitly reported that the daily token quota was exhausted. No paid tier was used. Ollama is not installed (`command -v ollama` returned not found), so checks use `groq:openai/gpt-oss-120b`. The daily-quota response included `retry-after: 626`; per instruction, no further live company checks were started.

| Priority check | Result | Evidence | Remaining |
|---|---|---|---|
| 1. Apple approve | **Failed: daily token quota** | Latest attempt: 16 calls; 38,302 input / 2,905 output tokens; largest provider request 5,015; largest estimate 6,407; 7 waits / 274.4 seconds; runtime 296.3 seconds; 1 rate-limit error. No draft, claims, skill report, flags, or files. Latest raw headers below. | Required to ship; rerun after free-tier reset. |
| 2. Ramp approve | Not run after daily-quota error | Apple did not pass, so testing stopped per instruction. | Run after Apple succeeds and quota resets. |
| 3. Apple reject and revise | Not run after daily-quota error | Same stop condition. | Run after Apple succeeds and quota resets. |
| 4. Apple with `ARA_MCP_ENABLED=false` | Not run after daily-quota error | Same stop condition. | Run after Apple succeeds and quota resets. |
| 5. Rate limit, `max_total_wait_seconds=1` | Passed offline simulation | `tests/test_ratelimit.py::test_total_wait_cap_fails_fast_with_limit_and_reset`; also daily-quota fail-fast test. | None for the simulated check. |

## Latest Groq daily-quota response

```json
{
  "retry-after": "626",
  "x-ratelimit-limit-requests": "1000",
  "x-ratelimit-limit-tokens": "8000",
  "x-ratelimit-remaining-requests": "976",
  "x-ratelimit-remaining-tokens": "7894",
  "x-ratelimit-reset-requests": "34m33.6s",
  "x-ratelimit-reset-tokens": "794ms"
}
```

The rate-limit error text reported the **daily token quota** exhausted with reset value `626`. The listed minute-level token allowance had replenished; it was not the blocking limit. No TPD header was supplied.

## Run attempts and fixes

| Attempt | Result and measurements | Bug found / fix commit |
|---|---|---|
| Apple, initial budget implementation | Failed before submission: estimate 6,525 exceeded the 6,500 cap after trimming. 17 calls; 43,504 input / 4,337 output; largest actual 5,163; largest sent estimate 6,341; 8 waits / 288.1 s; 331.9 s runtime. | Older plain-text tool results were not trimmed. Fixed, with source-preservation tests, in `e63e817`. |
| Apple, after general trimming | Agent stopped normally without submitting. 8 calls; 24,399 input / 1,491 output; largest actual 4,808; estimate 6,063; 4 waits / 119 s; 146 s runtime. | Runner did not prompt for a draft after an early normal stop. Fixed with a runner regression test in `1724a3f`. |
| Apple, after early-stop fix | Failed because the Python `repr` estimate overstated the serialized request (6,525, then 7,534). 17 calls; 45,761 input / 4,583 output; largest actual 5,769; largest sent estimate 6,480; 8 waits / 251.6 s; 277.4 s runtime. | Estimator now serializes actual message contents and tool schemas. Fixed in `79683db`. |
| Apple, latest | Failed on daily quota as reported above. | No further provider workaround attempted. |

Failed runs did not produce claim results, a complete skill-read report, or flags. There are no approved Apple/Ramp `AccountRecord`s and no files under `examples/`.

## Offline verification

`make check` passed after the budget, early-stop, and request-estimator changes: Ruff lint and formatting, mypy on 38 source files, and **71 tests**. Coverage includes search/page/SEC budgets, final-submit instructions, request trimming with IDs and titles preserved, newest-result preservation, approval, citation repair, and wait caps.

The [LangChain middleware docs](https://docs.langchain.com/oss/python/langchain/middleware/custom#wrap-style-hooks), confirmed through the docs MCP, say `wrap_model_call` can intercept and modify a `ModelRequest`; `before_model` is also available for message trimming. This implementation trims in `wrap_model_call`, before size and rate-limit checks. No paid model tier was used.
