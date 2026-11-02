"""Models for model-authored drafts and code-assembled account records."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, HttpUrl

SourceId = Annotated[str, Field(pattern=r"^S\d+$")]


class Claim(BaseModel):
    """One factual statement supported by one or more registered source IDs."""

    text: str
    source_ids: list[SourceId] = Field(min_length=1)


class Source(BaseModel):
    """A source used in the account brief."""

    id: SourceId
    url: HttpUrl
    title: str
    retrieved_at: datetime
    kind: Literal["search_result", "web_page", "sec_filing"]


class BriefDraft(BaseModel):
    """Structured draft returned by the research model."""

    company_name: str
    domain: str | None = None
    industry: str | None = None
    is_public: bool | None = None
    summary: Claim
    financial_highlights: list[Claim] = Field(default_factory=list)
    signals: list[Claim] = Field(default_factory=list)
    priorities: list[Claim] = Field(default_factory=list)
    pain_points: list[Claim] = Field(default_factory=list)
    talking_points: list[Claim] = Field(default_factory=list)


class AccountRecord(BaseModel):
    """CRM-ready account record assembled and validated by application code."""

    company_name: str
    domain: str | None = None
    industry: str | None = None
    is_public: bool | None = None
    summary: Claim | None = None
    financial_highlights: list[Claim] = Field(default_factory=list)
    signals: list[Claim] = Field(default_factory=list)
    priorities: list[Claim] = Field(default_factory=list)
    pain_points: list[Claim] = Field(default_factory=list)
    talking_points: list[Claim] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    generated_at: datetime
    model: str
