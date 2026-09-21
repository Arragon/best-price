"""价格解析测试。

用例来源标注：
- [实测] = 2026-09-22 真实抓取「富士 X-T4」时观察到的原文格式
- [指南] = Xianyu_Agent_Price_Service_Implementation_Guide.md §7 / §11 明确列举
- [上游] = 上游 xianyu_spider 会产生的哨兵/占位值，本项目必须识别为无效而非数字

所有期望值均为人工推导，非真实市场报价。
"""

from __future__ import annotations

import pytest

from xps.services.normalize import PriceParse, parse_price

# (原文, 期望分)
VALID_CASES = [
    ("3500", 350_000),            # [指南]
    ("¥3500", 350_000),           # [指南]
    ("¥3,500", 350_000),          # [指南] 千分位
    ("3,500.00", 350_000),        # [指南]
    ("0.35万", 350_000),          # [指南] 万
    ("¥5499", 549_900),           # [实测] id 1083967235157
    ("¥5642.50", 564_250),        # [实测] id 1085916193246，sign+integer+decimal 三段拼接
    ("¥90", 9_000),               # [实测] id 1086948380894（租赁盘，分类阶段另行排除）
    ("￥3500", 350_000),          # 全角人民币符号
    ("3500元", 350_000),
    ("1,234,567.89", 123_456_789),
]


@pytest.mark.parametrize("text,expected_fen", VALID_CASES)
def test_valid_price_parses_to_exact_fen(text: str, expected_fen: int) -> None:
    result = parse_price(text)

    assert result.status == "valid"
    assert result.price_fen == expected_fen
    assert result.currency == "CNY"


@pytest.mark.parametrize("text,expected_fen", VALID_CASES)
def test_original_text_always_recoverable(text: str, expected_fen: int) -> None:
    """§Phase2 验收：价格原文始终可还原。"""
    assert parse_price(text).price_raw == text


def test_wan_uses_decimal_not_float() -> None:
    """float(1.15)*10000 == 11499.999999999998 → 会算出 1149999 分。必须精确。"""
    assert parse_price("1.15万").price_fen == 1_150_000


def test_two_decimal_places_do_not_truncate() -> None:
    """int(float('1154.99')*100) == 115498，少一分。必须精确。"""
    assert parse_price("¥1154.99").price_fen == 115_499


AMBIGUOUS_CASES = [
    "1元占位",        # [指南]
    "定金200",        # [指南]
    "价格私聊",       # [指南]
    "面议",           # [指南]
    "3500起",         # [指南]
    "3500-4500",      # [指南] 区间
    "3500~4500",      # 区间变体
    "3500到4500",     # 区间变体
    "不包邮",         # [指南] 未确认运费
    "押金500",
    "约3500",
    "3500可小刀",
    "50元/天",        # 租赁日租价，非在售总价
]


@pytest.mark.parametrize("text", AMBIGUOUS_CASES)
def test_ambiguous_price_is_never_a_number(text: str) -> None:
    """§7：这些不能当成完整商品总价，不参与合格样本统计。"""
    result = parse_price(text)

    assert result.status == "ambiguous"
    assert result.price_fen is None
    assert result.price_raw == text


@pytest.mark.parametrize("text", ["¥0", "0", "0元"])
def test_zero_price_is_ambiguous_not_valid(text: str) -> None:
    """0 元挂牌不是有意义的在售报价，不能进统计。"""
    result = parse_price(text)

    assert result.status == "ambiguous"
    assert result.price_fen is None


INVALID_CASES = [
    "价格异常",   # [上游] handle_data 的字符串哨兵，绝不能变成 0
    "暂无",       # [上游] safe_get 的占位伪数据，绝不能变成数字
    "未知",
    "abc",
    "",
    "   ",
    "¥-500",
    "¥",
]


@pytest.mark.parametrize("text", INVALID_CASES)
def test_unparseable_price_is_null_never_zero(text: str) -> None:
    """§7：非法/无法解析价格设置 price_fen=null，不得转换为 0。"""
    result = parse_price(text)

    assert result.status in {"invalid", "missing"}
    assert result.price_fen is None
    assert result.currency is None


def test_none_input_is_missing() -> None:
    result = parse_price(None)

    assert result.status == "missing"
    assert result.price_fen is None
    assert result.price_raw is None
    assert result.currency is None


def test_blank_input_is_missing_not_invalid() -> None:
    """区分「平台没给价格」与「给了但解析不了」，便于统计采集质量。"""
    assert parse_price("   ").status == "missing"
    assert parse_price("abc").status == "invalid"


def test_price_parse_is_frozen_and_typed() -> None:
    result = parse_price("¥3500")

    assert isinstance(result, PriceParse)
    with pytest.raises(Exception):
        result.price_fen = 1  # type: ignore[misc]
