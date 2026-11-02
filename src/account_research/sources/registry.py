"""URL-deduplicated source registry scoped to one research run."""

from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import HttpUrl, TypeAdapter

from account_research.schemas.account import Source

_TRACKING_KEYS = {"fbclid", "gclid", "dclid", "mc_cid", "mc_eid"}
_KIND_RANK = {"search_result": 0, "web_page": 1, "sec_filing": 2}
_URL_ADAPTER: TypeAdapter[HttpUrl] = TypeAdapter(HttpUrl)


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

    def __init__(self) -> None:
        self._by_url: dict[str, Source] = {}
        self._next_id = 1

    def record(self, url: str, title: str, kind: str) -> Source:
        """Record a source or return its existing record when the URL is known."""
        normalized = normalize_url(url)
        existing = self._by_url.get(normalized)
        if existing is not None:
            if _KIND_RANK[kind] > _KIND_RANK[existing.kind]:
                existing = existing.model_copy(update={"kind": kind, "title": title})
                self._by_url[normalized] = existing
            return existing
        source = Source(
            id=f"S{self._next_id}",
            url=_URL_ADAPTER.validate_python(normalized),
            title=title,
            retrieved_at=datetime.now(UTC),
            kind=kind,  # type: ignore[arg-type]
        )
        self._next_id += 1
        self._by_url[normalized] = source
        return source

    def contains(self, url: str) -> bool:
        """Return whether a normalized URL has already been recorded."""
        return normalize_url(url) in self._by_url

    def all(self) -> list[Source]:
        """Return records in the order they were first seen."""
        return list(self._by_url.values())
