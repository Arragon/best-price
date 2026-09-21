"""原始 mtop 条目 → RawListing → NormalizedListing 管道测试。

fixture 全部为合成数据（见 tests/fixtures/mtop_entry.py 头部声明）。
结构对齐 2026-09-22 实测响应路径，取值虚构。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from xps.adapters.base import RawListing
from xps.adapters.xianyu import RAW_PAYLOAD_MAX_BYTES, entry_to_raw_listing
from xps.services.classify import (
    ACCESSORY_ONLY,
    AUCTION,
    BODY,
    EXACT_MODEL,
    INVALID_IDENTITY,
    PROMOTED_AD,
    build_model_spec,
)
from xps.services.normalize import AMBIGUOUS, VALID, normalize_listing
from tests.fixtures.mtop_entry import (
    DEFAULT_ITEM_ID,
    PRICE_YEN_5642_50,
    make_entry,
)

XT4 = build_model_spec("富士 X-T4")
OBSERVED_AT = datetime(2026, 9, 22, 3, 0, 0, tzinfo=timezone.utc)

# 上游 safe_get / handle_data 的默认哨兵值，本项目绝不允许出现
UPSTREAM_PLACEHOLDERS = {"暂无", "未知标题", "地区未知", "匿名卖家", "价格异常", ""}


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


# ---------------------------------------------------------------- 标准化组装


def test_normalize_composes_identity_price_and_classification() -> None:
    raw = entry_to_raw_listing(
        make_entry(item_id="7001", title="富士 X-T4 单机身", target_url=None)
    )

    result = normalize_listing(raw, spec=XT4, observed_at=OBSERVED_AT)

    assert result.identity.identity_key == "xianyu:item:7001"
    assert result.price_fen == 549_900
    assert result.price_status == VALID
    assert result.currency == "CNY"
    assert EXACT_MODEL in result.classification.flags
    assert result.classification.item_kind == BODY
    assert result.observed_at == OBSERVED_AT


def test_normalize_preserves_price_text_verbatim() -> None:
    raw = entry_to_raw_listing(make_entry(price=PRICE_YEN_5642_50))

    result = normalize_listing(raw, spec=XT4, observed_at=OBSERVED_AT)

    assert result.price_raw == "¥5642.50"
    assert result.price_fen == 564_250


def test_normalize_marks_ambiguous_price_without_a_number() -> None:
    raw = entry_to_raw_listing(make_entry(price=(("integer", "定金200"),)))

    result = normalize_listing(raw, spec=XT4, observed_at=OBSERVED_AT)

    assert result.price_status == AMBIGUOUS
    assert result.price_fen is None
    assert result.price_raw == "定金200"


def test_normalize_propagates_auction_and_ad_into_flags() -> None:
    raw = entry_to_raw_listing(make_entry(is_auction=True))
    assert AUCTION in normalize_listing(raw, spec=XT4, observed_at=OBSERVED_AT).classification.flags

    raw_ad = entry_to_raw_listing(make_entry(is_ad=True))
    result = normalize_listing(raw_ad, spec=XT4, observed_at=OBSERVED_AT)
    assert PROMOTED_AD in result.classification.flags


def test_normalize_classifies_accessory_from_title() -> None:
    raw = entry_to_raw_listing(make_entry(title="X-T4 电池", price=(("integer", "50"),)))

    result = normalize_listing(raw, spec=XT4, observed_at=OBSERVED_AT)

    assert ACCESSORY_ONLY in result.classification.flags
    assert result.classification.excluded is True
    assert result.price_fen == 5_000, "价格本身有效，是被分类排除，不是解析失败"


def test_invalid_identity_is_flagged_and_excluded() -> None:
    raw = RawListing(
        source_id=None,
        url="https://evil.example.com/item?id=1",
        title="富士 X-T4 单机身",
        price_text="¥5499",
    )

    result = normalize_listing(raw, spec=XT4, observed_at=OBSERVED_AT)

    assert result.identity.id_source == "invalid_identity"
    assert INVALID_IDENTITY in result.classification.flags
    assert result.classification.excluded is True


def test_published_at_is_never_backfilled_with_observed_at() -> None:
    """§6：published_at 真实存在才填，不可用采集时间替代。"""
    raw = entry_to_raw_listing(make_entry(publish_time=None))

    result = normalize_listing(raw, spec=XT4, observed_at=OBSERVED_AT)

    assert result.published_at is None
    assert result.observed_at == OBSERVED_AT


def test_unidentifiable_listing_is_marked_skippable() -> None:
    """既无 itemId 又无可信 URL 的条目无法去重，必须显式标记，不能硬塞进 products。"""
    raw = entry_to_raw_listing(make_entry(item_id=None, target_url=None))

    result = normalize_listing(raw, spec=XT4, observed_at=OBSERVED_AT)

    assert result.identity.id_source == "invalid_identity"
    assert result.storable is False
