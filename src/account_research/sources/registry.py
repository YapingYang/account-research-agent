"""URL-deduplicated source registry scoped to one research run."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import HttpUrl, TypeAdapter

from account_research.schemas.account import Source

_TRACKING_KEYS = {"fbclid", "gclid", "dclid", "mc_cid", "mc_eid"}
_KIND_RANK = {"search_result": 0, "web_page": 1, "sec_filing": 2, "sec": 2}
_MAX_SOURCE_CHARS = 20000
_URL_ADAPTER: TypeAdapter[HttpUrl] = TypeAdapter(HttpUrl)


def _date_from_url(url: str) -> date | None:
    """Parse a common YYYY/MM/DD date segment from a source URL."""
    parts = urlsplit(url)
    match = __import__("re").search(r"/(20\d{2})/(0?[1-9]|1[0-2])/([0-3]?\d)(?:/|$)", parts.path)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def normalize_url(url: str) -> str:
    """Normalize URL spelling for stable source deduplication."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if parts.port:
        host = f"{host}:{parts.port}"
    if parts.username:
        auth = parts.username
        if parts.password:
            auth += f":{parts.password}"
        host = f"{auth}@{host}"
    path = parts.path.rstrip("/")
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_") and key.casefold() not in _TRACKING_KEYS
    ]
    query.sort()
    return urlunsplit((scheme, host, path, urlencode(query, doseq=True), ""))


class SourceRegistry:
    """Keep one source record per normalized URL for a single run."""

    def __init__(self, *, freshness_window_days: int = 365) -> None:
        self._by_url: dict[str, Source] = {}
        self._next_id = 1
        self.freshness_window_days = freshness_window_days

    def _freshness_for(self, published_date: date | datetime | None) -> tuple[str, str | None]:
        if published_date is None:
            return ("undated", None)
        if isinstance(published_date, datetime):
            snapshot = published_date.astimezone(UTC).date()
        else:
            snapshot = published_date
        age_days = (datetime.now(UTC).date() - snapshot).days
        if age_days <= self.freshness_window_days:
            return ("fresh", snapshot.isoformat())
        return ("stale", snapshot.isoformat())

    def record(
        self,
        url: str,
        title: str,
        kind: str,
        *,
        published_date: date | datetime | str | None = None,
        content: str | None = None,
        form_type: str | None = None,
        fiscal_period: str | None = None,
        date_source: str = "none",
    ) -> Source:
        """Record a source or return its existing record when the URL is known."""
        normalized = normalize_url(url)
        existing = self._by_url.get(normalized)
        if existing is not None:
            updates: dict[str, object] = {}
            if _KIND_RANK[kind] > _KIND_RANK[existing.kind]:
                updates["kind"] = kind
                updates["title"] = title
            if published_date is not None and existing.published_date is None:
                updates["published_date"] = _coerce_date(published_date)
            if content and content not in existing.content:
                # A later fetch (full page after a search snippet, or a second SEC call) adds text
                # the model has seen; keep it so citation checks can find the quoted evidence.
                merged = f"{existing.content}\n\n{content}" if existing.content else content
                updates["content"] = merged[-_MAX_SOURCE_CHARS:]
            if form_type is not None and existing.form_type is None:
                updates["form_type"] = form_type
            if fiscal_period is not None and existing.fiscal_period is None:
                updates["fiscal_period"] = fiscal_period
            if date_source != "none" and existing.date_source == "none":
                updates["date_source"] = date_source
            if updates:
                existing = existing.model_copy(update=updates)
                self._by_url[normalized] = existing
                if "published_date" in updates:
                    parsed = updates["published_date"]
                    if parsed is not None:
                        updates["freshness_status"] = self._freshness_for(parsed)[0]
            return existing
        parsed_published = _coerce_date(published_date)
        resolved_date_source = date_source
        if parsed_published is None:
            parsed_published = _date_from_url(normalized)
            resolved_date_source = "url" if parsed_published is not None else "none"
        source = Source(
            id=f"S{self._next_id}",
            url=_URL_ADAPTER.validate_python(normalized),
            title=title,
            retrieved_at=datetime.now(UTC),
            kind=kind,
            published_date=parsed_published,
            freshness_status="undated",
            content=content or "",
            form_type=form_type,
            fiscal_period=fiscal_period,
            date_source=resolved_date_source,
        )
        if source.published_date is not None:
            source.freshness_status = self._freshness_for(source.published_date)[0]
        self._next_id += 1
        self._by_url[normalized] = source
        return source

    def contains(self, url: str) -> bool:
        """Return whether a normalized URL has already been recorded."""
        return normalize_url(url) in self._by_url

    def all(self) -> list[Source]:
        """Return records in the order they were first seen."""
        return list(self._by_url.values())


def _coerce_date(value: date | datetime | str | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC).date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text or text.lower() == "undated":
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.date()
        except ValueError:
            continue
    try:
        from datetime import date as _date
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None
