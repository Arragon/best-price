"""原始 mtop 条目 → RawListing → NormalizedListing 管道测试。

fixture 全部为合成数据（见 tests/fixtures/mtop_entry.py 头部声明）。
结构对齐 2026-09-22 实测响应路径，取值虚构。

本服务**不做相关性判断**，所以这里考的是两件事：
1. 平台真实给出的字段有没有被完整抽出来（漏抽 = agent 少一份判断依据）
2. 缺失是不是老老实实为 None（不得用占位值冒充真实数据）
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from xps.adapters.base import RawListing
from xps.adapters.xianyu import RAW_PAYLOAD_MAX_BYTES, entry_to_raw_listing
from xps.services.normalize import AMBIGUOUS, VALID, normalize_listing
from tests.fixtures.mtop_entry import (
    DEFAULT_ITEM_ID,
    PRICE_YEN_5642_50,
    make_entry,
)

OBSERVED_AT = datetime(2026, 9, 22, 3, 0, 0, tzinfo=timezone.utc)

# 上游 safe_get / handle_data 的默认哨兵值，本项目绝不允许出现
UPSTREAM_PLACEHOLDERS = {"暂无", "未知标题", "地区未知", "匿名卖家", "价格异常", ""}


def normalize(entry: dict) -> tuple[RawListing, object]:
    raw = entry_to_raw_listing(entry)
    return raw, normalize_listing(raw, observed_at=OBSERVED_AT)


# ---------------------------------------------------------------- 原始条目解析


def test_source_id_is_the_real_platform_item_id() -> None:
    raw = entry_to_raw_listing(make_entry(item_id="1083967235157"))

    assert isinstance(raw, RawListing)
    assert raw.source_id == "1083967235157"


def test_fleamarket_target_url_becomes_canonical_https() -> None:
    raw = entry_to_raw_listing(make_entry())

    assert raw.url == f"https://www.goofish.com/item?id={DEFAULT_ITEM_ID}"
    assert "referPageArgs" not in raw.url
    assert "gulSource" not in raw.url


def test_price_text_is_joined_from_all_parts() -> None:
    assert entry_to_raw_listing(make_entry()).price_text == "¥5499"


def test_price_text_keeps_the_decimal_part() -> None:
    """实测 ¥5642.50 由 sign+integer+decimal 三段组成，漏掉 decimal 会少 5 角。"""
    raw = entry_to_raw_listing(make_entry(price=PRICE_YEN_5642_50))

    assert raw.price_text == "¥5642.50"


def test_protocol_relative_image_url_gets_https() -> None:
    raw = entry_to_raw_listing(make_entry(pic_url="//img.example.invalid/a.jpg"))

    assert raw.image_url == "https://img.example.invalid/a.jpg"


def test_publish_time_millis_becomes_utc_iso() -> None:
    raw = entry_to_raw_listing(make_entry(publish_time="1789989232000"))

    assert raw.published_at == "2026-09-21T11:13:52Z"


@pytest.mark.parametrize("bad", [None, "", "未知时间", "abc", "178998923200"])
def test_unusable_publish_time_is_none(bad: str | None) -> None:
    """§6：published_at 真实存在才填，不可用采集时间替代。"""
    assert entry_to_raw_listing(make_entry(publish_time=bad)).published_at is None


@pytest.mark.parametrize(
    ("field", "kwargs"),
    [
        ("title", {"title": None}),
        ("area", {"area": None}),
        ("seller_name", {"nick": None}),
        ("image_url", {"pic_url": None}),
        ("price_text", {"price": None}),
        ("description", {"title": None, "description": None}),
        ("seller_avatar_url", {"avatar_url": None}),
        ("seller_credit", {"credit": None}),
        ("seller_review_count", {"review_count": None}),
        ("seller_positive_rate", {"positive_rate": None}),
        ("seller_identity", {"seller_identity": None}),
        ("published_text", {"published_text": None}),
        ("original_price_text", {"ori_price": None}),
    ],
)
def test_missing_fields_are_none_never_upstream_placeholders(
    field: str, kwargs: dict
) -> None:
    """§8.3 + §0.4：上游会填「暂无」「价格异常」「匿名卖家」等占位伪数据，
    本项目必须让缺失就是 None，不得插入占位值冒充真实数据。"""
    raw = entry_to_raw_listing(make_entry(**kwargs))

    value = getattr(raw, field)
    assert value is None
    assert value not in UPSTREAM_PLACEHOLDERS


def test_missing_ex_content_does_not_crash() -> None:
    raw = entry_to_raw_listing(make_entry(with_ex_content=False))

    assert raw.title is None
    assert raw.price_text is None
    assert raw.area is None
    assert raw.seller_name is None
    assert raw.description is None
    assert raw.seller_credit is None


def test_item_id_falls_back_to_click_param_when_ex_content_is_missing() -> None:
    """实测 clickParam.args.item_id 与 exContent.itemId 一致，可作冗余来源。"""
    raw = entry_to_raw_listing(make_entry(with_ex_content=False, item_id="7770001"))

    assert raw.source_id == "7770001"
    assert raw.url == "https://www.goofish.com/item?id=7770001"


def test_auction_and_ad_flags_are_surfaced() -> None:
    raw = entry_to_raw_listing(make_entry(is_auction=True, is_ad=True))

    assert raw.is_auction is True
    assert raw.is_ad is True


def test_auction_defaults_to_false_not_none() -> None:
    raw = entry_to_raw_listing(make_entry())

    assert raw.is_auction is False
    assert raw.is_ad is False


def test_raw_payload_keeps_the_unstripped_source_url_for_traceability() -> None:
    """canonical_url 剥掉了跟踪参数，原始形态必须留档以便追溯。"""
    raw = entry_to_raw_listing(make_entry())

    assert raw.raw_payload is not None
    assert "referPageArgs" in raw.raw_payload["source_url"]


def test_raw_payload_is_bounded() -> None:
    """上游 extra 跟踪参数可能极长；限长后仍可追溯，不会把 DB 撑爆。"""
    long_url = (
        f"fleamarket://item?id={DEFAULT_ITEM_ID}&extra=" + "A" * 20_000
    )

    raw = entry_to_raw_listing(make_entry(target_url=long_url))
    encoded = json.dumps(raw.raw_payload, ensure_ascii=False).encode()

    assert len(encoded) <= RAW_PAYLOAD_MAX_BYTES
    assert raw.raw_payload["source_url"].startswith("fleamarket://item?id=")


def test_raw_payload_never_contains_credentials() -> None:
    raw = entry_to_raw_listing(make_entry())
    blob = json.dumps(raw.raw_payload, ensure_ascii=False).lower()

    for forbidden in ("cookie", "_m_h5_tk", "token", "unb=", "sgcookie"):
        assert forbidden not in blob


def test_garbage_entry_returns_a_listing_not_an_exception() -> None:
    """上游结构漂移时应当降级为可识别的坏条目，而不是整轮崩掉。"""
    for entry in ({}, {"data": {}}, {"data": {"item": None}}, {"data": {"item": {"main": 5}}}):
        raw = entry_to_raw_listing(entry)
        assert isinstance(raw, RawListing)
        assert raw.source_id is None


# ------------------------------------------------- 平台标签透传（fishTags / shopLabel）


def test_seller_credit_is_passed_through_verbatim() -> None:
    """信用标签是平台原话，不做任何映射或打分——映射就等于替调用方判断。"""
    raw = entry_to_raw_listing(make_entry(credit="卖家信用优秀"))

    assert raw.seller_credit == "卖家信用优秀"


def test_review_count_and_positive_rate_are_parsed_from_shop_label() -> None:
    raw = entry_to_raw_listing(make_entry(review_count=318, positive_rate="39%"))

    assert raw.seller_review_count == 318
    assert raw.seller_positive_rate == "39%"


@pytest.mark.parametrize("text,expected", [("好评率100%", "100%"), ("好评率0%", "0%")])
def test_positive_rate_handles_boundary_percentages(text: str, expected: str) -> None:
    raw = entry_to_raw_listing(make_entry(positive_rate=text.removeprefix("好评率")))

    assert raw.seller_positive_rate == expected


def test_unparsable_shop_label_yields_none_not_zero() -> None:
    """文案变了就取不到数字，此时必须是 None——填 0 会伪造出「零评价卖家」。"""
    entry = make_entry()
    entry["data"]["item"]["main"]["exContent"]["userFishShopLabel"] = {
        "tagList": [{"data": {"content": "卖家信誉良好"}}]
    }

    raw = entry_to_raw_listing(entry)

    assert raw.seller_review_count is None
    assert raw.seller_positive_rate is None


def test_want_count_and_published_text_come_from_their_own_tag_groups() -> None:
    raw = entry_to_raw_listing(
        make_entry(want_count=8, published_text="6小时前发布")
    )

    assert raw.want_count == 8
    assert raw.published_text == "6小时前发布"


def test_coupon_text_is_kept_because_it_qualifies_the_price() -> None:
    """「券已抵50元」意味着展示价可能已扣券，属价格口径，必须透出。"""
    raw = entry_to_raw_listing(make_entry(coupon_text="券已抵50元", want_count=None))

    assert raw.coupon_text == "券已抵50元"
    assert raw.want_count is None


def test_free_shipping_icon_becomes_a_boolean_and_leaves_labels() -> None:
    raw = entry_to_raw_listing(
        make_entry(free_shipping=True, badges=("严选", "验货宝"))
    )

    assert raw.free_shipping is True
    assert raw.labels == ("严选", "验货宝")


def test_free_shipping_absent_means_false_and_no_icon_leaks_into_labels() -> None:
    raw = entry_to_raw_listing(make_entry(badges=("严选",)))

    assert raw.free_shipping is False
    assert raw.labels == ("严选",)


def test_description_preserves_line_breaks_that_title_drops() -> None:
    """实测 exContent.title 从不含换行，detailParams.title 保留分段。
    挂牌描述常达 1500 字，没有分段的版本对判断「是不是租赁/配件」很不利。"""
    raw = entry_to_raw_listing(
        make_entry(
            title="合成租赁 免押租赁 流程说明",
            description="合成租赁 免押租赁\n流程说明：\n档期沟通",
        )
    )

    assert raw.title == "合成租赁 免押租赁 流程说明"
    assert raw.description == "合成租赁 免押租赁\n流程说明：\n档期沟通"


def test_all_tag_labels_are_archived_in_raw_payload() -> None:
    """已建模成独立字段的只是常用几个；全量留档，平台新增标签才不会被静默丢掉。"""
    raw = entry_to_raw_listing(
        make_entry(
            credit="卖家信用极好",
            published_text="8小时前发布",
            want_count=3,
            coupon_text="券已抵50元",
            badges=("验货宝",),
            review_count=7,
            positive_rate="100%",
        )
    )

    assert set(raw.raw_payload["tag_labels"]) >= {
        "验货宝",
        "8小时前发布",
        "3人想要",
        "券已抵50元",
        "卖家信用极好",
        "7条评价",
        "好评率100%",
    }


# ---------------------------------------------------------------- 标准化组装


def test_normalize_carries_the_platform_record_untouched() -> None:
    """normalize 只加身份与价格解析，**不得**改写或筛掉平台字段。"""
    raw, result = normalize(make_entry(item_id="7001", target_url=None))

    assert result.raw is raw
    assert result.identity.identity_key == "xianyu:item:7001"
    assert result.price.price_fen == 549_900
    assert result.price.status == VALID
    assert result.price.currency == "CNY"
    assert result.observed_at == OBSERVED_AT


def test_normalize_preserves_price_text_verbatim() -> None:
    _, result = normalize(make_entry(price=PRICE_YEN_5642_50))

    assert result.price.price_raw == "¥5642.50"
    assert result.price.price_fen == 564_250


def test_normalize_marks_ambiguous_price_but_keeps_the_listing() -> None:
    """「定金200」解析不出总价，但条目照样返回给调用方，原文一并保留。"""
    raw, result = normalize(make_entry(price=(("integer", "定金200"),)))

    assert result.price.status == AMBIGUOUS
    assert result.price.price_fen is None
    assert result.price.price_raw == "定金200"
    assert result.storable is True


def test_untrusted_host_url_yields_invalid_identity_but_is_still_storable() -> None:
    """非闲鱼主机的 URL 造不出可信 canonical_url，但有 URL 就仍能安全去重。"""
    raw = RawListing(
        source_id=None,
        url="https://evil.example.com/item?id=1",
        title="合成商品",
        price_text="¥5499",
    )

    result = normalize_listing(raw, observed_at=OBSERVED_AT)

    assert result.identity.id_source == "invalid_identity"
    assert result.identity.canonical_url is None
    assert result.storable is True


def test_published_at_is_never_backfilled_with_observed_at() -> None:
    """§6：published_at 真实存在才填，不可用采集时间替代。"""
    _, result = normalize(make_entry(publish_time=None))

    assert result.raw.published_at is None
    assert result.observed_at == OBSERVED_AT


def test_unidentifiable_listing_is_marked_skippable() -> None:
    """既无 itemId 又无可信 URL 的条目无法去重（identity_key 会碰撞成一行、
    静默吞掉数据），必须显式标记不入库。这是完整性约束，不是相关性筛选。"""
    _, result = normalize(make_entry(item_id=None, target_url=None))

    assert result.identity.id_source == "invalid_identity"
    assert result.storable is False
