"""Deterministic budget policy used by rental-search callers."""

from __future__ import annotations

from math import ceil


def price_range_for_budget(
    budget_yuan: int, *, shared_rent: bool = False
) -> tuple[int, int]:
    """Return the search range implied by a customer's monthly budget.

    The lower bound is half of the stated budget for ordinary rentals.  The
    upper allowance is 25% of budget, with a 200-yuan floor and 500-yuan cap.
    A shared-rent request deliberately has no lower-price bound.
    """
    if budget_yuan <= 0:
        raise ValueError("budget_yuan must be positive")
    upper_allowance = min(500, max(200, ceil(budget_yuan * 0.25)))
    return (0 if shared_rent else budget_yuan // 2, budget_yuan + upper_allowance)
