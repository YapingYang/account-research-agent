"""Parse SEC XBRL metric sources and turn the latest ones into cited brief claims."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from account_research.schemas.account import BriefDraft, Claim

_METRIC = re.compile(
    r"^(?P<name>\w+): (?P<value>-?[\d.eE+]+) (?P<unit>\S+); period=(?P<period>[^;]+); "
    r"form=(?P<form>[^;]+); fiscal_year=(?P<fy>[^;]+); fiscal_period=(?P<fp>\w+)"
)
METRIC_LABELS = {
    "Revenues": "Revenue",
    "NetIncomeLoss": "Net income (loss)",
    "Assets": "Total assets",
    "Liabilities": "Total liabilities",
    "StockholdersEquity": "Stockholders' equity",
    "EarningsPerShareBasic": "EPS (basic)",
    "EarningsPerShareDiluted": "EPS (diluted)",
    "CommonStockSharesOutstanding": "Shares outstanding",
    "OperatingIncomeLoss": "Operating income (loss)",
    "CashAndCashEquivalentsAtCarryingValue": "Cash and equivalents",
}
# Figures worth a sentence in a seller's brief, in the order they should appear.
_HIGHLIGHT_ORDER = (
    "Revenues",
    "NetIncomeLoss",
    "OperatingIncomeLoss",
    "EarningsPerShareDiluted",
    "EarningsPerShareBasic",
    "CashAndCashEquivalentsAtCarryingValue",
    "Assets",
    "Liabilities",
)
_MAX_HIGHLIGHTS = 4


@dataclass(frozen=True)
class SecMetric:
    """One XBRL fact registered as a source."""

    source_id: str
    url: str
    name: str
    value: float
    unit: str
    period_end: str
    form: str
    fiscal_year: str
    fiscal_period: str
    raw_line: str

    @property
    def label(self) -> str:
        if self.name == "NetIncomeLoss":
            return "Net loss" if self.value < 0 else "Net income"
        if self.name == "OperatingIncomeLoss":
            return "Operating loss" if self.value < 0 else "Operating income"
        return METRIC_LABELS.get(self.name, self.name)

    @property
    def period_label(self) -> str:
        if self.fiscal_period == "FY":
            return f"FY{self.fiscal_year}"
        return f"{self.fiscal_period} FY{self.fiscal_year}"


def format_amount(value: float, unit: str) -> str:
    """Format an XBRL value the way a seller would say it: $8.69B, -$1.40/share, 352.5M shares."""
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    if unit == "USD/shares":
        return f"{sign}${magnitude:,.2f}/share"
    for threshold, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if magnitude >= threshold:
            scaled = f"{magnitude / threshold:,.2f}{suffix}"
            break
    else:
        scaled = f"{magnitude:,.0f}"
    if unit == "USD":
        return f"{sign}${scaled}"
    return f"{sign}{scaled} {unit}"


def parse_sec_metric(source: Any) -> SecMetric | None:
    """Return the metric a SEC source holds, or None for filings and other sources."""
    if source.kind not in {"sec", "sec_filing"}:
        return None
    match = _METRIC.match(source.content or "")
    if not match:
        return None
    return SecMetric(
        source_id=source.id,
        url=str(source.url),
        name=match["name"],
        value=float(match["value"]),
        unit=match["unit"],
        period_end=match["period"],
        form=match["form"],
        fiscal_year=match["fy"],
        fiscal_period=match["fp"],
        raw_line=f"{match['name']}: {match['value']} {match['unit']}",
    )


def _highlight_text(metric: SecMetric) -> str:
    # Absolute values: the label already says "loss", and the checker ignores sign.
    amount = format_amount(abs(metric.value), metric.unit)
    if metric.name in {"Assets", "Liabilities", "CashAndCashEquivalentsAtCarryingValue"}:
        return (
            f"{metric.label} were {amount} as of {metric.period_end} "
            f"({metric.period_label}, SEC {metric.form})."
        )
    if metric.unit == "USD/shares":
        sign = "-" if metric.value < 0 else ""
        return f"{metric.label} was {sign}{amount} for {metric.period_label} (SEC {metric.form})."
    return f"{metric.label} was {amount} for {metric.period_label} (SEC {metric.form})."


def add_sec_highlights(draft: BriefDraft, sources: list[Any]) -> BriefDraft:
    """Add the latest reported SEC figures to financial highlights when the draft lacks them.

    The figures come straight from EDGAR, so a brief always carries the company's reported
    numbers even when the model writes only about web sources. Each added claim cites its
    metric source and still goes through the normal citation checks.
    """
    metrics = [metric for source in sources if (metric := parse_sec_metric(source))]
    if not metrics:
        return draft
    latest_period = max(metric.period_end for metric in metrics)
    by_name = {
        metric.name: metric
        for metric in metrics
        if metric.period_end == latest_period and metric.form in {"10-Q", "10-K"}
    }
    already_cited = {
        source_id
        for claim in [draft.summary, *draft.financial_highlights]
        if claim is not None
        for source_id in claim.source_ids
    }
    added: list[Claim] = []
    for name in _HIGHLIGHT_ORDER:
        if len(added) >= _MAX_HIGHLIGHTS:
            break
        metric = by_name.get(name)
        if metric is None or metric.source_id in already_cited:
            continue
        if name == "EarningsPerShareBasic" and "EarningsPerShareDiluted" in by_name:
            continue
        added.append(
            Claim(
                text=_highlight_text(metric),
                source_ids=[metric.source_id],
                evidence_quote=metric.raw_line,
            )
        )
    if not added:
        return draft
    return draft.model_copy(update={"financial_highlights": [*added, *draft.financial_highlights]})
