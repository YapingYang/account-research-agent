"""Command-line interface for diagnostics and future research runs."""

import asyncio
import json
import re
from typing import Annotated, Any, cast

import typer
from rich.console import Console
from rich.table import Table

from account_research import __version__
from account_research.config import get_settings
from account_research.mcp.client import discover_raw_tools, load_mcp_tools
from account_research.mcp.config import load_mcp_config
from account_research.skills.loader import discover_skills
from account_research.sources.registry import SourceRegistry
from account_research.tools.factory import make_research_tools

app = typer.Typer(no_args_is_help=True)
console = Console()


def version_callback(value: bool) -> None:
    """Print package version and exit."""
    if value:
        console.print(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool, typer.Option("--version", callback=version_callback, is_eager=True)
    ] = False,
) -> None:
    """Account Research Agent command line."""
    del version


@app.command()
def doctor() -> None:
    """Show local configuration and scaffold readiness."""
    settings = get_settings()
    required_missing = []
    if settings.groq_api_key is None:
        required_missing.append("GROQ_API_KEY")
    if settings.tavily_api_key is None:
        required_missing.append("TAVILY_API_KEY")
    console.print(f"Account Research Agent {__version__}")
    console.print(json.dumps(settings.redacted(), indent=2, default=str))
    try:
        mcp = load_mcp_config(settings.mcp_config_path)
        for name, server in mcp.servers.items():
            status = "enabled" if server.enabled else "disabled"
            console.print(f"MCP {name}: {status}, allowlist={len(server.allowed_tools)}")
            if (
                server.enabled
                and "SEC_EDGAR_USER_AGENT" in server.env_from_settings
                and not settings.sec_edgar_user_agent
            ):
                required_missing.append("SEC_EDGAR_USER_AGENT (enabled SEC EDGAR server)")
    except (OSError, ValueError) as exc:
        console.print(f"MCP config error: {exc}")
    skills = discover_skills(settings.skills_dir)
    console.print("Skills: " + ", ".join(item["name"] for item in skills) or "Skills: (none)")
    if required_missing:
        console.print("Missing required keys: " + ", ".join(required_missing), style="red")
        raise typer.Exit(code=1)


@app.command()
def skills() -> None:
    """List discovered skills."""
    table = Table("Name", "Description", "Path")
    for item in discover_skills(get_settings().skills_dir):
        table.add_row(item["name"], item["description"], str(item["path"]))
    console.print(table)


@app.command()
def research(
    company: str,
    domain: Annotated[str | None, typer.Option("--domain")] = None,
    ticker: Annotated[str | None, typer.Option("--ticker")] = None,
    context: Annotated[str | None, typer.Option("--context")] = None,
) -> None:
    """Research one company, validate citations, and write an AccountRecord."""
    from account_research.agent.run import ResearchRunError, run_research, save_record

    settings = get_settings()
    try:
        result = run_research(
            settings,
            company,
            domain=domain,
            ticker=ticker,
            context=context,
        )
    except Exception as exc:  # noqa: BLE001 - report model/provider failures to CLI users
        console.print(f"Research failed: {exc}", style="red")
        if isinstance(exc, ResearchRunError):
            console.print(
                "Token report before failure: " + json.dumps(exc.token_report.as_dict(), indent=2)
            )
            console.print(f"Elapsed before failure: {exc.elapsed_seconds:.1f}s")
            console.print(
                f"Rate-limit waits: {exc.rate_limit_waits} "
                f"({exc.rate_limit_wait_seconds:.1f}s total)"
            )
        raise typer.Exit(code=1) from exc
    slug = re.sub(r"[^a-z0-9]+", "-", company.casefold()).strip("-") or "company"
    output = settings.output_dir / f"draft_{slug}.json"
    save_record(result.record, str(output))
    console.print(f"Saved {output}")
    console.print(f"# {result.record.company_name}")
    if result.record.summary:
        console.print(result.record.summary.text)
    for title, claims in (
        ("Signals", result.record.signals),
        ("Priorities", result.record.priorities),
        ("Talking points", result.record.talking_points),
    ):
        if claims:
            console.print(f"## {title}")
            for claim in claims[:3]:
                console.print(f"- {claim.text} ({', '.join(claim.source_ids)})")
    console.print("Token report:")
    console.print(json.dumps(result.token_report.as_dict(), indent=2))
    console.print(
        f"Claims passed={result.citations.passed_count}, failed={result.claims_failed}, "
        f"dropped={result.claims_dropped}; elapsed={result.elapsed_seconds:.1f}s; "
        f"rate-limit waits={result.rate_limit_waits} "
        f"({result.rate_limit_wait_seconds:.1f}s total); "
        f"skills read={', '.join(result.skills_read) or '(none)'}"
    )
    console.print(f"Flags: {', '.join(result.record.flags) or '(none)'}")


def _token_estimate(value: object) -> int:
    return (len(str(value)) + 3) // 4


def _tool_schema(item: object) -> dict[str, Any]:
    schema = getattr(item, "args_schema", None)
    if isinstance(schema, dict):
        return schema
    if schema is not None and hasattr(schema, "model_json_schema"):
        return cast(dict[str, Any], schema.model_json_schema())
    return {}


@app.command("mcp-tools")
def mcp_tools() -> None:
    """Discover and describe tools exposed by configured MCP servers."""
    tools, status = asyncio.run(discover_raw_tools(get_settings()))
    console.print(
        f"MCP available: {status.available}" + (f" ({status.reason})" if status.reason else "")
    )
    if not status.available:
        return
    schema_total = 0
    for item in tools:
        schema = _tool_schema(item)
        cost = _token_estimate(json.dumps(schema, ensure_ascii=False))
        schema_total += cost
        console.print(f"{item.name} — {item.description or ''}")
        console.print(f"  args={json.dumps(schema, ensure_ascii=False)}; schema≈{cost} tokens")
    console.print(f"Total schema tokens≈{schema_total}")


@app.command("tools-check")
def tools_check(
    company: str, ticker: Annotated[str | None, typer.Option("--ticker")] = None
) -> None:
    """Exercise search, page extraction, and allowlisted MCP tools without an LLM."""
    settings = get_settings()
    registry = SourceRegistry()
    web_tools = make_research_tools(settings, registry)
    mcp_tools, status = asyncio.run(load_mcp_tools(settings, registry))
    total = 0
    snippets: list[tuple[str, str]] = []
    for item in web_tools[:1]:
        try:
            output = item.invoke({"query": f"{company} company official"})
            snippets.append((item.name, str(output)))
            total += _token_estimate(output)
            console.print(
                f"{item.name}: chars={len(str(output))}, tokens≈{_token_estimate(output)}"
            )
        except Exception as exc:  # noqa: BLE001 - diagnostic reports tool failure
            console.print(f"{item.name}: unavailable ({exc})")
    if snippets and web_tools:
        try:
            search_payload = json.loads(snippets[0][1])
            if search_payload:
                output = web_tools[1].invoke({"url": search_payload[0]["url"]})
                total += _token_estimate(output)
                console.print(
                    f"fetch_page: chars={len(str(output))}, tokens≈{_token_estimate(output)}"
                )
        except Exception as exc:  # noqa: BLE001
            console.print(f"fetch_page: unavailable ({exc})")
    for item in mcp_tools:
        schema = _tool_schema(item)
        props: dict[str, Any] = schema.get("properties", {})
        args = {}
        for key in props:
            if key.casefold() in {
                "company",
                "company_name",
                "name",
                "query",
                "ticker",
                "symbol",
                "identifier",
            }:
                args[key] = (
                    ticker
                    if key.casefold() in {"ticker", "symbol", "identifier"} and ticker
                    else company
                )
            elif key.casefold() in {"cik", "cik_number"} and ticker:
                args[key] = ticker
            elif key in schema.get("required", []):
                args[key] = ticker or company
        try:
            output = item.invoke(args)
            total += _token_estimate(output)
            console.print(
                f"{item.name}: chars={len(str(output))}, tokens≈{_token_estimate(output)}"
            )
        except Exception as exc:  # noqa: BLE001
            console.print(f"{item.name}: unavailable ({exc})")
    console.print(
        f"MCP available: {status.available}" + (f" ({status.reason})" if status.reason else "")
    )
    console.print("Sources:")
    for source in registry.all():
        console.print(f"  {source.id} {source.kind} {source.url}")
    console.print(f"Estimated output tokens total≈{total}")
    schema_tokens = sum(_token_estimate(_tool_schema(item)) for item in [*web_tools, *mcp_tools])
    console.print(f"Total tool schema tokens≈{schema_tokens}")


@app.command()
def spike(
    trials: Annotated[int, typer.Option("--trials", min=1)] = 3,
    pause_seconds: Annotated[float, typer.Option("--pause-seconds", min=0)] = 5,
    models: Annotated[str, typer.Option("--models")] = (
        "groq:openai/gpt-oss-120b,groq:llama-3.3-70b-versatile,groq:qwen/qwen3-32b,"
        "groq:openai/gpt-oss-20b,groq:qwen/qwen3.8-27b,gemini:gemini-3.7-flash"
    ),
) -> None:
    """Run the test-only model capability spike."""
    from account_research.spike.run import run_spike

    run_spike(
        get_settings(),
        [item.strip() for item in models.split(",") if item.strip()],
        trials=trials,
        pause_seconds=pause_seconds,
    )
