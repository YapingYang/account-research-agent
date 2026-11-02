# Model spike results

Trials per model: 3

## Current Groq model catalog

| Model ID | Tool calling |
|---|---|
| `allam-2-7b` | Yes; Groq docs state hosted models support local/remote tool use. |
| `canopylabs/orpheus-arabic-saudi` | No; text-to-speech model |
| `canopylabs/orpheus-v1-english` | No; text-to-speech model |
| `meta-llama/llama-prompt-guard-2-22m` | Classifier/safety model, not an agent candidate |
| `meta-llama/llama-prompt-guard-2-86m` | Classifier/safety model, not an agent candidate |
| `openai/gpt-oss-120b` | Yes; Groq docs state hosted models support local/remote tool use. |
| `openai/gpt-oss-20b` | Yes; Groq docs state hosted models support local/remote tool use. |
| `openai/gpt-oss-safeguard-20b` | Classifier/safety model, not an agent candidate |
| `qwen/qwen3.8-27b` | Yes; Groq docs state hosted models support local/remote tool use. |
| `whisper-large-v3` | No; speech-to-text model |
| `whisper-large-v3-turbo` | No; speech-to-text model |

Tool calling is marked supported for Groq chat models per the [Groq tool-use overview](https://console.groq.com/docs/tool-use/overview). Audio generation and transcription models use specialized audio endpoints and are not chat-agent candidates. See the [Groq Speech to Text docs](https://console.groq.com/docs/speech-to-text) and [Orpheus TTS docs](https://console.groq.com/docs/text-to-speech/orpheus).

RPM and TPM values below were captured from live `x-ratelimit-limit-*` response headers for the configured key; the headers do not identify its account tier. The responses did not include a daily-token limit; copy TPD from the Groq console where shown. Daily run estimate = TPD / median tokens per trivial run.

## groq:openai/gpt-oss-120b

| Tool called | Skill read | Format OK | Median latency (s) | Median tokens | RPM | TPM | TPD | Est. runs/day | Errors |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 3/3 | 3/3 | 3/3 | 14.729 | 7632 | 1000 | 8000 | TODO: copy from Groq console | TODO: daily limit not returned in headers | — |

### Token budget

Median tokens per trivial run: 7632. Estimated runs/day at the daily token limit: TODO: daily limit not returned in headers

## groq:openai/gpt-oss-20b

| Tool called | Skill read | Format OK | Median latency (s) | Median tokens | RPM | TPM | TPD | Est. runs/day | Errors |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 3/3 | 3/3 | 3/3 | 51.331 | 7714 | 1000 | 8000 | TODO: copy from Groq console | TODO: daily limit not returned in headers | — |

### Token budget

Median tokens per trivial run: 7714. Estimated runs/day at the daily token limit: TODO: daily limit not returned in headers

## groq:qwen/qwen3.8-27b

| Tool called | Skill read | Format OK | Median latency (s) | Median tokens | RPM | TPM | TPD | Est. runs/day | Errors |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 3/3 | 3/3 | 3/3 | 49.599 | 6819 | 1000 | 8000 | TODO: copy from Groq console | TODO: daily limit not returned in headers | — |

### Token budget

Median tokens per trivial run: 6819. Estimated runs/day at the daily token limit: TODO: daily limit not returned in headers

## Skipped models

- groq:llama-3.3-70b-versatile: model ID absent from Groq /models response
- groq:qwen/qwen3-32b: model ID absent from Groq /models response
- gemini:gemini-3.7-flash: GOOGLE_API_KEY is not configured

## Recommendation

Fallback model: `groq:qwen/qwen3.8-27b`
