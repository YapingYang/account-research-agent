"""Shareable brief renderings."""

from datetime import UTC, datetime

from account_research.interfaces.brief_export import email_body, email_subject, render_brief_html
from account_research.schemas.account import AccountRecord
from account_research.sources.registry import SourceRegistry


def _record() -> AccountRecord:
    registry = SourceRegistry()
    source = registry.record("https://example.com/a?x=1&y=2", "Q2 <recap>", "web_page", content="t")
    return AccountRecord(
        company_name="Snowflake Inc.",
        domain="snowflake.com",
        is_public=True,
        summary={"text": "Revenue grew & margins rose.", "source_ids": ["S1"]},
        signals=[{"text": "CFO stressed discipline.", "source_ids": ["S1"]}],
        sources=[source],
        flags=[],
        generated_at=datetime(2026, 11, 17, tzinfo=UTC),
        model="test",
    )


def test_html_brief_escapes_text_and_links_citations() -> None:
    html = render_brief_html(_record())
    assert "Revenue grew &amp; margins rose." in html
    assert "Q2 &lt;recap&gt;" in html
    assert 'href="https://example.com/a?x=1&amp;y=2"' in html
    assert "<h2>Signals</h2>" in html
    assert "<h2>Priorities</h2>" not in html


def test_email_uses_numbered_citations() -> None:
    record = _record()
    assert email_subject(record) == "Account brief: Snowflake Inc."
    body = email_body(record)
    assert "Revenue grew & margins rose. [1]" in body
    assert "- CFO stressed discipline. [1]" in body
    assert "[1] Q2 <recap>: https://example.com/a?x=1&y=2" in body
