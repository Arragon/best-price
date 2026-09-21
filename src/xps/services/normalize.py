"""平台条目 → 可入库形态：身份去重 + 价格解析，**不做相关性判断**。

本服务只负责采集与清洗，不替调用方决定哪条商品「可比」。
分类筛选层已移除：它靠词表猜品类，换个品类就大面积误杀有效样本，
而这些判断调用方（agent）拿着原始字段自己做要准得多。

保留的两件事都不是筛选：
- `resolve_identity`：跨轮去重要靠稳定身份键，否则同一件商品会被重复计入
- `parse_price`：把「¥5642.50」「面议」「¥90/天」变成机器可读形态。
  解析不出来时**保留原文**并标注状态，条目照样返回给调用方，不删除。

价格口径：采集时刻的**公开在售报价**。不是成交价，不含国补/优惠券/议价结果。
若平台标了券抵扣（`coupon_text`），展示价可能已扣券——原文一并透出，由调用方判断。

全程 Decimal，绝不出现 float：
    float(1.15) * 10000      == 11499.999999999998   → 少一分
    int(float("1154.99")*100) == 115498              → 少一分
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from xps.adapters.base import RawListing
from xps.services.identity import INVALID_IDENTITY as INVALID_ID_SOURCE
from xps.services.identity import Identity, resolve_identity

VALID = "valid"
AMBIGUOUS = "ambiguous"
MISSING = "missing"
INVALID = "invalid"

_CURRENCY_EDGES = "¥￥元 \t\r\n"
_WAN = Decimal(10_000)
_HUNDRED = Decimal(100)
_ZERO = Decimal(0)

# 出现即不能当作完整商品总价（指南 §7）。
# 价格文本只来自价格控件，不含标题，故单字标记不会误伤正常数字。
_AMBIGUOUS_MARKERS = (
    "定金",
    "订金",
    "押金",
    "占位",
    "面议",
    "电议",
    "私聊",
    "私信",
    "议价",
    "小刀",
    "详询",
    "咨询",
    "联系",
    "看图",
    "待定",
    "约",
    "起",
    "不包邮",
    "运费",
    "邮费",
    "租",
    "/天",
    "/月",
    "每天",
    "每月",
)

_RANGE = re.compile(r"\d\s*(?:-|~|～|—|至|到)\s*\d")


@dataclass(frozen=True)
class PriceParse:
    price_raw: str | None
    price_fen: int | None
    status: str
    currency: str | None


def fen_to_yuan(fen: int | None) -> str | None:
    """分 → 保留两位的人民币字符串。金额以字符串出 API，避免 JSON 浮点失真。"""
    if fen is None:
        return None
    return str((Decimal(fen) / _HUNDRED).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _to_decimal(text: str) -> Decimal | None:
    multiplier = Decimal(1)
    working = text
    if "万" in working:
        working = working.replace("万", "")
        multiplier = _WAN
    working = working.replace(",", "").replace("，", "")
    working = working.strip(_CURRENCY_EDGES)
    if not working:
        return None
    try:
        return Decimal(working) * multiplier
    except InvalidOperation:
        return None


def parse_price(text: str | None) -> PriceParse:
    if text is None:
        return PriceParse(None, None, MISSING, None)

    stripped = text.strip()
    if not stripped:
        # 平台没给价格，与「给了但解析不了」区分开，便于统计采集质量
        return PriceParse(text, None, MISSING, None)

    if _RANGE.search(stripped) or any(marker in stripped for marker in _AMBIGUOUS_MARKERS):
        return PriceParse(text, None, AMBIGUOUS, None)

    value = _to_decimal(stripped)
    if value is None or value < _ZERO:
        return PriceParse(text, None, INVALID, None)
    if value == _ZERO:
        return PriceParse(text, None, AMBIGUOUS, None)

    fen = int((value * _HUNDRED).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return PriceParse(text, fen, VALID, "CNY")


@dataclass(frozen=True)
class NormalizedListing:
    """平台原样字段 + 身份 + 价格解析结果。不携带任何相关性判断。"""

    raw: RawListing
    identity: Identity
    price: PriceParse
    observed_at: datetime
    # 既无平台 ID 又无可信 URL 的条目无法安全去重（identity_key 会碰撞成一行，
    # 静默吞掉数据），故不入库，只计数上报。这是数据完整性约束，不是相关性筛选。
    storable: bool


def normalize_listing(raw: RawListing, *, observed_at: datetime) -> NormalizedListing:
    identity = resolve_identity(raw.source_id, raw.url)
    return NormalizedListing(
        raw=raw,
        identity=identity,
        price=parse_price(raw.price_text),
        observed_at=observed_at,
        storable=identity.id_source != INVALID_ID_SOURCE or bool((raw.url or "").strip()),
    )
