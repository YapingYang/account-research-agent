"""Application settings and environment loading."""

from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment and optional .env file."""

    model_config = SettingsConfigDict(env_prefix="ARA_", env_file=".env", extra="ignore")

    model: str = "groq:openai/gpt-oss-120b"
    fallback_model: str | None = None
    search_budget: int = Field(default=8, ge=1, le=25)
    max_page_chars: int = 6000
    recursion_limit: int = 50
    output_dir: Path = Path("output")
    skills_dir: Path = Path("skills")
    mcp_config_path: Path = Path("mcp_servers.json")
    mcp_enabled: bool = True
    mcp_timeout_seconds: int = 30
    log_level: str = "INFO"
    groq_api_key: SecretStr | None = Field(default=None, validation_alias="GROQ_API_KEY")
    tavily_api_key: SecretStr | None = Field(default=None, validation_alias="TAVILY_API_KEY")
    langsmith_api_key: SecretStr | None = Field(default=None, validation_alias="LANGSMITH_API_KEY")
    sec_edgar_user_agent: str | None = Field(default=None, validation_alias="SEC_EDGAR_USER_AGENT")
    langsmith_tracing: bool = Field(default=False, validation_alias="LANGSMITH_TRACING")
    langsmith_project: str = Field(
        default="account-research-agent", validation_alias="LANGSMITH_PROJECT"
    )

    @model_validator(mode="after")
    def validate_positive_limits(self) -> "Settings":
        """Reject non-positive runtime limits."""
        if self.max_page_chars < 1 or self.recursion_limit < 1 or self.mcp_timeout_seconds < 1:
            raise ValueError(
                "max_page_chars, recursion_limit, and mcp_timeout_seconds must be positive"
            )
        return self

    def redacted(self) -> dict[str, Any]:
        """Return settings safe for diagnostic output."""
        values = self.model_dump()
        for key in ("groq_api_key", "tavily_api_key", "langsmith_api_key"):
            values[key] = "********" if values[key] is not None else None
        return values


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
