"""Conservative rule baseline: explicit phrases only, never fraud inference."""

from __future__ import annotations

from xps.ai.schemas import Claim, TextAnalysisResult

_TYPE_MARKERS = (
    ("rental", ("出租", "租赁", "日租", "月租")),
    ("wanted", ("求购", "收一台", "高价回收")),
    ("deposit", ("定金", "订金", "标价非售价", "价格不是实际价格")),
    ("accessory", ("仅配件", "空盒", "保护壳", "机身盖")),
)
_FAULT_MARKERS = ("黑屏", "不开机", "故障", "维修过", "进水", "功能异常")
_TRANSACTION_MARKERS = ("微信交易", "线下转账", "先款")


def _first(fields: dict[str, str], marker: str) -> tuple[str, str] | None:
    for name, text in fields.items():
        if marker in text:
            return name, marker
    return None


def analyze_rules(title: str | None, description: str | None, *, is_auction: bool = False) -> TextAnalysisResult:
    fields = {"title": title or "", "description": description or ""}
    listing_type = "auction" if is_auction else "physical_product"
    for candidate, markers in _TYPE_MARKERS:
        if any(_first(fields, marker) for marker in markers):
            listing_type = candidate
            break
    state = []
    transaction = []
    for marker in _FAULT_MARKERS:
        found = _first(fields, marker)
        if found:
            state.append(Claim(tag="functional_issue_claim", source_field=found[0], evidence=found[1]))
    for marker in _TRANSACTION_MARKERS:
        found = _first(fields, marker)
        if found:
            transaction.append(Claim(tag="off_platform_payment_requested", source_field=found[0], evidence=found[1]))
    price = []
    for marker in ("标价非售价", "价格不是实际价格", "定金", "订金"):
        found = _first(fields, marker)
        if found:
            price.append(Claim(tag="price_placeholder", source_field=found[0], evidence=found[1]))
    return TextAnalysisResult(
        listing_type=listing_type,
        state_claims=state,
        price_flags=price,
        transaction_flags=transaction,
        unknowns=["repair_history"] if not state else [],
        model_uncertain=True,
    )
