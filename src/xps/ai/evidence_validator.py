from __future__ import annotations

from xps.ai.schemas import TextAnalysisResult

_CLAIM_FIELDS = ("state_claims", "price_flags", "marketing_flags", "transaction_flags", "contradictions")


def evidence_is_valid(result: TextAnalysisResult, *, title: str, description: str) -> bool:
    sources = {"title": title, "description": description}
    return all(
        claim.evidence in sources[claim.source_field]
        for field in _CLAIM_FIELDS
        for claim in getattr(result, field)
    )
