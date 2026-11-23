"""Cities Growing hooks: the urban population in words and a note once the years become projections."""
from __future__ import annotations

LAST_OBSERVED = 2025


def people(total: float) -> str:
    """Return a population as words, such as '3.6 billion' or '850 million'. Tested by test_v3_recipes.py::test_cities_growing_hook."""
    if total >= 1e9:
        return f"{total / 1e9:.1f} billion"
    if total >= 1e6:
        return f"{total / 1e6:.0f} million"
    return f"{total:,.0f}"


def values(t, ctx, vals) -> dict:
    """Add the urban population in words and a projection note after the last observed year. Tested by test_v3_recipes.py::test_cities_growing_hook."""
    year = vals.get("year") or (t.date.year if t.date else LAST_OBSERVED)
    return {"people": people(vals.get("weight_sum", 0.0)), "projected": " (projected)" if year > LAST_OBSERVED else ""}
