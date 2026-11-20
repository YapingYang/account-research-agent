"""Models for model-authored drafts and code-assembled account records."""

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, HttpUrl

SourceId = Annotated[str, Field(pattern=r"^S\d+$")]


class Claim(BaseModel):
    """One factual statement supported by one or more registered source IDs."""

    text: str = Field(description="One sentence, one fact or idea.")
    source_ids: list[SourceId] = Field(min_length=1, description="IDs of the sources it relies on.")
    evidence_quote: str = Field(
        default="",
        max_length=220,
        description=(
            "Exact words copied from the cited source text (5-30 words); never a paraphrase "
            "or your own sentence. For SEC metrics, the metric line."
        ),
    )


class Source(BaseModel):
    """A source used in the account brief."""

    id: SourceId
    url: HttpUrl
    title: str
    retrieved_at: datetime
    kind: Literal["search_result", "web_page", "sec_filing", "sec"]
    published_date: date | None = None
    freshness_status: Literal["fresh", "stale", "undated"] = "undated"
    content: str = ""
    form_type: str | None = None
    fiscal_period: str | None = None
    date_source: Literal["sec", "tavily", "url", "page_meta", "none"] = "none"

    @property
    def freshness(self) -> Literal["fresh", "stale", "undated"]:
        return self.freshness_status


class BriefDraft(BaseModel):
    """Structured draft returned by the research model."""

    company_name: str
    domain: str | None = None
    industry: str | None = None
    is_public: bool | None = None
    summary: Claim = Field(description="Two-sentence company snapshot.")
    financial_highlights: list[Claim] = Field(
        default_factory=list, description="Reported results with period labels (public only)."
    )
    signals: list[Claim] = Field(
        default_factory=list, description="2-4 material company events from the last 12 months."
    )
    priorities: list[Claim] = Field(
        default_factory=list,
        description="2-4 goals leadership has stated, each quoting what was said.",
    )
    pain_points: list[Claim] = Field(
        default_factory=list,
        description=(
            "2-3 hypotheses phrased 'Possible need: ...', each citing the fact that suggests "
            "it and quoting that fact."
        ),
    )
    talking_points: list[Claim] = Field(
        default_factory=list,
        description=(
            "3-5 openers that connect a cited company fact to the seller context; quote the "
            "fact, not the seller context."
        ),
    )


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
