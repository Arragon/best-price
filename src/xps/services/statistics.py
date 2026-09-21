"""在售报价统计。

口径（指南 §7）：仅使用当前 run、去重后、明确匹配商品范围且 price_parse_status=valid
的样本。这些是**采集时刻的公开在售报价**，不是成交价，不含国补/优惠券/议价结果。

分位数算法固定为 **inclusive 线性插值**，与 `statistics.quantiles(n=4,
method='inclusive')` 等价，但全程用 Decimal 实现，避免 float 进入金额路径：
    位置 = (样本数 - 1) * i / 4        i ∈ {1,2,3}
    落在两点之间时按小数部分线性插值
    结果按 ROUND_HALF_UP 取整到分
中位数：奇数取中间值；偶数取中间两值均值（可能落在 .5 分，同样 ROUND_HALF_UP）。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Mapping, Sequence

from xps.services.classify import ANY, ITEM_KIND_MISMATCH, SUSPICIOUS_PRICE
from xps.services.normalize import VALID

# 有效样本少于此值即告警。这是 MVP 阈值，不是统计学保证（指南 §7）。
MIN_SAMPLE_THRESHOLD = 8

PRICE_NOT_VALID = "price_not_valid"

_LOWEST_ITEM_LIMIT = 3
_ONE = Decimal(1)
_ZERO = Decimal(0)
_HALF = Decimal("0.5")
_OUTLIER_K = Decimal("1.5")


@dataclass(frozen=True)
class StatsItem:
    """统计输入：一个 run 内、一个商品的一条去重后记录。"""

    product_id: int
    title: str
    canonical_url: str | None
    price_fen: int | None
    price_status: str
    flags: tuple[str, ...]
    item_kind: str | None
    excluded: bool
    exclusion_reasons: tuple[str, ...]
    needs_review: bool


@dataclass(frozen=True)
class LowestItem:
    product_id: int
    title: str
    canonical_url: str | None
    price_fen: int


@dataclass(frozen=True)
class PriceStats:
    run_id: str
    item_kind: str
    currency: str
    raw_count: int
    distinct_count: int
    eligible_count: int
    excluded_count: int
    excluded_by_reason: Mapping[str, int]
    needs_review_count: int
    suspicious_price_count: int
    min_fen: int | None
    p25_fen: int | None
    median_fen: int | None
    p75_fen: int | None
    max_fen: int | None
    lowest_items: tuple[LowestItem, ...]
    insufficient_sample: bool
    sample_quality: tuple[str, ...]
    partial: bool


def _round_fen(value: Decimal) -> int:
    return int(value.quantize(_ONE, rounding=ROUND_HALF_UP))


def _quantiles_inclusive(values: Sequence[int], parts: int = 4) -> list[Decimal]:
    """inclusive 线性插值分位数。要求 len(values) >= 2。"""
    ordered = sorted(values)
    count = len(ordered)
    result: list[Decimal] = []
    for index in range(1, parts):
        position = Decimal((count - 1) * index) / Decimal(parts)
        lower = int(position)
        fraction = position - Decimal(lower)
        low_value = Decimal(ordered[lower])
        if fraction == _ZERO:
            result.append(low_value)
            continue
        high_value = Decimal(ordered[lower + 1])
        result.append(low_value + fraction * (high_value - low_value))
    return result


def _median(values: Sequence[int]) -> Decimal:
    ordered = sorted(values)
    count = len(ordered)
    middle = count // 2
    if count % 2 == 1:
        return Decimal(ordered[middle])
    return (Decimal(ordered[middle - 1]) + Decimal(ordered[middle])) * _HALF


def _split_outliers(candidates: list[StatsItem]) -> tuple[list[StatsItem], list[StatsItem]]:
    """IQR 下界围栏。仅在样本足够时启用——小样本 IQR 不稳定，不该自动删数据。"""
    if len(candidates) < MIN_SAMPLE_THRESHOLD:
        return candidates, []
    quartiles = _quantiles_inclusive([entry.price_fen for entry in candidates])  # type: ignore[misc]
    q1, q3 = quartiles[0], quartiles[2]
    fence = q1 - _OUTLIER_K * (q3 - q1)
    kept = [entry for entry in candidates if Decimal(entry.price_fen) >= fence]  # type: ignore[arg-type]
    flagged = [entry for entry in candidates if Decimal(entry.price_fen) < fence]  # type: ignore[arg-type]
    return kept, flagged


def compute_stats(
    *,
    run_id: str,
    items: Sequence[StatsItem],
    raw_count: int,
    item_kind: str = ANY,
    min_sample: int = MIN_SAMPLE_THRESHOLD,
    pages_requested: int = 1,
    pages_fetched: int = 1,
    auth_mode: str = "unknown",
    status: str = "succeeded",
) -> PriceStats:
    reasons: Counter[str] = Counter()
    excluded_count = 0
    review_count = 0
    candidates: list[StatsItem] = []

    for entry in items:
        if entry.excluded:
            excluded_count += 1
            reasons.update(entry.exclusion_reasons or ("excluded",))
            continue
        if item_kind != ANY:
            if entry.item_kind is None:
                # 无法从文本确认配置 → 待核验，不是排除；不计入 excluded_by_reason
                review_count += 1
                continue
            if entry.item_kind != item_kind:
                excluded_count += 1
                reasons[ITEM_KIND_MISMATCH] += 1
                continue
        if entry.needs_review:
            review_count += 1
            continue
        if entry.price_status != VALID or entry.price_fen is None:
            excluded_count += 1
            reasons[PRICE_NOT_VALID] += 1
            continue
        candidates.append(entry)

    candidates, outliers = _split_outliers(candidates)
    excluded_count += len(outliers)
    if outliers:
        reasons[SUSPICIOUS_PRICE] += len(outliers)

    values = sorted(entry.price_fen for entry in candidates)  # type: ignore[misc]
    eligible = len(values)

    if eligible == 0:
        low = p25 = middle = p75 = high = None
    elif eligible == 1:
        low = middle = high = values[0]
        p25 = p75 = None
    else:
        quartiles = _quantiles_inclusive(values)
        low, high = values[0], values[-1]
        p25, p75 = _round_fen(quartiles[0]), _round_fen(quartiles[2])
        middle = _round_fen(_median(values))

    lowest = tuple(
        LowestItem(
            product_id=entry.product_id,
            title=entry.title,
            canonical_url=entry.canonical_url,
            price_fen=entry.price_fen,  # type: ignore[arg-type]
        )
        for entry in sorted(
            candidates, key=lambda candidate: (candidate.price_fen, candidate.product_id)
        )[:_LOWEST_ITEM_LIMIT]
    )

    quality: list[str] = []
    if eligible < min_sample:
        quality.append("insufficient_sample")
    if len({entry.item_kind for entry in candidates}) > 1:
        # 套机与单机身混在同一份报价分布里没有意义，调用方选了 any 就必须看到这条限制
        quality.append("mixed_item_kinds")
    if pages_requested <= 1:
        quality.append("single_page_only")
    if pages_fetched < pages_requested:
        quality.append(f"partial_pages:{pages_fetched}/{pages_requested}")
    if auth_mode == "guest":
        quality.append("guest_auth")
    elif auth_mode == "unknown":
        quality.append("unknown_auth")
    if status != "succeeded":
        quality.append(f"status_{status}")

    return PriceStats(
        run_id=run_id,
        item_kind=item_kind,
        currency="CNY",
        raw_count=raw_count,
        distinct_count=len(items),
        eligible_count=eligible,
        excluded_count=excluded_count,
        excluded_by_reason=dict(reasons),
        needs_review_count=review_count,
        suspicious_price_count=len(outliers),
        min_fen=low,
        p25_fen=p25,
        median_fen=middle,
        p75_fen=p75,
        max_fen=high,
        lowest_items=lowest,
        insufficient_sample=eligible < min_sample,
        sample_quality=tuple(quality),
        partial=pages_fetched < pages_requested,
    )
