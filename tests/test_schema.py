"""Account schema tests."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from account_research.schemas.account import AccountRecord, Claim, Source


def test_valid_record() -> None:
    record = AccountRecord(
        company_name="Acme",
        summary="Example",
        generated_at=datetime.now(UTC),
        model="test",
        financial_highlights=[{"text": "Revenue grew", "source_urls": ["https://example.com"]}],
    )
    assert record.company_name == "Acme"


def test_claim_requires_source() -> None:
    with pytest.raises(ValidationError):
        Claim(text="Unsupported", source_urls=[])


def test_source_kind_is_restricted() -> None:
    with pytest.raises(ValidationError):
        Source(
            url="https://example.com",
            title="Example",
            retrieved_at=datetime.now(UTC),
            kind="other",
        )  # type: ignore[arg-type]
