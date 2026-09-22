from __future__ import annotations

from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal

RULE_VERSION = "score-v1"
WEIGHTS = {
    "match": Decimal("0.20"),
    "price": Decimal("0.25"),
    "condition": Decimal("0.20"),
    "integrity": Decimal("0.20"),
    "seller": Decimal("0.15"),
}


def weighted_score(subscores: Mapping[str, int]) -> int | None:
    if set(subscores) != set(WEIGHTS):
        return None
    value = sum(Decimal(subscores[name]) * weight for name, weight in WEIGHTS.items())
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))
