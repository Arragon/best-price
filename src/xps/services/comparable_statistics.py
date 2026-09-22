"""Arithmetic for an explicitly selected, deduplicated comparable set."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Any


def _round(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _percentile(values: Sequence[int], fraction: Decimal) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = Decimal(len(ordered) - 1) * fraction
    lower = int(position)
    remainder = position - lower
    if not remainder:
        return ordered[lower]
    return _round(Decimal(ordered[lower]) + remainder * (ordered[lower + 1] - ordered[lower]))


def comparable_stats(items: Sequence[dict[str, Any]], *, min_sample: int = 10) -> dict[str, Any]:
    values = [int(item["price_fen"]) for item in items]
    return {
        "sample_size": len(values),
        "min_fen": min(values) if values else None,
        "p25_fen": _percentile(values, Decimal("0.25")),
        "median_fen": _percentile(values, Decimal("0.5")),
        "p75_fen": _percentile(values, Decimal("0.75")),
        "max_fen": max(values) if values else None,
        "insufficient_sample": len(values) < min_sample,
        "price_semantics": "deduplicated comparable asking prices, not completed sales",
        "items": list(items),
    }
