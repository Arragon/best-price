"""相关性分类测试。

用例来源：
- [指南] = Xianyu_Agent_Price_Service_Implementation_Guide.md §7 明确要求测试的标题
- [实测] = 2026-09-22 真实抓取「富士 X-T4」时观察到的标题原文（公开挂牌信息）

关键验收（§Phase4）：规则**不得误删正常的 X-T4 单机身**，也不得把 ¥50 的配件
当成相机最低价。
"""

from __future__ import annotations

import pytest

from xps.services.classify import (
    ACCESSORY_ONLY,
    AUCTION,
    BODY,
    BUNDLE,
    COMPATIBLE_VARIANT,
    DEPOSIT_OR_PLACEHOLDER,
    EXACT_MODEL,
    KIT,
    MODEL_MISMATCH,
    PROMOTED_AD,
    RENTAL_OR_LEASE,
    REPAIR_OR_FAULT,
    UNKNOWN_VARIANT,
    WANTED_TO_BUY,
    Classification,
    build_model_spec,
    classify,
)

XT4 = build_model_spec("富士 X-T4")


def flags_of(title: str, **kwargs) -> set[str]:
    return set(classify(title, spec=XT4, **kwargs).flags)


# ---------------------------------------------------------------- 不得误删正常机身


def test_guide_case_body_only_is_kept() -> None:
    """[指南] 富士 X-T4 单机身 → 保留进「单机身」集合。"""
    result = classify("富士 X-T4 单机身", spec=XT4)

    assert EXACT_MODEL in result.flags
    assert result.item_kind == BODY
    assert result.excluded is False
    assert result.needs_review is False


def test_real_observed_body_listing_is_kept() -> None:
    """[实测] id 1083967235157 —— 标题里同时出现「配件/电池/手柄/保护膜」，
    但它是整机出售。绝不能因为出现配件词就判 accessory_only。"""
    title = (
        "几乎全新富士XT4银色国行，箱说全，功能完好无修，贴了保护膜 ，成色很棒。"
        "单机+原厂配件+原厂电池，送红木L型快装手柄。按键屏幕都正常"
    )
    result = classify(title, spec=XT4)

    assert EXACT_MODEL in result.flags
    assert ACCESSORY_ONLY not in result.flags
    assert REPAIR_OR_FAULT not in result.flags, "「功能完好无修」不是故障机"
    assert result.item_kind == BODY
    assert result.excluded is False


@pytest.mark.parametrize(
    "title",
    [
        "富士xt4单机，银色，机器整体外观98新左右，底部有划痕",   # [实测] id 1083927427704
        "富士X-T4银色机身，功能正常，屏幕、按键、CMOS都没问题",   # [实测] id 1083927423640
        "富士 X-T4 单机身 无拆无修",
        "x-t4 裸机一台",
    ],
)
def test_body_hints_recognised_regardless_of_spacing_or_case(title: str) -> None:
    result = classify(title, spec=XT4)

    assert result.item_kind == BODY
    assert result.excluded is False


# ---------------------------------------------------------------- 套机分流


def test_guide_case_kit_is_separated_from_body() -> None:
    """[指南] 富士 X-T4 18-55 套机 → 归入套机，不能与单机身求同一均价。"""
    result = classify("富士 X-T4 18-55 套机", spec=XT4)

    assert result.item_kind == KIT
    assert BUNDLE in result.flags
    assert EXACT_MODEL in result.flags


def test_kit_is_excluded_when_body_was_requested() -> None:
    result = classify("富士 X-T4 18-55 套机", spec=XT4, item_kind=BODY)

    assert result.excluded is True
    assert "item_kind_mismatch" in result.exclusion_reasons


def test_body_is_excluded_when_kit_was_requested() -> None:
    result = classify("富士 X-T4 单机身", spec=XT4, item_kind=KIT)

    assert result.excluded is True
    assert "item_kind_mismatch" in result.exclusion_reasons


def test_both_body_and_kit_hints_go_to_review() -> None:
    result = classify("富士 X-T4 单机+套机都出", spec=XT4)

    assert result.item_kind is None
    assert result.needs_review is True
    assert result.excluded is False


# ---------------------------------------------------------------- 配件


@pytest.mark.parametrize(
    "title",
    [
        "X-T4 电池",        # [指南]
        "X-T4 皮套",        # [指南]
        "X-T4 快门线",      # [指南]
        "富士X-T4原装充电器一个",
        "X-T4 机身盖 镜头盖",
    ],
)
def test_guide_case_accessories_are_excluded_from_body_stats(title: str) -> None:
    result = classify(title, spec=XT4)

    assert ACCESSORY_ONLY in result.flags
    assert result.excluded is True
    assert ACCESSORY_ONLY in result.exclusion_reasons


def test_lens_only_is_accessory() -> None:
    result = classify("富士 XF 18-55mm 镜头 单出", spec=XT4)

    assert ACCESSORY_ONLY in result.flags
    assert result.excluded is True


# ---------------------------------------------------------------- 故障机


def test_guide_case_water_damage_is_fault() -> None:
    """[指南] X-T4 进水不开机 → 归入故障机，排除正常机身统计。"""
    result = classify("X-T4 进水不开机", spec=XT4)

    assert REPAIR_OR_FAULT in result.flags
    assert result.excluded is True


@pytest.mark.parametrize(
    "title",
    ["富士X-T4 故障机 配件机", "X-T4 无法开机 便宜出", "X-T4 屏幕花屏 有修过"],
)
def test_fault_variants_are_excluded(title: str) -> None:
    assert REPAIR_OR_FAULT in flags_of(title)


@pytest.mark.parametrize("title", ["富士 X-T4 功能完好无修", "X-T4 无拆无修 成色新"])
def test_negated_repair_words_are_not_faults(title: str) -> None:
    """「无修」「无拆无修」是好事，不能触发 repair_or_fault。"""
    assert REPAIR_OR_FAULT not in flags_of(title)


# ---------------------------------------------------------------- 求购（非卖盘）


@pytest.mark.parametrize("title", ["收 X-T4", "求购 X-T4", "高价回收富士X-T4", "想收一台XT4"])
def test_guide_case_wanted_to_buy_is_excluded(title: str) -> None:
    """[指南] 收/求购 → 非卖盘，排除。"""
    result = classify(title, spec=XT4)

    assert WANTED_TO_BUY in result.flags
    assert result.excluded is True


def test_word_containing_shou_is_not_wanted() -> None:
    """「收纳」「收录」里的「收」不是求购。"""
    assert WANTED_TO_BUY not in flags_of("富士 X-T4 单机身 附赠收纳包")


# ---------------------------------------------------------------- 租赁（实测新增标签）


@pytest.mark.parametrize(
    "title",
    [
        # [实测] id 1086948380894，¥90 —— 本次搜索最大的价格污染源
        "#重庆同城#索尼a7m4/a7m3/a7c2/a7r3免押租赁#同城可自提 ⭐租赁流程⭐",
        # [实测] id 1083927299431，¥50
        "杭州租索尼/A7M5/A7M4/A7M3全画幅微单 活动跟拍旅拍写真直播设备 猴哥相机租赁",
        "富士X-T4 日租 100/天",
    ],
)
def test_rental_listings_are_excluded(title: str) -> None:
    """租赁日租价混入机身价会把中位数打穿。"""
    result = classify(title, spec=XT4)

    assert RENTAL_OR_LEASE in result.flags
    assert result.excluded is True


# ---------------------------------------------------------------- 型号错配


def test_different_model_is_excluded_not_reviewed() -> None:
    """X-T3 是另一个型号，有文本证据即可明确排除，不该塞进 review 稀释样本。"""
    result = classify("富士 X-T3 单机身 成色好", spec=XT4)

    assert MODEL_MISMATCH in result.flags
    assert EXACT_MODEL not in result.flags
    assert result.excluded is True


def test_model_token_does_not_match_longer_model() -> None:
    """XT40 不能因为包含子串 XT4 就被当成 X-T4。"""
    result = classify("富士 X-T40 单机身", spec=XT4)

    assert EXACT_MODEL not in result.flags
    assert MODEL_MISMATCH in result.flags


def test_compatible_variant_flagged_alongside_exact_model() -> None:
    result = classify("富士 X-T4 银色 国行 单机身", spec=XT4)

    assert EXACT_MODEL in result.flags
    assert COMPATIBLE_VARIANT in result.flags
    assert result.excluded is False


# ---------------------------------------------------------------- 定金 / 占位


@pytest.mark.parametrize(
    "title", ["富士X-T4 定金链接", "X-T4 拍前请联系 补差价专拍", "X-T4 一元占位"]
)
def test_deposit_or_placeholder_is_excluded(title: str) -> None:
    assert DEPOSIT_OR_PLACEHOLDER in flags_of(title)


# ---------------------------------------------------------------- 拍卖 / 广告（实测字段）


def test_auction_flag_from_platform_field() -> None:
    """实测 schema 有 exContent.isAuction；起拍价不是在售报价。"""
    result = classify("富士 X-T4 单机身", spec=XT4, is_auction=True)

    assert AUCTION in result.flags
    assert result.excluded is True


def test_promoted_ad_flag_from_platform_field() -> None:
    """实测 schema 有 exContent.isAliMaMaAD；广告位非自然结果。"""
    result = classify("富士 X-T4 单机身", spec=XT4, is_ad=True)

    assert PROMOTED_AD in result.flags
    assert result.excluded is True


# ---------------------------------------------------------------- 待核验


def test_guide_case_image_deferral_goes_to_review() -> None:
    """[指南] XT4，详情见图，3500 → 型号可疑歧义时标记待核验，
    不得由大模型把图片内容当已核实事实。"""
    result = classify("XT4，详情见图，3500", spec=XT4)

    assert UNKNOWN_VARIANT in result.flags
    assert result.needs_review is True
    assert result.excluded is False, "不确定样本进 review，不静默删除"


def test_real_observed_inspected_listing_without_config_goes_to_review() -> None:
    """[实测] id 1085916193246 标题只说「富士 X-T4_银色 经检测…」，
    文本无法确认是单机身还是套机 → 诚实地进 review。"""
    result = classify("富士 X-T4_银色 经检测，设备外观仅有轻微使用痕迹", spec=XT4)

    assert EXACT_MODEL in result.flags
    assert result.item_kind is None
    assert result.needs_review is True
    assert result.excluded is False


# ---------------------------------------------------------------- 多标签与结构


def test_multiple_flags_can_coexist() -> None:
    """§7：每个商品可同时具有多个标签。"""
    result = classify("X-T4 进水不开机 定金链接", spec=XT4)

    assert REPAIR_OR_FAULT in result.flags
    assert DEPOSIT_OR_PLACEHOLDER in result.flags
    assert result.exclusion_reasons == tuple(sorted(set(result.exclusion_reasons)))


def test_classification_is_frozen() -> None:
    result = classify("富士 X-T4 单机身", spec=XT4)

    assert isinstance(result, Classification)
    with pytest.raises(Exception):
        result.excluded = True  # type: ignore[misc]


def test_empty_title_is_not_silently_kept() -> None:
    result = classify("", spec=XT4)

    assert result.needs_review is True
    assert result.excluded is False


def test_spec_without_model_token_never_flags_mismatch() -> None:
    """关键词里没有可识别型号时，不能用 model_mismatch 把所有东西删光。"""
    spec = build_model_spec("二手相机")

    assert spec.model_tokens == ()
    result = classify("索尼 a7m4 单机身", spec=spec)
    assert MODEL_MISMATCH not in result.flags


def test_build_model_spec_splits_model_and_brand() -> None:
    spec = build_model_spec("富士 X-T4")

    assert spec.model_tokens == ("XT4",)
    assert spec.brand_tokens == ("富士",)


@pytest.mark.parametrize(
    "keyword,expected",
    [("9950X3D", ("9950X3D",)), ("2TB NVMe SSD", ("2TB",)), ("x-t4", ("XT4",))],
)
def test_build_model_spec_handles_guide_success_keywords(
    keyword: str, expected: tuple[str, ...]
) -> None:
    """指南成功条件里的三个搜索词都要能推导出型号 token。"""
    assert build_model_spec(keyword).model_tokens == expected
