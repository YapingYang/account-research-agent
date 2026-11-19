"""Shareable renderings of an approved brief: standalone HTML and a plain-text email."""

from __future__ import annotations

from html import escape

from account_research.schemas.account import AccountRecord, Claim

SECTIONS = (
    ("Financial highlights", "financial_highlights"),
    ("Signals", "signals"),
    ("Priorities", "priorities"),
    ("Pain points", "pain_points"),
    ("Talking points", "talking_points"),
)
_STYLE = " ".join(
    (
        'body{font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;',
        "color:#1f2328;max-width:760px;margin:32px auto;padding:0 16px;background:#fff}",
        "h1{font-size:26px;margin:0 0 4px}",
        "h2{font-size:17px;margin:28px 0 8px;border-bottom:1px solid #d8dee4;padding-bottom:4px}",
        ".meta{color:#59636e;margin:0 0 16px}",
        ".summary{background:#f1f6ff;border-left:4px solid #2f6fde;padding:12px 16px;",
        "border-radius:4px}ul{padding-left:20px}li{margin:6px 0}a{color:#2f6fde}",
        "sup a{text-decoration:none;font-size:12px}.sources li{font-size:13px;color:#3d444d}",
        ".foot{margin-top:32px;font-size:12px;color:#59636e}",
    )
)


def _cite_html(claim: Claim, record: AccountRecord) -> str:
    urls = {source.id: str(source.url) for source in record.sources}
    links = [
        f'<a href="{escape(urls[source_id])}">{escape(source_id)}</a>'
        if source_id in urls
        else escape(source_id)
        for source_id in claim.source_ids
    ]
    return f" <sup>[{', '.join(links)}]</sup>" if links else ""


def render_brief_html(record: AccountRecord) -> str:
    """A self-contained HTML brief suitable for attaching to an email or printing to PDF."""
    meta = " · ".join(
        part
        for part in (
            record.industry,
            record.domain,
            "Public company" if record.is_public else None,
            f"Generated {record.generated_at:%b %d, %Y}",
        )
        if part
    )
    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>{escape(record.company_name)} account brief</title>",
        f"<style>{_STYLE}</style></head><body>",
        f"<h1>{escape(record.company_name)}</h1><p class='meta'>{escape(meta)}</p>",
    ]
    if record.summary:
        summary = escape(record.summary.text) + _cite_html(record.summary, record)
        parts.append(f"<p class='summary'>{summary}</p>")
    for title, field in SECTIONS:
        claims: list[Claim] = getattr(record, field)
        if not claims:
            continue
        items = "".join(f"<li>{escape(c.text)}{_cite_html(c, record)}</li>" for c in claims)
        parts.append(f"<h2>{escape(title)}</h2><ul>{items}</ul>")
    if record.sources:
        items = "".join(
            f"<li><strong>{escape(s.id)}</strong> — "
            f"<a href='{escape(str(s.url))}'>{escape(s.title)}</a> "
            f"({s.published_date.isoformat() if s.published_date else 'undated'})</li>"
            for s in record.sources
        )
        parts.append(f"<h2>Sources</h2><ul class='sources'>{items}</ul>")
    parts.append(
        "<p class='foot'>Every claim is cited and was checked against its source "
        "before approval.</p>"
        "</body></html>"
    )
    return "".join(parts)


def email_subject(record: AccountRecord) -> str:
    return f"Account brief: {record.company_name}"


def email_body(record: AccountRecord) -> str:
    """Plain-text brief with numbered citations, ready to paste into an email."""
    numbers = {source.id: index for index, source in enumerate(record.sources, start=1)}

    def cite(claim: Claim) -> str:
        refs = [f"[{numbers[source_id]}]" for source_id in claim.source_ids if source_id in numbers]
        return f" {''.join(refs)}" if refs else ""

    lines = ["Hi team,", "", f"Here is the account brief for {record.company_name}.", ""]
    if record.summary:
        lines += [f"{record.summary.text}{cite(record.summary)}", ""]
    for title, field in SECTIONS:
        claims: list[Claim] = getattr(record, field)
        if not claims:
            continue
        lines.append(f"{title}:")
        lines += [f"- {claim.text}{cite(claim)}" for claim in claims]
        lines.append("")
    if record.sources:
        lines.append("Sources:")
        lines += [f"[{numbers[s.id]}] {s.title}: {s.url}" for s in record.sources]
    return "\n".join(lines).rstrip() + "\n"
