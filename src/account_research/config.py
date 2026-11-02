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
    max_model_calls: int = Field(default=15, ge=1)
    max_request_tokens: int = Field(default=6500, ge=1)
    search_budget: int = Field(default=4, ge=1, le=25)
    max_page_fetches: int = Field(default=2, ge=1)
    max_sec_calls: int = Field(default=4, ge=1)
    max_search_results: int = Field(default=5, ge=1, le=20)
    snippet_chars: int = Field(default=300, ge=1)
    max_page_chars: int = 3000
    mcp_result_chars: int = 3000
    recursion_limit: int = 50
    request_timeout_seconds: int = 60
    max_retries: int = 3
    max_rate_limit_waits: int = Field(default=6, ge=0)
    max_total_wait_seconds: float = Field(default=300, ge=0)
    min_output_tpm: int = Field(default=3000, ge=1)
    max_output_tokens: int = Field(default=1500, ge=1)
    output_dir: Path = Path("output")
    skills_dir: Path = Path("skills")
    mcp_config_path: Path = Path("mcp_servers.json")
    mcp_enabled: bool = True
    mcp_timeout_seconds: int = 30
    log_level: str = "INFO"
    groq_api_key: SecretStr | None = Field(default=None, validation_alias="GROQ_API_KEY")
    google_api_key: SecretStr | None = Field(default=None, validation_alias="GOOGLE_API_KEY")
    tavily_api_key: SecretStr | None = Field(default=None, validation_alias="TAVILY_API_KEY")
    sec_edgar_user_agent: str | None = Field(default=None, validation_alias="SEC_EDGAR_USER_AGENT")

    @model_validator(mode="after")
    def validate_positive_limits(self) -> "Settings":
        """Reject non-positive runtime limits."""
        if (
            self.max_page_chars < 1
            or self.max_model_calls < 1
            or self.max_request_tokens < 1
            or self.max_page_fetches < 1
            or self.max_sec_calls < 1
            or self.recursion_limit < 1
            or self.mcp_timeout_seconds < 1
            or self.request_timeout_seconds < 1
            or self.max_retries < 0
            or self.max_rate_limit_waits < 0
            or self.max_total_wait_seconds < 0
            or self.min_output_tpm < 1
            or self.max_output_tokens < 1
            or self.mcp_result_chars < 1
        ):
            raise ValueError("limits must be positive (max_retries must be non-negative)")
        return self

    def redacted(self) -> dict[str, Any]:
        """Return settings safe for diagnostic output."""
        values = self.model_dump()
        for key in (
            "groq_api_key",
            "google_api_key",
            "tavily_api_key",
        ):
            values[key] = "********" if values[key] is not None else None
        return values


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
