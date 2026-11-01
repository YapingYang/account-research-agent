"""CRM-ready account research record schema."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


class Claim(BaseModel):
    """A research claim with at least one supporting URL."""

    text: str
    source_urls: list[HttpUrl] = Field(min_length=1)


class Source(BaseModel):
    """A source used in the account brief."""

    url: HttpUrl
    title: str
    retrieved_at: datetime
    kind: Literal["web", "sec_filing"]


class AccountRecord(BaseModel):
    """CRM-ready sourced account research record."""

    company_name: str
    domain: str | None = None
    industry: str | None = None
    is_public: bool | None = None
    summary: str
    financial_highlights: list[Claim] = Field(default_factory=list)
    signals: list[Claim] = Field(default_factory=list)
    priorities: list[Claim] = Field(default_factory=list)
    pain_points: list[Claim] = Field(default_factory=list)
    talking_points: list[str] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    generated_at: datetime
    model: str
