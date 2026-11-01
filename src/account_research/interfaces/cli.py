"""Command-line interface for diagnostics and future research runs."""

import json
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from account_research import __version__
from account_research.config import get_settings
from account_research.mcp.config import load_mcp_config
from account_research.skills.loader import discover_skills

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
    for agent_name in ("main", "researcher", "verifier"):
        skills = discover_skills(settings.skills_dir)
        selected = [item["name"] for item in skills if item["agent_folder"] == agent_name]
        console.print(f"Skills {agent_name}: {', '.join(selected) or '(none)'}")
    if required_missing:
        console.print("Missing required keys: " + ", ".join(required_missing), style="red")
        raise typer.Exit(code=1)


@app.command()
def skills() -> None:
    """List discovered skills."""
    table = Table("Agent", "Name", "Description", "Path")
    for item in discover_skills(get_settings().skills_dir):
        table.add_row(item["agent_folder"], item["name"], item["description"], str(item["path"]))
    console.print(table)


@app.command()
def research(
    company: str,
    domain: Annotated[str | None, typer.Option("--domain")] = None,
    context: Annotated[str | None, typer.Option("--context")] = None,
) -> None:
    """Research a company (future milestone)."""
    del company, domain, context
    console.print("Not implemented yet (milestone 6)")
