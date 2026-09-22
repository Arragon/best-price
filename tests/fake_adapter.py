"""⚠️ 测试替身适配器 —— 返回的全部是合成商品。

这里的任何价格、标题、卖家都**不是真实闲鱼报价**，只用于离线驱动 API 契约测试。
刻意放在 tests/ 而非 src/：避免任何人误把 fake 数据当成真实市场结果（指南 §0.4）。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from decimal import Decimal

from xps.adapters.base import (
    AUTH_GUEST,
    AuthStatus,
    CrawlResult,
    PageOutcome,
    RawListing,
)
from xps.adapters.xianyu import entry_to_raw_listing
from tests.fixtures.mtop_entry import make_entry


def price_parts(price_text: str) -> tuple[tuple[str, str], ...]:
    """把 '5642.50' 拆成实测那种 sign/integer/decimal 三段。"""
    integer, _, decimal = price_text.partition(".")
    parts: list[tuple[str, str]] = [("sign", "¥"), ("integer", integer)]
    if decimal:
        parts.append(("decimal", f".{decimal}"))
    return tuple(parts)


def synthetic_listing(
    item_id: str,
    *,
    title: str = "合成 富士 X-T4 单机身",
    price_text: str = "5499",
    area: str | None = "上海",
    nick: str | None = "合成卖家",
    publish_time: str | None = "1789989232000",
    is_auction: bool = False,
    is_ad: bool = False,
    **entry_kwargs,
) -> RawListing:
    """entry_kwargs 直接透传给 make_entry，用来造带信用/描述/标签的合成商品。"""
    return entry_to_raw_listing(
        make_entry(
            item_id=item_id,
            title=title,
            price=price_parts(price_text) if price_text else None,
            area=area,
            nick=nick,
            publish_time=publish_time,
            is_auction=is_auction,
            is_ad=is_ad,
            target_url=f"fleamarket://item?id={item_id}&gulSource=search",
            **entry_kwargs,
        )
    )


def body_listings(start_id: int, count: int, base_price: int = 4800) -> list[RawListing]:
    """合成 count 件「正常单机身」，价格递增，便于断言分位数。"""
    return [
        synthetic_listing(
            str(start_id + index),
            title="合成 富士 X-T4 单机身",
            price_text=str(base_price + index * 100),
        )
        for index in range(count)
    ]


@dataclass
class FakeAdapter:
    """按页返回预置合成商品；可注入指定页失败与延迟。"""

    pages: list[list[RawListing]] = field(default_factory=list)
    auth_mode: str = AUTH_GUEST
    page_errors: dict[int, str] = field(default_factory=dict)
    verified_empty: bool = False
    delay: float = 0.0
    auth_error: Exception | None = None
    exhaust_after_page: int | None = None

    calls: list[dict] = field(default_factory=list)
    active: int = 0
    max_active: int = 0
    reload_calls: int = 0

    async def search(
        self,
        keyword: str,
        max_pages: int,
        sort: str,
        min_price: Decimal | None,
        max_price: Decimal | None,
        city: str | None,
    ) -> CrawlResult:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            self.calls.append(
                {
                    "keyword": keyword,
                    "max_pages": max_pages,
                    "sort": sort,
                    "min_price": min_price,
                    "max_price": max_price,
                    "city": city,
                }
            )
            if self.delay:
                await asyncio.sleep(self.delay)

            outcomes: list[PageOutcome] = []
            warnings: list[str] = []
            for page in range(1, max_pages + 1):
                if self.exhaust_after_page is not None and page > self.exhaust_after_page:
                    outcomes.append(
                        PageOutcome(page, False, (), outcome_kind="exhausted")
                    )
                    break
                error = self.page_errors.get(page)
                if error:
                    outcomes.append(PageOutcome(page, False, (), error, "合成失败"))
                    warnings.append(f"page_{page}_failed:{error}")
                    continue
                listings = self.pages[page - 1] if page - 1 < len(self.pages) else []
                outcomes.append(PageOutcome(page, True, tuple(listings)))
                if not listings and self.verified_empty:
                    warnings.append(f"page_{page}_verified_empty")

            return CrawlResult(
                auth_mode=self.auth_mode,
                pages_requested=max_pages,
                pages=tuple(outcomes),
                warnings=tuple(warnings),
                has_next_page=(
                    False
                    if self.exhaust_after_page is not None
                    else len(self.pages) > max_pages
                ),
            )
        finally:
            self.active -= 1

    async def auth_status(self) -> AuthStatus:
        if self.auth_error is not None:
            raise self.auth_error
        return AuthStatus(self.auth_mode)

    async def reload(self) -> AuthStatus:
        self.reload_calls += 1
        return await self.auth_status()
