"""价格统计测试。

分位数算法固定为 statistics.quantiles(n=4, method='inclusive')，
下列期望值均为手工推导，不用实现本身去算期望值。

数据集（8 件，单位：分）：
    480000 500000 520000 540000 550000 560000 580000 600000
inclusive 法：位置 = i*(n-1)/4 = i*7/4
    P25 → 1.75 → 500000 + 0.75*20000 = 515000
    P50 → 3.50 → 540000 + 0.50*10000 = 545000
    P75 → 5.25 → 560000 + 0.25*20000 = 565000
"""

from __future__ import annotations

import pytest

from xps.services.classify import (
    ACCESSORY_ONLY,
    ANY,
    BODY,
    EXACT_MODEL,
    KIT,
    MODEL_MISMATCH,
    SUSPICIOUS_PRICE,
    UNKNOWN_VARIANT,
)
from xps.services.normalize import VALID, AMBIGUOUS
from xps.services.statistics import (
    MIN_SAMPLE_THRESHOLD,
    PriceStats,
    StatsItem,
    compute_stats,
)

BASELINE_FEN = [480_000, 500_000, 520_000, 540_000, 550_000, 560_000, 580_000, 600_000]


def item(
    price_fen: int | None,
    *,
    product_id: int = 1,
    title: str = "富士 X-T4 单机身",
    url: str | None = "https://www.goofish.com/item?id=1",
    price_status: str = VALID,
    flags: tuple[str, ...] = (EXACT_MODEL,),
    item_kind: str | None = BODY,
    excluded: bool = False,
    exclusion_reasons: tuple[str, ...] = (),
    needs_review: bool = False,
) -> StatsItem:
    return StatsItem(
        product_id=product_id,
        title=title,
        canonical_url=url,
        price_fen=price_fen,
        price_status=price_status,
        flags=flags,
        item_kind=item_kind,
        excluded=excluded,
        exclusion_reasons=exclusion_reasons,
        needs_review=needs_review,
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

    assert result.eligible_count == 1
    assert result.min_fen == result.median_fen == result.max_fen == 500_000
    assert result.p25_fen is None
    assert result.p75_fen is None


# ---------------------------------------------------------------- 样本门槛


def test_eight_samples_is_not_insufficient() -> None:
    assert MIN_SAMPLE_THRESHOLD == 8
    assert stats(baseline_items()).insufficient_sample is False


def test_seven_samples_is_insufficient() -> None:
    result = stats(baseline_items()[:7])

    assert result.eligible_count == 7
    assert result.insufficient_sample is True
    assert "insufficient_sample" in result.sample_quality


def test_insufficient_sample_still_reports_known_values() -> None:
    """§7：样本过少仍展示已知样本，但不产生过度确定的「市场公允价」。"""
    result = stats(baseline_items()[:3])

    assert result.median_fen == 500_000
    assert result.insufficient_sample is True


# ---------------------------------------------------------------- 排除与待核验


def test_excluded_accessory_never_becomes_the_lowest_price() -> None:
    """§Phase4 验收：不把配件 50 元当成相机最低价。"""
    items = baseline_items() + [
        item(
            5_000,
            product_id=99,
            title="X-T4 电池",
            flags=(ACCESSORY_ONLY,),
            item_kind=None,
            excluded=True,
            exclusion_reasons=(ACCESSORY_ONLY,),
        )
    ]

    result = stats(items)

    assert result.min_fen == 480_000
    assert result.excluded_count == 1
    assert result.eligible_count == 8


def test_needs_review_items_are_not_eligible() -> None:
    items = baseline_items() + [
        item(
            350_000,
            product_id=98,
            title="XT4，详情见图，3500",
            flags=(UNKNOWN_VARIANT,),
            item_kind=None,
            needs_review=True,
        )
    ]

    result = stats(items)

    assert result.needs_review_count == 1
    assert result.eligible_count == 8
    assert result.median_fen == 545_000


def test_review_items_do_not_pollute_excluded_by_reason() -> None:
    """excluded_by_reason 只统计被排除的；待核验是另一个维度，不能混进去。"""
    items = baseline_items() + [
        item(530_000, product_id=95, item_kind=None, needs_review=True)
    ]

    result = stats(items, item_kind=BODY)

    assert result.needs_review_count == 1
    assert result.excluded_count == 0
    assert result.excluded_by_reason == {}


def test_unconfirmed_kind_without_review_flag_counts_as_review() -> None:
    """请求 body 时，无法从文本确认配置的商品进 review，不当成排除。"""
    items = baseline_items() + [item(530_000, product_id=94, item_kind=None)]

    result = stats(items, item_kind=BODY)

    assert result.needs_review_count == 1
    assert result.excluded_count == 0
    assert result.eligible_count == 8


def test_ambiguous_price_is_not_eligible() -> None:
    items = baseline_items() + [
        item(None, product_id=97, price_status=AMBIGUOUS, title="X-T4 定金200")
    ]

    result = stats(items)

    assert result.eligible_count == 8
    assert result.excluded_count == 1


def test_wrong_item_kind_is_excluded_with_reason() -> None:
    items = baseline_items() + [
        item(300_000, product_id=96, title="富士 X-T4 18-55 套机", item_kind=KIT, flags=(KIT,))
    ]

    result = stats(items, item_kind=BODY)

    assert result.eligible_count == 8
    assert result.excluded_by_reason.get("item_kind_mismatch") == 1


def test_item_kind_any_accepts_both_body_and_kit() -> None:
    # 530000 落在 IQR 围栏内，确保这条测试考的是 item_kind 放行，而不是异常价拦截
    items = baseline_items() + [
        item(530_000, product_id=96, title="富士 X-T4 18-55 套机", item_kind=KIT)
    ]

    assert stats(items, item_kind=ANY).eligible_count == 9


def test_mixed_item_kinds_are_reported_as_a_limitation() -> None:
    """§7：套机不能与单机身直接求同一均价；调用方选了 any 就必须看到这个限制。"""
    items = baseline_items() + [
        item(530_000, product_id=96, title="富士 X-T4 18-55 套机", item_kind=KIT)
    ]

    assert "mixed_item_kinds" in stats(items, item_kind=ANY).sample_quality


def test_single_kind_run_has_no_mixed_limitation() -> None:
    assert "mixed_item_kinds" not in stats(baseline_items(), item_kind=BODY).sample_quality


def test_excluded_by_reason_groups_counts() -> None:
    items = [
        item(None, product_id=1, excluded=True, exclusion_reasons=(ACCESSORY_ONLY,)),
        item(None, product_id=2, excluded=True, exclusion_reasons=(ACCESSORY_ONLY,)),
        item(None, product_id=3, excluded=True, exclusion_reasons=(MODEL_MISMATCH,)),
    ]

    result = stats(items)

    assert result.excluded_by_reason[ACCESSORY_ONLY] == 2
    assert result.excluded_by_reason[MODEL_MISMATCH] == 1
    assert result.excluded_count == 3


def test_multi_reason_item_is_counted_once_per_reason() -> None:
    items = [
        item(
            None,
            product_id=1,
            excluded=True,
            exclusion_reasons=(ACCESSORY_ONLY, MODEL_MISMATCH),
        )
    ]

    result = stats(items)

    assert result.excluded_count == 1
    assert result.excluded_by_reason[ACCESSORY_ONLY] == 1
    assert result.excluded_by_reason[MODEL_MISMATCH] == 1


# ---------------------------------------------------------------- 异常低价防线


def test_low_outlier_is_flagged_suspicious_and_removed() -> None:
    """第二道防线：即使标签漏判，¥50 也不会把中位数打穿。"""
    items = baseline_items() + [item(5_000, product_id=99, title="富士 X-T4 单机身 急出")]

    result = stats(items)

    assert result.excluded_by_reason.get(SUSPICIOUS_PRICE) == 1
    assert result.eligible_count == 8
    assert result.median_fen == 545_000
    assert result.min_fen == 480_000


def test_outlier_detection_is_skipped_below_sample_threshold() -> None:
    """样本太少时 IQR 不稳定，不该自动删数据。"""
    items = [item(5_000, product_id=1), item(500_000, product_id=2), item(520_000, product_id=3)]

    result = stats(items)

    assert result.excluded_by_reason.get(SUSPICIOUS_PRICE) is None
    assert result.eligible_count == 3
    assert result.insufficient_sample is True


def test_normal_spread_is_not_flagged_as_outlier() -> None:
    result = stats(baseline_items())

    assert result.excluded_by_reason.get(SUSPICIOUS_PRICE) is None


# ---------------------------------------------------------------- 可追溯性


def test_lowest_items_are_traceable_to_source_listings() -> None:
    result = stats(baseline_items())

    assert len(result.lowest_items) == 3
    assert [entry.price_fen for entry in result.lowest_items] == [480_000, 500_000, 520_000]
    assert all(entry.canonical_url for entry in result.lowest_items)
    assert all(entry.product_id for entry in result.lowest_items)


def test_excluded_items_are_absent_from_lowest_items() -> None:
    items = baseline_items() + [
        item(
            5_000,
            product_id=99,
            flags=(ACCESSORY_ONLY,),
            item_kind=None,
            excluded=True,
            exclusion_reasons=(ACCESSORY_ONLY,),
        )
    ]

    assert stats(items).lowest_items[0].price_fen == 480_000


# ---------------------------------------------------------------- 空结果与降级


def test_empty_run_is_verified_empty_not_zero_priced() -> None:
    result = stats([])

    assert result.eligible_count == 0
    assert result.min_fen is None
    assert result.median_fen is None
    assert result.max_fen is None
    assert result.lowest_items == ()
    assert result.insufficient_sample is True


def test_counts_are_layered() -> None:
    """§7：raw / distinct / eligible 分层，raw_count 含同轮重复条目。"""
    items = baseline_items()
    result = stats(items, raw_count=62)

    assert result.raw_count == 62
    assert result.distinct_count == 8
    assert result.eligible_count == 8


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
    result = stats(baseline_items(), item_kind=BODY)

    assert result.run_id == "run-1"
    assert result.item_kind == BODY
    assert result.currency == "CNY"
    with pytest.raises(Exception):
        result.median_fen = 0  # type: ignore[misc]
