"""价格原文 → 人民币分。

口径：采集时刻的**公开在售报价**。不是成交价，不含国补/优惠券/议价结果。

全程 Decimal，绝不出现 float：
    float(1.15) * 10000      == 11499.999999999998   → 少一分
    int(float("1154.99")*100) == 115498              → 少一分
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import TYPE_CHECKING

from xps.services.classify import (
    INVALID_IDENTITY as INVALID_IDENTITY_FLAG,
    Classification,
    ModelSpec,
    classify,
)
from xps.services.identity import INVALID_IDENTITY as INVALID_ID_SOURCE
from xps.services.identity import Identity, resolve_identity

if TYPE_CHECKING:  # 仅注解用：避免 service 层在运行时反向依赖 adapter 层
    from xps.adapters.base import RawListing

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
    """RawListing 经身份/价格/相关性处理后的可入库形态。"""

    identity: Identity
    title: str | None
    price_raw: str | None
    price_fen: int | None
    price_status: str
    currency: str | None
    area: str | None
    seller_display_name: str | None
    image_url: str | None
    published_at: str | None
    observed_at: datetime
    classification: Classification
    raw_payload: dict | None
    # 既无平台 ID 又无可信 URL 的条目无法安全去重，不硬塞进 products
    storable: bool


def normalize_listing(
    raw: RawListing,
    *,
    spec: ModelSpec,
    observed_at: datetime,
) -> NormalizedListing:
    identity = resolve_identity(raw.source_id, raw.url)
    price = parse_price(raw.price_text)
    classification = classify(
        raw.title or "",
        spec=spec,
        is_auction=raw.is_auction,
        is_ad=raw.is_ad,
    )

    if identity.id_source == INVALID_ID_SOURCE and INVALID_IDENTITY_FLAG not in classification.flags:
        # 身份不可信 → 不进可信统计（指南 §7），但仍保留记录以便追溯
        classification = Classification(
            flags=classification.flags + (INVALID_IDENTITY_FLAG,),
            item_kind=classification.item_kind,
            excluded=True,
            exclusion_reasons=tuple(
                sorted(set(classification.exclusion_reasons) | {INVALID_IDENTITY_FLAG})
            ),
            needs_review=False,
        )

    return NormalizedListing(
        identity=identity,
        title=raw.title,
        price_raw=price.price_raw,
        price_fen=price.price_fen,
        price_status=price.status,
        currency=price.currency,
        area=raw.area,
        seller_display_name=raw.seller_name,
        image_url=raw.image_url,
        published_at=raw.published_at,
        observed_at=observed_at,
        classification=classification,
        raw_payload=raw.raw_payload,
        storable=identity.id_source != INVALID_ID_SOURCE or bool((raw.url or "").strip()),
    )
