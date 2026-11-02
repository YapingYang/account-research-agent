# Live verification status

The verified repo state is: the project is green offline, the environment is configured with `uv`, and the required Apple live run was attempted but did not finish because the Groq free-tier path stopped the run before an approved export was produced.

## Environment result

- `uv` is installed.
- `uv python install 3.11` succeeded.
- `uv sync --all-extras` succeeded.
- `uv run ara doctor` loaded `GROQ_API_KEY`, `TAVILY_API_KEY`, and `SEC_EDGAR_USER_AGENT`.
- `make check` passed with `71 passed`.

## Required live run

Command run:

```sh
printf 'a\n' | uv run ara research "Apple" --ticker AAPL --context "selling AI workflow tools to finance teams"
```

Result:

- The run reached the model-call budget and stopped with:
  `Research failed: Reached max_model_calls=15; the final instruction did not call submit_draft.`
- Provider rate-limit headers during the run included:
  - `x-ratelimit-limit-requests`: `1000`
  - `x-ratelimit-limit-tokens`: `8000`
  - `x-ratelimit-remaining-requests`: `984`
  - `x-ratelimit-remaining-tokens`: `3268`
  - `x-ratelimit-reset-requests`: `23m2.4s`
  - `x-ratelimit-reset-tokens`: `35.49s`
- No approved export files were created under `examples/`.

This is the exact reset window and quota state observed in the repo during the required live Apple step; no paid tier was used.

## Verification table

| Check | Status | Evidence |
|---|---|---|
| `uv sync --all-extras` | Passed | Completed successfully with Python 3.11.15 |
| `make check` | Passed | Ruff, mypy, and pytest all passed; 71 tests passed |
| `uv run ara doctor` | Passed | All three required keys loaded |
| Required Apple live run | Failed on provider limits | Run stopped at max model calls before a verified approved export |
| Ramp / reject-then-approve / MCP-disabled Apple | Not run | The required Apple run was blocked before the remaining live checks were allowed |
| Offline budget and request trimming tests | Passed | `tests/test_model_budgets.py` and `tests/test_ratelimit.py` passed |

## Verified live vs offline only

### Verified live

- `uv run ara doctor` successfully read the required env values.
- `make check` passed in the uv 3.11 environment.
- `uv run ara --help` and the command-specific help outputs were checked and match the implementation.

### Verified offline only

- The end-to-end research/export path is implemented and covered by offline tests, but a live approved export was not produced because the provider stopped the required Apple research run before completion.
- Model support for Gemini and Ollama exists in the code and config, but those live paths were not completed in this workspace.
- There are no approved example files under `examples/` yet.

## Offline verification summary

`make check` passed across the repository under the required uv-based Python 3.11 setup: Ruff lint and formatting, mypy type-checking, and **71 pytest tests**. The offline tests cover search/page/SEC budgets, final-submit instructions, request trimming with IDs and titles preserved, newest-result preservation, approval flow, citation repair, and rate-limit caps.

The earlier budget and request-size work is reflected in the project code and tests; the temporary free-tier provider limit was the reason the live Apple run did not complete.
