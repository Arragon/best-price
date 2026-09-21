"""价格统计测试。

分位数算法固定为 statistics.quantiles(n=4, method='inclusive')，
下列期望值均为手工推导，不用实现本身去算期望值。

基线数据集（8 件，单位：分）：
    480000 500000 520000 540000 550000 560000 580000 600000
inclusive 法：位置 = i*(n-1)/4 = i*7/4
    P25 → 1.75 → 500000 + 0.75*20000 = 515000
    P50 → 3.50 → 540000 + 0.50*10000 = 545000
    P75 → 5.25 → 560000 + 0.25*20000 = 565000

**零筛选是本模块的核心契约**：曾经这里有一整套规则分类器（配件/租赁/型号错配…）
和 IQR 异常值剔除，靠词表猜品类，换个品类就大面积误杀有效样本。现在样本就是
平台返回的全部有价条目，判断交给调用方。下面带「零筛选」的测试就是防止它悄悄回来。
"""

from __future__ import annotations

import pytest

from xps.services.normalize import AMBIGUOUS, MISSING, VALID
from xps.services.statistics import (
    MIN_SAMPLE_THRESHOLD,
    UNFILTERED,
    PriceStats,
    StatsItem,
    compute_stats,
)

BASELINE_FEN = [480_000, 500_000, 520_000, 540_000, 550_000, 560_000, 580_000, 600_000]


def item(
    price_fen: int | None,
    *,
    product_id: int = 1,
    title: str = "合成商品",
    url: str | None = "https://www.goofish.com/item?id=1",
    price_status: str = VALID,
    is_auction: bool = False,
    is_ad: bool = False,
) -> StatsItem:
    return StatsItem(
        product_id=product_id,
        title=title,
        canonical_url=url,
        price_fen=price_fen,
        price_status=price_status,
        is_auction=is_auction,
        is_ad=is_ad,
    )


def baseline_items() -> list[StatsItem]:
    return [
        item(fen, product_id=index, url=f"https://www.goofish.com/item?id={index}")
        for index, fen in enumerate(BASELINE_FEN, start=1)
    ]


def stats(items, **kwargs) -> PriceStats:
    kwargs.setdefault("raw_count", len(items))
    return compute_stats(run_id="run-1", items=items, **kwargs)


# ---------------------------------------------------------------- 分位数口径固定


def test_quantiles_use_inclusive_linear_interpolation() -> None:
    result = stats(baseline_items())

    assert result.min_fen == 480_000
    assert result.p25_fen == 515_000
    assert result.median_fen == 545_000
    assert result.p75_fen == 565_000
    assert result.max_fen == 600_000


def test_all_amounts_are_int_fen_never_float() -> None:
    """§6：金额一律人民币分 INTEGER，不使用 REAL 做交易金额基础类型。"""
    result = stats(baseline_items())

    for value in (
        result.min_fen,
        result.p25_fen,
        result.median_fen,
        result.p75_fen,
        result.max_fen,
    ):
        assert isinstance(value, int)
        assert not isinstance(value, bool)


def test_quantiles_round_half_up_to_whole_fen() -> None:
    """插值可能落在半分上，必须确定性地取整而不是随实现漂移。"""
    items = [item(1, product_id=1), item(2, product_id=2), item(4, product_id=3)]

    result = stats(items)

    # inclusive: P25 位置 0.5 → 1.5 → 四舍五入 2；P75 位置 1.5 → 3.0 → 3
    assert result.p25_fen == 2
    assert result.p75_fen == 3


def test_single_sample_reports_min_median_max_but_no_percentiles() -> None:
    result = stats([item(500_000, product_id=1)])

    assert result.priced_count == 1
    assert result.min_fen == result.median_fen == result.max_fen == 500_000
    assert result.p25_fen is None
    assert result.p75_fen is None


# ---------------------------------------------------------------- 样本门槛


def test_eight_samples_is_not_insufficient() -> None:
    assert MIN_SAMPLE_THRESHOLD == 8
    assert stats(baseline_items()).insufficient_sample is False


def test_seven_samples_is_insufficient() -> None:
    result = stats(baseline_items()[:7])

    assert result.priced_count == 7
    assert result.insufficient_sample is True
    assert "insufficient_sample" in result.sample_quality


def test_insufficient_sample_still_reports_known_values() -> None:
    """§7：样本过少仍展示已知样本，但不产生过度确定的「市场公允价」。"""
    result = stats(baseline_items()[:3])

    assert result.median_fen == 500_000
    assert result.insufficient_sample is True


# ---------------------------------------------------------------- 零筛选契约


def test_result_is_always_marked_unfiltered() -> None:
    """任何转述都无法假装这份分布已经清洗过。"""
    assert UNFILTERED in stats(baseline_items()).sample_quality
    assert UNFILTERED in stats([]).sample_quality


def test_a_cheap_outlier_stays_in_the_arithmetic() -> None:
    """零筛选：¥50 的条目会拉低 min 与分位数，这就是未筛选分布的真实样子。

    旧实现在这里做 IQR 剔除，把它悄悄删掉——那等于替调用方判断它不可比。
    现在它必须留在样本里，并且出现在 lowest_items 里让调用方看见。
    """
    items = baseline_items() + [item(5_000, product_id=99, title="合成 配件")]

    result = stats(items)
    # 9 件：5000 480000 500000 520000 540000 550000 560000 580000 600000
    # inclusive 位置 = i*8/4 → P25=idx2=500000, P50=idx4=540000, P75=idx6=560000
    assert result.priced_count == 9
    assert result.min_fen == 5_000
    assert result.p25_fen == 500_000
    assert result.median_fen == 540_000
    assert result.p75_fen == 560_000
    assert result.lowest_items[0].price_fen == 5_000
    assert result.lowest_items[0].title == "合成 配件"


def test_auction_and_ad_items_are_counted_not_removed() -> None:
    """起拍价与广告位不是普通在售报价，但**是不是要算进分布由调用方决定**。
    本服务只如实计数并给出限制标记。"""
    items = baseline_items() + [
        item(100_000, product_id=90, is_auction=True),
        item(700_000, product_id=91, is_ad=True),
    ]

    result = stats(items)

    assert result.priced_count == 10
    assert result.auction_count == 1
    assert result.ad_count == 1
    assert "includes_auction_start_prices" in result.sample_quality
    assert "includes_promoted_ads" in result.sample_quality
    assert result.min_fen == 100_000
    assert result.max_fen == 700_000


def test_no_auction_or_ad_markers_when_absent() -> None:
    quality = stats(baseline_items()).sample_quality

    assert "includes_auction_start_prices" not in quality
    assert "includes_promoted_ads" not in quality


# ---------------------------------------------------------------- 无价条目


def test_unpriced_items_are_grouped_by_parse_status_not_silently_dropped() -> None:
    """「面议」「平台没给价格控件」都不是本服务能补的，必须原样报出来。"""
    items = baseline_items() + [
        item(None, product_id=97, price_status=AMBIGUOUS, title="合成 定金200"),
        item(None, product_id=96, price_status=AMBIGUOUS, title="合成 面议"),
        item(None, product_id=95, price_status=MISSING, title="合成 无价格控件"),
    ]

    result = stats(items)

    assert result.priced_count == 8
    assert result.unpriced_count == 3
    assert result.unpriced_by_status == {AMBIGUOUS: 2, MISSING: 1}
    assert "some_prices_unparsed" in result.sample_quality
    assert result.median_fen == 545_000, "无价条目不进算术，但也不改变有价条目的分布"


def test_all_unpriced_run_reports_nothing_but_the_reason() -> None:
    items = [
        item(None, product_id=1, price_status=AMBIGUOUS),
        item(None, product_id=2, price_status=MISSING),
    ]

    result = stats(items)

    assert result.priced_count == 0
    assert result.min_fen is None
    assert result.median_fen is None
    assert result.lowest_items == ()
    assert result.unpriced_by_status == {AMBIGUOUS: 1, MISSING: 1}


# ---------------------------------------------------------------- 可追溯性


def test_lowest_items_are_traceable_to_source_listings() -> None:
    result = stats(baseline_items())

    assert len(result.lowest_items) == 3
    assert [entry.price_fen for entry in result.lowest_items] == [480_000, 500_000, 520_000]
    assert all(entry.canonical_url for entry in result.lowest_items)
    assert all(entry.product_id for entry in result.lowest_items)


def test_highest_items_are_reported_too() -> None:
    """只给最低价会让调用方看不见另一端是不是也混进了不可比条目。"""
    result = stats(baseline_items())

    assert [entry.price_fen for entry in result.highest_items] == [
        600_000,
        580_000,
        560_000,
    ]


def test_extremes_are_capped_at_three() -> None:
    result = stats(baseline_items())

    assert len(result.lowest_items) == 3
    assert len(result.highest_items) == 3


# ---------------------------------------------------------------- 空结果与降级


def test_empty_run_is_verified_empty_not_zero_priced() -> None:
    result = stats([])

    assert result.priced_count == 0
    assert result.distinct_count == 0
    assert result.min_fen is None
    assert result.median_fen is None
    assert result.max_fen is None
    assert result.lowest_items == ()
    assert result.insufficient_sample is True


def test_counts_are_layered() -> None:
    """§7：raw / distinct / priced 分层，raw_count 含同轮重复条目。"""
    items = baseline_items()
    result = stats(items, raw_count=62)

    assert result.raw_count == 62
    assert result.distinct_count == 8
    assert result.priced_count == 8


def test_partial_run_is_reported() -> None:
    result = stats(baseline_items(), pages_requested=3, pages_fetched=2, status="partial")

    assert result.partial is True
    assert "partial_pages:2/3" in result.sample_quality


def test_single_page_run_is_reported_as_a_limitation() -> None:
    result = stats(baseline_items(), pages_requested=1, pages_fetched=1)

    assert result.partial is False
    assert "single_page_only" in result.sample_quality


def test_guest_auth_is_reported_as_a_limitation() -> None:
    result = stats(baseline_items(), auth_mode="guest")

    assert "guest_auth" in result.sample_quality


def test_logged_in_has_no_auth_limitation() -> None:
    assert "guest_auth" not in stats(baseline_items(), auth_mode="logged_in").sample_quality


def test_result_is_frozen_and_carries_run_context() -> None:
    result = stats(baseline_items())

    assert result.run_id == "run-1"
    assert result.currency == "CNY"
    with pytest.raises(Exception):
        result.median_fen = 0  # type: ignore[misc]
