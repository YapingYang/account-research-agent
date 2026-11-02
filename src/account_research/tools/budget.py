"""Small mutable call budgets shared by per-run tools."""

from dataclasses import dataclass

BUDGET_REACHED = "Budget reached. Draft and call submit_draft now."


@dataclass
class ToolCallBudget:
    """Allow a fixed number of calls to a tool family during one run."""

    maximum: int
    calls: int = 0

    def consume(self) -> bool:
        """Increment the budget counter, returning false after it is exhausted."""
        if self.calls >= self.maximum:
            return False
        self.calls += 1
        return True
