"""Small mutable call budgets shared by per-run tools."""

from dataclasses import dataclass, field

BUDGET_REACHED = "Budget reached. Draft and call submit_draft now."
SEC_BUDGET_REACHED = (
    "SEC call budget reached. Do not call SEC tools again. Use the SEC data you already have and "
    "continue with web_search for leadership commentary and news, or draft if research is done."
)
PAGE_BUDGET_REACHED = (
    "Page fetch budget reached. Do not call fetch_page again. Continue with web_search results "
    "you have, or draft if research is done."
)


@dataclass
class ToolCallBudget:
    """Allow a fixed number of calls to a tool family during one run."""

    maximum: int
    calls: int = field(default=0, init=False)
    successful_calls: int = field(default=0, init=False)
    seen: set[str] = field(default_factory=set)

    def consume(self) -> bool:
        """Increment the budget counter, returning false after it is exhausted."""
        if self.calls >= self.maximum:
            return False
        self.calls += 1
        return True

    def refund(self, calls: int) -> None:
        """Return calls made on the agent's behalf (e.g. a prefetch) to the budget."""
        self.calls = max(0, self.calls - calls)
