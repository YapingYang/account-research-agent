"""Application settings and environment loading."""

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


GROQ_REQUEST_TOKEN_CAP = 6500
DEFAULT_REQUEST_TOKEN_CAP = 32000


class Settings(BaseSettings):
    """Runtime configuration loaded from environment and optional .env file."""

    model_config = SettingsConfigDict(env_prefix="ARA_", env_file=".env", extra="ignore")

    model: str = "anthropic:claude-opus-5-5"
    fallback_model: str | None = None
    # Claude thinking depth/cost: low | medium | high | xhigh | max (Opus 5.5 defaults to medium).
    anthropic_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    # Claude counts thinking tokens against max_tokens, so it needs more than max_output_tokens.
    anthropic_max_tokens: int = Field(default=16000, ge=1)
    max_model_calls: int = Field(default=15, ge=1)
    # None = per-provider cap (see request_token_cap); ARA_MAX_REQUEST_TOKENS forces one cap.
    max_request_tokens: int | None = Field(default=None, ge=1)
    search_budget: int = Field(default=8, ge=1, le=25)
    max_page_fetches: int = Field(default=4, ge=1)
    max_sec_calls: int = Field(default=4, ge=1)
    max_search_results: int = Field(default=5, ge=1, le=20)
    snippet_chars: int = Field(default=300, ge=1)
    max_page_chars: int = 3000
    mcp_result_chars: int = 3000
    freshness_window_days: int = Field(default=365, ge=1)
    default_search_time_range: str = "year"
    default_search_topic: str = "general"
    recursion_limit: int = 50
    request_timeout_seconds: int = 60
    max_retries: int = 3
    max_rate_limit_waits: int = Field(default=6, ge=0)
    max_total_wait_seconds: float = Field(default=300, ge=0)
    rate_limit_default_wait_seconds: float = Field(default=60.0, ge=0.0)
    rate_limit_backoff_base_seconds: float = Field(default=2.0, ge=0.0)
    rate_limit_backoff_cap_seconds: float = Field(default=15.0, ge=0.0)
    tracing_backend: Literal["langsmith", "langfuse", "none"] = "langsmith"
    langsmith_project: str = "account-research-demo"
    langfuse_host: str = "https://cloud.langfuse.com"
    min_output_tpm: int = Field(default=3000, ge=1)
    max_output_tokens: int = Field(default=1500, ge=1)
    output_dir: Path = Path("output")
    skills_dir: Path = Path("skills")
    mcp_config_path: Path = Path("mcp_servers.json")
    mcp_enabled: bool = True
    mcp_timeout_seconds: int = 30
    log_level: str = "INFO"
    groq_api_key: SecretStr | None = Field(default=None, validation_alias="GROQ_API_KEY")
    openai_api_key: SecretStr | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    anthropic_api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    google_api_key: SecretStr | None = Field(default=None, validation_alias="GOOGLE_API_KEY")
    tavily_api_key: SecretStr | None = Field(default=None, validation_alias="TAVILY_API_KEY")
    sec_edgar_user_agent: str | None = Field(default=None, validation_alias="SEC_EDGAR_USER_AGENT")
    langsmith_api_key: SecretStr | None = Field(default=None, validation_alias="LANGSMITH_API_KEY")
    langfuse_public_key: SecretStr | None = Field(default=None, validation_alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: SecretStr | None = Field(default=None, validation_alias="LANGFUSE_SECRET_KEY")

    @model_validator(mode="after")
    def validate_positive_limits(self) -> "Settings":
        """Reject non-positive runtime limits."""
        if (
            self.max_page_chars < 1
            or self.max_model_calls < 1
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

    def request_token_cap(self, model: str | None = None) -> int:
        """Return the request-size cap for a model.

        Groq's free tier allows 8,000 tokens per minute, so its requests stay small. Other
        providers (for example OpenAI gpt-4o-mini with a 128k context) get a larger cap.
        """
        if self.max_request_tokens is not None:
            return self.max_request_tokens
        provider = (model or self.model).split(":", 1)[0].lower()
        return GROQ_REQUEST_TOKEN_CAP if provider == "groq" else DEFAULT_REQUEST_TOKEN_CAP

    def redacted(self) -> dict[str, Any]:
        """Return settings safe for diagnostic output."""
        values = self.model_dump()
        for key in (
            "anthropic_api_key",
            "groq_api_key",
            "openai_api_key",
            "google_api_key",
            "tavily_api_key",
        ):
            values[key] = "********" if values[key] is not None else None
        return values


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
