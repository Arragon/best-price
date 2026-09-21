"""在售报价统计：**纯算术，零筛选**。

口径（指南 §7）：仅使用当前 run、去重后、`price_parse_status=valid` 的样本。
这些是**采集时刻的公开在售报价**，不是成交价，不含国补/优惠券/议价结果。

为什么不做异常值剔除、不做相关性排除：
本服务不替调用方判断哪条商品「可比」。租赁盘、拍卖起拍价、配件、广告位全都留在样本里，
只做算术；同时把 `is_auction` / `is_ad` 的条数与无法解析的价格条数如实报出来，
让调用方（agent）拿着原始商品清单自己判断。`sample_quality` 里恒有 `unfiltered`，
就是为了让任何转述都无法假装这份分布已经清洗过。

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

from xps.services.normalize import VALID

# 有效样本少于此值即告警。这是 MVP 阈值，不是统计学保证（指南 §7）。
MIN_SAMPLE_THRESHOLD = 8

# 未进入算术的条目按解析状态分组上报。这是**说明**，不是排除理由。
UNFILTERED = "unfiltered"

_ONE = Decimal(1)
_ZERO = Decimal(0)
_HALF = Decimal("0.5")
_EXTREME_ITEM_LIMIT = 3


@dataclass(frozen=True)
class StatsItem:
    """统计输入：一个 run 内、一个商品的一条去重后记录。"""

    product_id: int
    title: str
    canonical_url: str | None
    price_fen: int | None
    price_status: str
    is_auction: bool = False
    is_ad: bool = False


@dataclass(frozen=True)
class PricedItem:
    product_id: int
    title: str
    canonical_url: str | None
    price_fen: int


@dataclass(frozen=True)
class PriceStats:
    run_id: str
    currency: str
    raw_count: int
    distinct_count: int
    # 参与算术的条目数：去重后 price_parse_status=valid 的商品
    priced_count: int
    # 未能参与算术的条目数（面议 / 缺价格控件 / 文本无法解析）
    unpriced_count: int
    unpriced_by_status: Mapping[str, int]
    auction_count: int
    ad_count: int
    min_fen: int | None
    p25_fen: int | None
    median_fen: int | None
    p75_fen: int | None
    max_fen: int | None
    lowest_items: tuple[PricedItem, ...]
    highest_items: tuple[PricedItem, ...]
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


def _extremes(
    entries: Sequence[StatsItem], *, reverse: bool
) -> tuple[PricedItem, ...]:
    ordered = sorted(
        (entry for entry in entries if entry.price_fen is not None),
        key=lambda candidate: (candidate.price_fen, candidate.product_id),
        reverse=reverse,
    )
    return tuple(
        PricedItem(
            product_id=entry.product_id,
            title=entry.title,
            canonical_url=entry.canonical_url,
            price_fen=int(entry.price_fen or 0),
        )
        for entry in ordered[:_EXTREME_ITEM_LIMIT]
    )


def compute_stats(
    *,
    run_id: str,
    items: Sequence[StatsItem],
    raw_count: int,
    min_sample: int = MIN_SAMPLE_THRESHOLD,
    pages_requested: int = 1,
    pages_fetched: int = 1,
    auth_mode: str = "unknown",
    status: str = "succeeded",
) -> PriceStats:
    unpriced: Counter[str] = Counter()
    priced: list[StatsItem] = []

    for entry in items:
        if entry.price_status != VALID or entry.price_fen is None:
            unpriced[entry.price_status] += 1
            continue
        priced.append(entry)

    values = sorted(entry.price_fen for entry in priced)  # type: ignore[misc]
    count = len(values)

    if count == 0:
        low = p25 = middle = p75 = high = None
    elif count == 1:
        low = middle = high = values[0]
        p25 = p75 = None
    else:
        quartiles = _quantiles_inclusive(values)
        low, high = values[0], values[-1]
        p25, p75 = _round_fen(quartiles[0]), _round_fen(quartiles[2])
        middle = _round_fen(_median(values))

    quality: list[str] = [UNFILTERED]
    if count < min_sample:
        quality.append("insufficient_sample")
    if sum(1 for entry in items if entry.is_auction):
        quality.append("includes_auction_start_prices")
    if sum(1 for entry in items if entry.is_ad):
        quality.append("includes_promoted_ads")
    if unpriced:
        quality.append("some_prices_unparsed")
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
        currency="CNY",
        raw_count=raw_count,
        distinct_count=len(items),
        priced_count=count,
        unpriced_count=len(items) - count,
        unpriced_by_status=dict(unpriced),
        auction_count=sum(1 for entry in items if entry.is_auction),
        ad_count=sum(1 for entry in items if entry.is_ad),
        min_fen=low,
        p25_fen=p25,
        median_fen=middle,
        p75_fen=p75,
        max_fen=high,
        lowest_items=_extremes(priced, reverse=False),
        highest_items=_extremes(priced, reverse=True),
        insufficient_sample=count < min_sample,
        sample_quality=tuple(quality),
        partial=pages_fetched < pages_requested,
    )
