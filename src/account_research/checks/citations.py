"""Citation validation and draft-to-record conversion."""

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from account_research.schemas.account import AccountRecord, BriefDraft, Claim
from account_research.sources.registry import SourceRegistry

_CLAIM_SECTIONS = (
    "summary",
    "financial_highlights",
    "signals",
    "priorities",
    "pain_points",
    "talking_points",
)


@dataclass(frozen=True)
class FailedClaim:
    """A draft claim that cannot be resolved to registered sources."""

    section: str
    index: int
    claim: Claim
    reason: str


@dataclass(frozen=True)
class CitationReport:
    """Citation check result, preserving valid claims and their locations."""

    passed_claims: list[Claim]
    failed_claims: list[FailedClaim]
    valid_source_ids: list[str]
    cited_source_ids: list[str]
    # (section, index) -> claim whose paraphrased quote was replaced by the real source sentence
    repaired_claims: dict[tuple[str, int], Claim] = field(default_factory=dict)

    @property
    def passed_count(self) -> int:
        return len(self.passed_claims)

    @property
    def failed_count(self) -> int:
        return len(self.failed_claims)


def _claims(draft: BriefDraft) -> Iterator[tuple[str, int, Claim]]:
    for section in _CLAIM_SECTIONS:
        value = getattr(draft, section)
        if section == "summary":
            yield section, 0, value
        else:
            for index, claim in enumerate(value):
                yield section, index, claim


def _source_text_for(claim: Claim, registry: SourceRegistry) -> str:
    source_ids = set(claim.source_ids or [])
    texts = [
        source.content for source in registry.all() if source.id in source_ids and source.content
    ]
    return "\n".join(texts)


_UNIT_SCALE = {
    "": 1.0,
    "k": 1e3,
    "thousand": 1e3,
    "m": 1e6,
    "mm": 1e6,
    "million": 1e6,
    "b": 1e9,
    "bn": 1e9,
    "billion": 1e9,
    "t": 1e12,
    "trillion": 1e12,
}
_UNIT_PATTERN = r"(?:\s?(?P<unit>trillion|billion|million|thousand|bn|mm|[kmbt])\b)?"
_CLAIM_MONEY = re.compile(r"\$\s?(?P<num>\d[\d,]*(?:\.\d+)?)" + _UNIT_PATTERN, re.IGNORECASE)
_PERCENT = re.compile(r"(?P<num>\d[\d,]*(?:\.\d+)?)\s?%")
_SOURCE_NUMBER = re.compile(
    r"(?<![\w.])\$?\s?(?P<num>\d[\d,]*(?:\.\d+)?)" + _UNIT_PATTERN, re.IGNORECASE
)
_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


@dataclass(frozen=True)
class _Value:
    """A numeric value with the rounding tolerance implied by how it was written."""

    label: str
    amount: float
    tolerance: float
    percent: bool


def _parse_value(match: re.Match[str], *, percent: bool) -> _Value:
    raw = match.group("num").replace(",", "")
    unit = "" if percent else (match.groupdict().get("unit") or "").casefold()
    scale = _UNIT_SCALE.get(unit, 1.0)
    decimals = len(raw.split(".", 1)[1]) if "." in raw else 0
    amount = float(raw) * scale
    tolerance = 0.5 * (10**-decimals) * scale
    label = raw + ("%" if percent else {1e3: "k", 1e6: "m", 1e9: "b", 1e12: "t"}.get(scale, ""))
    return _Value(label, amount, tolerance, percent)


def _claim_values(text: str) -> list[_Value]:
    values = [_parse_value(match, percent=False) for match in _CLAIM_MONEY.finditer(text)]
    values.extend(_parse_value(match, percent=True) for match in _PERCENT.finditer(text))
    return values


def _source_values(text: str) -> list[_Value]:
    text = _ISO_DATE.sub(" ", text)
    percents = [_parse_value(match, percent=True) for match in _PERCENT.finditer(text)]
    numbers = [_parse_value(match, percent=False) for match in _SOURCE_NUMBER.finditer(text)]
    return percents + numbers


def _value_supported(claim_value: _Value, source_values: list[_Value]) -> bool:
    """Match a claimed value to a source number, allowing for the claim's rounding.

    Signs are ignored so "a net loss of $487.3 million" matches "NetIncomeLoss: -487296000".
    """
    target = abs(claim_value.amount)
    for source_value in source_values:
        if claim_value.percent and not source_value.percent:
            continue
        tolerance = claim_value.tolerance + 1e-9 * max(target, 1.0)
        if abs(abs(source_value.amount) - target) <= tolerance:
            return True
    return False


_QUARTER = re.compile(r"\bQ([1-4])\b", re.IGNORECASE)
_FISCAL_YEAR = re.compile(
    r"\b(?:FY\s?'?|fiscal(?:\s+year)?\s*|fiscal_year\s*[=:]\s*)(\d{4}|\d{2})\b", re.IGNORECASE
)


def _fiscal_years(text: str) -> set[str]:
    return {year if len(year) == 4 else f"20{year}" for year in _FISCAL_YEAR.findall(text)}


def _missing_periods(claim_text: str, source_text: str) -> list[str]:
    """Return claimed quarters/fiscal years that the source never mentions.

    Quarter and fiscal year are checked separately, so "Q2 fiscal 2027" is supported by
    "fiscal_year=2027; fiscal_period=Q2" or "Q2 FY2027".
    """
    missing: list[str] = []
    source_quarters = {quarter for quarter in _QUARTER.findall(source_text)}
    for quarter in dict.fromkeys(_QUARTER.findall(claim_text)):
        if quarter not in source_quarters:
            missing.append(f"q{quarter}")
    source_years = _fiscal_years(source_text)
    for year in sorted(_fiscal_years(claim_text)):
        if year not in source_years:
            missing.append(f"fy{year}")
    return missing


_QUOTE_CHARS = str.maketrans(
    {"\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'", "\u2013": "-", "\u2014": "-"}
)
_ELLIPSIS = re.compile(r"\.\.\.+|\u2026(?:\[[^\]]*\])?")


def _normalize_for_quote(text: str) -> str:
    text = text.translate(_QUOTE_CHARS).casefold()
    return re.sub(r"\s+", " ", text).strip()


def _quote_found(quote: str, source_text: str) -> bool:
    """Find an evidence quote in source text, allowing elided spans marked with an ellipsis.

    Surrounding quote marks are ignored, and each fragment between ellipses must appear in the
    source in order. Fragments shorter than three words are too generic to prove anything alone.
    """
    source = _normalize_for_quote(source_text)
    fragments = [
        fragment.strip(" \"'.,;:") for fragment in _ELLIPSIS.split(_normalize_for_quote(quote))
    ]
    fragments = [fragment for fragment in fragments if fragment]
    if not fragments:
        return True
    if sum(len(fragment.split()) for fragment in fragments) < 3:
        return False
    position = 0
    for fragment in fragments:
        found = source.find(fragment, position)
        if found < 0:
            return False
        position = found + len(fragment)
    return True


_STOPWORDS = frozenset(
    "the a an and or of to in for on with by at as is are was were be been its it this that "
    "from our their his her we they has have had will would can could may more most than "
    "into over about".split()
)
_CONTENT_WORD = re.compile(r"[a-z0-9]+(?:[.,][0-9]+)?")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_TRUNCATION = re.compile(r"…\[[^\]]*\]$")
REPAIR_MIN_COVERAGE = 0.75
REPAIR_MIN_WORDS = 4


def _content_words(text: str) -> set[str]:
    words = _CONTENT_WORD.findall(_normalize_for_quote(text))
    return {word for word in words if word not in _STOPWORDS}


def _repair_quote(quote: str, source_text: str) -> str | None:
    """Return the real source sentence a paraphrased quote was taken from, if there is one.

    Models often reword a source sentence slightly ("beat the Zacks Consensus Estimate" becomes
    "surpassing the consensus estimate"). Accept the claim only when one sentence in the cited
    source contains at least 75% of the quote's content words (and at least four of them) and
    every number in the quote; the claim then carries that exact sentence as its evidence.
    Invented quotes share few words with any source sentence and stay rejected.
    """
    wanted = _content_words(quote)
    if len(wanted) < REPAIR_MIN_WORDS:
        return None
    numbers = set(_NUMBER.findall(quote))
    best, best_coverage = None, 0.0
    for sentence in _SENTENCE_SPLIT.split(source_text):
        sentence = _TRUNCATION.sub("", sentence.strip())
        if len(sentence.split()) < 5:
            continue
        if not numbers <= set(_NUMBER.findall(sentence)):
            continue
        matched = wanted & _content_words(sentence)
        coverage = len(matched) / len(wanted)
        if len(matched) >= REPAIR_MIN_WORDS and coverage > best_coverage:
            best, best_coverage = sentence, coverage
    if best is None or best_coverage < REPAIR_MIN_COVERAGE:
        return None
    return best[:220]


def _claim_support_reason(claim: Claim, source_text: str, registry: SourceRegistry) -> str | None:
    lowered_claim = claim.text.casefold()
    market_terms = (
        "market cap",
        "market capitalization",
        "stock price",
        "share price",
        "current price",
    )
    if any(term in lowered_claim for term in market_terms):
        source_ids = set(claim.source_ids or [])
        source_records = [source for source in registry.all() if source.id in source_ids]
        if not source_records or any(source.published_date is None for source in source_records):
            return "market-data claim cites an undated source; market cap and stock-price facts require dated sources"

    if not source_text.strip():
        return None

    claim_values = _claim_values(claim.text)
    if claim_values:
        source_values = _source_values(source_text)
        missing_values = [
            value.label for value in claim_values if not _value_supported(value, source_values)
        ]
        if missing_values:
            return "value mismatch: missing value(s) in source text: " + ", ".join(
                sorted(set(missing_values))
            )

    missing_periods = _missing_periods(claim.text, source_text)
    if missing_periods:
        return "period mismatch: missing fiscal period(s) in source text: " + ", ".join(
            sorted(set(missing_periods))
        )

    return None


def check_citations(draft: BriefDraft, registry: SourceRegistry) -> CitationReport:
    """Pass claims citing known source IDs and report missing or unknown IDs."""
    known = {source.id for source in registry.all()}
    passed: list[Claim] = []
    failed: list[FailedClaim] = []
    cited: set[str] = set()
    repaired: dict[tuple[str, int], Claim] = {}
    for section, index, claim in _claims(draft):
        source_ids = list(claim.source_ids or [])
        if not source_ids:
            failed.append(FailedClaim(section, index, claim, "no source_ids"))
            continue

        unknown = [source_id for source_id in source_ids if source_id not in known]
        if unknown:
            failed.append(
                FailedClaim(section, index, claim, "unknown source ID(s): " + ", ".join(unknown))
            )
            continue

        source_text = _source_text_for(claim, registry)
        reason = _claim_support_reason(claim, source_text, registry)
        if reason is not None:
            failed.append(FailedClaim(section, index, claim, reason))
            continue

        cited_sources = [source for source in registry.all() if source.id in set(source_ids)]
        # SEC metric sources are structured numbers, not prose: a claim (or its quote) that
        # states their figures is verified by value, since there is no sentence to quote.
        cites_only_sec = bool(cited_sources) and all(
            source.kind in {"sec", "sec_filing"} for source in cited_sources
        )
        quote_values = _claim_values(claim.evidence_quote)
        numbers_verified_by_sec = cites_only_sec and (
            bool(_claim_values(claim.text))
            or (
                bool(quote_values)
                and all(
                    _value_supported(value, _source_values(source_text)) for value in quote_values
                )
            )
        )
        if not numbers_verified_by_sec and not _quote_found(claim.evidence_quote, source_text):
            repaired_quote = _repair_quote(claim.evidence_quote, source_text)
            if repaired_quote is None:
                failed.append(
                    FailedClaim(
                        section, index, claim, "evidence_quote not found in cited source text"
                    )
                )
                continue
            claim = claim.model_copy(update={"evidence_quote": repaired_quote})
            repaired[(section, index)] = claim

        passed.append(claim)
        cited.update(source_ids)
    return CitationReport(
        passed_claims=passed,
        failed_claims=failed,
        valid_source_ids=[source.id for source in registry.all()],
        cited_source_ids=[source.id for source in registry.all() if source.id in cited],
        repaired_claims=repaired,
    )


def _recency_sensitive(text: str) -> bool:
    lowered = text.casefold()
    return any(
        token in lowered
        for token in (
            "latest",
            "current",
            "new",
            "recent",
            "quarter",
            "fy",
            "fiscal",
            "earnings",
            "revenue",
            "q1",
            "q2",
            "q3",
            "q4",
            "today",
            "this year",
            "202",
        )
    )


def _stale_or_undated_flag(record: AccountRecord) -> list[str]:
    flags: list[str] = []
    source_by_id = {source.id: source for source in record.sources}
    for section in (
        record.summary,
        *record.financial_highlights,
        *record.signals,
        *record.priorities,
        *record.pain_points,
        *record.talking_points,
    ):
        if section is None:
            continue
        if not _recency_sensitive(section.text):
            continue
        stale_sources = [
            source_by_id[source_id]
            for source_id in section.source_ids
            if source_by_id.get(source_id)
            and source_by_id[source_id].freshness_status in {"stale", "undated"}
        ]
        if stale_sources:
            names = ", ".join(source.id for source in stale_sources)
            flags.append(
                f"Claim uses stale or undated source(s): {names}. Avoid describing the claim as current."
            )
    return flags


def to_account_record(
    draft: BriefDraft,
    report: CitationReport,
    registry: SourceRegistry,
    *,
    model: str,
    flags: list[str] | None = None,
) -> AccountRecord:
    """Drop failed claims and attach only cited registry sources."""
    failed_locations = {(item.section, item.index) for item in report.failed_claims}
    out_flags = list(flags or [])
    out_flags.extend(
        f"Dropped {item.section} claim: {item.claim.text} ({item.reason})"
        for item in report.failed_claims
    )

    def keep(section: str, claims: list[Claim]) -> list[Claim]:
        return [
            report.repaired_claims.get((section, index), claim)
            for index, claim in enumerate(claims)
            if (section, index) not in failed_locations
        ]

    summary = (
        None
        if ("summary", 0) in failed_locations
        else report.repaired_claims.get(("summary", 0), draft.summary)
    )
    sources = [source for source in registry.all() if source.id in report.cited_source_ids]
    record = AccountRecord(
        company_name=draft.company_name,
        domain=draft.domain,
        industry=draft.industry,
        is_public=draft.is_public,
        summary=summary,
        financial_highlights=keep("financial_highlights", draft.financial_highlights),
        signals=keep("signals", draft.signals),
        priorities=keep("priorities", draft.priorities),
        pain_points=keep("pain_points", draft.pain_points),
        talking_points=keep("talking_points", draft.talking_points),
        sources=sources,
        flags=out_flags,
        generated_at=datetime.now(UTC),
        model=model,
    )
    freshness_flags = list(dict.fromkeys(_stale_or_undated_flag(record)))
    record.flags.extend(flag for flag in freshness_flags if flag not in record.flags)
    return record
