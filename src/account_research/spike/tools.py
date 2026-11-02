"""Fixture tool used only by the model spike."""


def lookup_company_fact(company: str) -> dict[str, str]:
    """Return a fixed fact for Acme Corp."""
    if "acme" in company.casefold():
        return {"fact": "Acme Corp was founded in 1999", "source_id": "fixture-001"}
    return {"error": "not found"}
