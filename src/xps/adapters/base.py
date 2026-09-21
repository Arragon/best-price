"""平台适配器契约。

指南 §3 要求契约定义在自有代码中，不要求上游已有。当前只有闲鱼一个平台，
刻意不建 plugins/factories 多级框架。

相对指南样例的两处有意偏离：
- 缺失字段用 `str | None` 而非 `str`：§0.4 禁止用占位值冒充真实数据。
- 序列用 tuple 而非 list：与其余 frozen dataclass 保持一致的不可变性。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable

AUTH_LOGGED_IN = "logged_in"
AUTH_GUEST = "guest"
AUTH_EXPIRED = "expired"
AUTH_UNKNOWN = "unknown"
AUTH_HUMAN_ACTION = "human_action_required"

SORT_OPTIONS = ("newest", "price_asc", "price_desc", "default")


@dataclass(frozen=True)
class RawListing:
    """上游返回的原始条目，未做业务判断。"""

    source_id: str | None
    url: str
    title: str | None
    price_text: str | None
    seller_name: str | None = None
    area: str | None = None
    image_url: str | None = None
    published_at: str | None = None
    # 实测 schema 里确实存在的两个字段：起拍价与广告位都不是普通在售报价
    is_auction: bool = False
    is_ad: bool = False
    raw_payload: dict | None = None


@dataclass(frozen=True)
class PageOutcome:
    """逐页成败与该页商品。scrape_xianyu_http 的 gather 拿不到这个粒度，故自行逐页采集。"""

    page: int
    fetched: bool
    listings: tuple[RawListing, ...] = ()
    error_code: str | None = None
    error_message: str | None = None

    @property
    def item_count(self) -> int:
        return len(self.listings)


@dataclass(frozen=True)
class CrawlResult:
    auth_mode: str
    pages_requested: int
    pages: tuple[PageOutcome, ...]
    warnings: tuple[str, ...] = ()
    has_next_page: bool | None = None

    @property
    def listings(self) -> tuple[RawListing, ...]:
        return tuple(listing for page in self.pages for listing in page.listings)

    @property
    def pages_fetched(self) -> int:
        return sum(1 for page in self.pages if page.fetched)


@dataclass(frozen=True)
class AuthStatus:
    state: str
    requires_human_action: bool = False
    hint: str | None = None


@runtime_checkable
class XianyuAdapter(Protocol):
    async def search(
        self,
        keyword: str,
        max_pages: int,
        sort: str,
        min_price: Decimal | None,
        max_price: Decimal | None,
        city: str | None,
    ) -> CrawlResult: ...

    async def auth_status(self) -> AuthStatus: ...
