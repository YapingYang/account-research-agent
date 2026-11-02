"""Live, test-only harness for comparing candidate chat models."""

import json
import math
import re
import statistics
import time
from pathlib import Path
from typing import Any, cast

from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from deepagents.backends.utils import create_file_data
from langchain_core.messages import AIMessage

from account_research.config import Settings
from account_research.models.factory import get_chat_model
from account_research.spike.tools import lookup_company_fact

DEFAULT_MODELS = [
    "groq:openai/gpt-oss-120b",
    "groq:llama-3.3-70b-versatile",
    "groq:qwen/qwen3-32b",
    "groq:openai/gpt-oss-20b",
    "groq:qwen/qwen3.8-27b",
    "gemini:gemini-3.6-flash",
]
GROQ_TOOL_CALLING = "Groq docs state hosted models support local/remote tool use."
GROQ_NON_CHAT = {
    "canopylabs/orpheus-arabic-saudi": "No; text-to-speech model",
    "canopylabs/orpheus-v1-english": "No; text-to-speech model",
    "whisper-large-v3": "No; speech-to-text model",
    "whisper-large-v3-turbo": "No; speech-to-text model",
    "meta-llama/llama-prompt-guard-2-22m": "Classifier/safety model, not an agent candidate",
    "meta-llama/llama-prompt-guard-2-86m": "Classifier/safety model, not an agent candidate",
    "openai/gpt-oss-safeguard-20b": "Classifier/safety model, not an agent candidate",
}
FORMAT_RE = re.compile(r"^FACT: .+ \[source: fixture-001\]$")
SKILL_PATH = Path("skills/spike/fact-format/SKILL.md")


def _tool_called(messages: list[Any]) -> bool:
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        for call in message.tool_calls:
            company = str(call.get("args", {}).get("company", ""))
            if call.get("name") == "lookup_company_fact" and "Acme" in company:
                return True
    return False


def _skill_read(messages: list[Any]) -> bool:
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        for call in message.tool_calls:
            if call.get("name") == "read_file" and "fact-format/SKILL.md" in str(
                call.get("args", {})
            ):
                return True
    return False


def _final_text(messages: list[Any]) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage) and isinstance(message.content, str):
            if message.content.strip():
                return message.content.strip()
    return ""


def score_trial(messages: list[Any]) -> dict[str, bool]:
    """Score capability evidence from a message history."""
    return {
        "tool_called": _tool_called(messages),
        "skill_read": _skill_read(messages),
        "format_ok": bool(FORMAT_RE.fullmatch(_final_text(messages))),
    }


def _usage_total(messages: list[Any]) -> int | None:
    totals = [
        usage.get("total_tokens")
        for message in messages
        if isinstance(message, AIMessage)
        and (usage := (message.usage_metadata or message.response_metadata.get("token_usage", {})))
        and usage.get("total_tokens") is not None
    ]
    return sum(totals) if totals else None


def _run_trial(model_string: str, settings: Settings, skill_content: str) -> dict[str, Any]:
    started = time.perf_counter()
    messages: list[Any] = []
    error = None
    rate_limit_headers: dict[str, str] = {}
    try:
        provider = model_string.split(":", 1)[0]
        if provider == "groq":
            import httpx

            def capture_headers(response: httpx.Response) -> None:
                rate_limit_headers.update(
                    {
                        key.lower(): value
                        for key, value in response.headers.items()
                        if key.lower().startswith("x-ratelimit-")
                    }
                )

            with httpx.Client(
                timeout=settings.request_timeout_seconds,
                event_hooks={"response": [capture_headers]},
            ) as http_client:
                model = get_chat_model(model_string, settings, http_client=http_client)
                agent = create_deep_agent(
                    model=model,
                    tools=[lookup_company_fact],
                    skills=["/skills/"],
                    backend=StateBackend(),
                    system_prompt=(
                        "Answer the company question. Use the fixture tool for a fact "
                        "and follow applicable skills."
                    ),
                )
                config: dict[str, Any] = {"recursion_limit": settings.recursion_limit}
                input_state: dict[str, Any] = {
                    "messages": [{"role": "user", "content": "What is one fact about Acme Corp?"}],
                    "files": {"/skills/fact-format/SKILL.md": create_file_data(skill_content)},
                }
                result = agent.invoke(cast(Any, input_state), config=cast(Any, config))
                messages = result.get("messages", [])
        else:
            model = get_chat_model(model_string, settings)
            agent = create_deep_agent(
                model=model,
                tools=[lookup_company_fact],
                skills=["/skills/"],
                backend=StateBackend(),
                system_prompt=(
                    "Answer the company question. Use the fixture tool for a fact "
                    "and follow applicable skills."
                ),
            )
            config = {"recursion_limit": settings.recursion_limit}
            input_state = {
                "messages": [{"role": "user", "content": "What is one fact about Acme Corp?"}],
                "files": {"/skills/fact-format/SKILL.md": create_file_data(skill_content)},
            }
            result = agent.invoke(cast(Any, input_state), config=cast(Any, config))
            messages = result.get("messages", [])
    except Exception as exc:  # Record provider and agent errors as trial data.
        error = f"{type(exc).__name__}: {exc}"
    scores = score_trial(messages)
    return {
        **scores,
        "latency_seconds": round(time.perf_counter() - started, 3),
        "total_tokens": _usage_total(messages),
        "rate_limit_headers": rate_limit_headers,
        "error": error,
        "answer": _final_text(messages),
        "messages": messages,
    }


def _serialize_trial(trial: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in trial.items() if key != "messages"}


def _pass_rate(trials: list[dict[str, Any]], name: str) -> str:
    return f"{sum(bool(trial.get(name)) for trial in trials)}/{len(trials)}" if trials else "n/a"


def _rate_value(
    headers: dict[str, str], names: tuple[str, ...], console_name: str = "Groq console"
) -> str:
    for name in names:
        if name in headers:
            return headers[name]
    return f"TODO: copy from {console_name}"


def render_markdown(results: dict[str, Any]) -> str:
    """Render aggregate results as a readable Markdown report."""
    lines = ["# Model spike results", "", f"Trials per model: {results['trials_per_model']}", ""]
    lines.extend(["## Current Groq model catalog", "", "| Model ID | Tool calling |", "|---|---|"])
    for entry in results.get("groq_catalog", []):
        lines.append(f"| `{entry['id']}` | {entry['tool_calling']} |")
    lines.append("")
    lines.extend(
        [
            "Tool calling is marked supported for Groq chat models per the [Groq tool-use overview]"
            "(https://console.groq.com/docs/tool-use/overview). Audio generation and transcription "
            "models use specialized audio endpoints and are not chat-agent candidates. See the "
            "[Groq Speech to Text docs](https://console.groq.com/docs/speech-to-text) and "
            "[Orpheus TTS docs](https://console.groq.com/docs/text-to-speech/orpheus).",
            "",
            "RPM and TPM values below were captured from live `x-ratelimit-limit-*` response "
            "headers for the configured key; the headers do not identify its account tier. The "
            "responses did not include a daily-token limit; copy TPD from the Groq console where "
            "shown. Daily run estimate = TPD / median tokens per trivial run.",
            "",
        ]
    )
    for model, data in results["models"].items():
        trials = data["trials"]
        latencies = [t["latency_seconds"] for t in trials if t.get("latency_seconds") is not None]
        tokens = [t["total_tokens"] for t in trials if t.get("total_tokens") is not None]
        errors = "; ".join(t["error"] for t in trials if t.get("error")) or "—"
        estimated_runs = data.get(
            "estimated_runs_per_day", "TODO: daily limit not returned in headers"
        )
        lines.extend(
            [
                f"## {model}",
                "",
                "| Tool called | Skill read | Format OK | Median latency (s) | "
                "Median tokens | RPM | TPM | TPD | Est. runs/day | Errors |",
                "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
                f"| {_pass_rate(trials, 'tool_called')} | {_pass_rate(trials, 'skill_read')} | "
                f"{_pass_rate(trials, 'format_ok')} | "
                f"{statistics.median(latencies) if latencies else 'n/a'} | "
                f"{statistics.median(tokens) if tokens else 'n/a'} | "
                f"{data.get('rate_limits', {}).get('rpm', 'TODO: copy from Groq console')} | "
                f"{data.get('rate_limits', {}).get('tpm', 'TODO: copy from Groq console')} | "
                f"{data.get('rate_limits', {}).get('tpd', 'TODO: copy from Groq console')} | "
                f"{data.get('estimated_runs_per_day', 'TODO')} | {errors} |",
                "",
                "### Token budget",
                "",
                f"Median tokens per trivial run: {statistics.median(tokens) if tokens else 'n/a'}. "
                f"Estimated runs/day at the daily token limit: "
                f"{estimated_runs}",
                "",
            ]
        )
    if results.get("skipped_models"):
        lines.extend(
            [
                "## Skipped models",
                "",
                *[f"- {m}: {why}" for m, why in results["skipped_models"].items()],
                "",
            ]
        )
    if results.get("recommended_fallback"):
        lines.extend(
            [
                "## Recommendation",
                "",
                f"Fallback model: `{results['recommended_fallback']}`",
                "",
            ]
        )
    return "\n".join(lines)


def write_results(results: dict[str, Any], output_dir: Path = Path("docs/spike")) -> None:
    """Write JSON data and Markdown summary."""
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "model-spike-results.json").write_text(
        json.dumps(results, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "model-spike-results.md").write_text(render_markdown(results), encoding="utf-8")


def _available_groq_models(settings: Settings) -> set[str]:
    """Return model IDs currently listed by Groq's models endpoint."""
    import httpx

    if settings.groq_api_key is None:
        raise ValueError("GROQ_API_KEY is required to query Groq's models endpoint")
    response = httpx.get(
        "https://api.groq.com/openai/v1/models",
        headers={"Authorization": f"Bearer {settings.groq_api_key.get_secret_value()}"},
        timeout=settings.request_timeout_seconds,
    )
    response.raise_for_status()
    return {entry["id"] for entry in response.json().get("data", [])}


def run_spike(
    settings: Settings,
    models: list[str] | None = None,
    *,
    trials: int = 3,
    pause_seconds: float = 5,
    output_dir: Path = Path("docs/spike"),
) -> dict[str, Any]:
    """Check candidate availability, run trials, and persist the report."""
    candidates = models or DEFAULT_MODELS
    available = _available_groq_models(settings)
    catalog = [
        {
            "id": model_id,
            "tool_calling": GROQ_NON_CHAT.get(model_id, "Yes; " + GROQ_TOOL_CALLING),
            "support_source": (
                "https://console.groq.com/docs/speech-to-text"
                if model_id.startswith("whisper-")
                else "https://console.groq.com/docs/text-to-speech/orpheus"
                if model_id.startswith("canopylabs/orpheus-")
                else "https://console.groq.com/docs/tool-use/overview"
            ),
        }
        for model_id in sorted(available)
    ]
    results: dict[str, Any] = {
        "trials_per_model": trials,
        "models": {},
        "skipped_models": {},
        "groq_catalog": catalog,
        "groq_catalog_checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    skill_content = SKILL_PATH.read_text(encoding="utf-8")
    runnable = []
    for model_string in candidates:
        provider, model = model_string.split(":", 1)
        if provider == "groq" and model not in available:
            results["skipped_models"][model_string] = "model ID absent from Groq /models response"
        elif provider == "gemini" and settings.google_api_key is None:
            results["skipped_models"][model_string] = "GOOGLE_API_KEY is not configured"
        else:
            runnable.append(model_string)
    for model_string in runnable:
        model_results = []
        for trial_index in range(trials):
            result = _run_trial(model_string, settings, skill_content)
            model_results.append(_serialize_trial(result))
            if trial_index + 1 < trials and pause_seconds:
                time.sleep(pause_seconds)
        limits = {}
        all_headers: dict[str, str] = next(
            (trial["rate_limit_headers"] for trial in model_results if trial["rate_limit_headers"]),
            {},
        )
        console_name = "Groq console" if model_string.startswith("groq:") else "Google AI Studio"
        limits["rpm"] = _rate_value(all_headers, ("x-ratelimit-limit-requests",), console_name)
        limits["tpm"] = _rate_value(all_headers, ("x-ratelimit-limit-tokens",), console_name)
        limits["tpd"] = _rate_value(
            all_headers,
            (
                "x-ratelimit-limit-tokens-per-day",
                "x-ratelimit-limit-tokens-day",
                "x-ratelimit-limit-tokens-daily",
            ),
            console_name,
        )
        median_tokens = (
            statistics.median(
                [trial["total_tokens"] for trial in model_results if trial["total_tokens"]]
            )
            if any(trial["total_tokens"] for trial in model_results)
            else None
        )
        try:
            daily_tokens = int(limits["tpd"])
        except ValueError:
            daily_tokens = None
        estimated_runs = (
            math.floor(daily_tokens / median_tokens)
            if daily_tokens is not None and median_tokens
            else "TODO: daily limit not returned in headers"
        )
        results["models"][model_string] = {
            "trials": model_results,
            "rate_limit_headers": all_headers,
            "rate_limits": limits,
            "estimated_runs_per_day": estimated_runs,
        }
    configured_default = settings.model
    eligible = [
        (name, data)
        for name, data in results["models"].items()
        if data["trials"]
        and all(
            all(trial.get(check) for check in ("tool_called", "skill_read", "format_ok"))
            for trial in data["trials"]
        )
    ]
    eligible.sort(
        key=lambda pair: (
            -sum(trial.get("format_ok", False) for trial in pair[1]["trials"])
            / len(pair[1]["trials"]),
            -sum(trial.get("skill_read", False) for trial in pair[1]["trials"])
            / len(pair[1]["trials"]),
            -sum(trial.get("tool_called", False) for trial in pair[1]["trials"])
            / len(pair[1]["trials"]),
            statistics.median([trial["latency_seconds"] for trial in pair[1]["trials"]]),
        )
    )
    results["recommended_fallback"] = next(
        (name for name, _ in eligible if name != configured_default), None
    )
    write_results(results, output_dir)
    return results
