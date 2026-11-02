"""MCP server configuration models and file loading."""

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class MCPServerConfig(BaseModel):
    """Configuration for one separately launched MCP server."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    transport: str = "stdio"
    command: str
    args: list[str] = Field(default_factory=list)
    env_from_settings: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)


class MCPConfig(BaseModel):
    """Validated MCP server collection."""

    model_config = ConfigDict(extra="forbid")

    servers: dict[str, MCPServerConfig]


def load_mcp_config(path: Path) -> MCPConfig:
    """Load and validate an MCP JSON configuration file."""
    with path.open(encoding="utf-8") as config_file:
        return MCPConfig.model_validate(json.load(config_file))
