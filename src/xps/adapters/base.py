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
    """上游返回的原始条目，未做业务判断。

    本服务**不做相关性筛选**：这里只负责把平台真实给出的字段抽出来，
    缺失一律为 None（不填占位值），判断交给调用方。
    字段取值范围以 2026-09-22 抓取的 60 条真实搜索结果为据，见各字段注释。
    """

    source_id: str | None
    url: str
    title: str | None
    price_text: str | None
    seller_name: str | None = None
    area: str | None = None
    image_url: str | None = None
    published_at: str | None = None
    # 实测 schema 里确实存在的两个字段：起拍价与广告位都不是普通在售报价。
    # 保留为**事实标记**而非排除依据——调用方自己决定怎么看待它们。
    is_auction: bool = False
    is_ad: bool = False
    raw_payload: dict | None = None

    # detailParams.title：与 exContent.title 同文，但保留换行分段（实测 42/60 含换行，
    # exContent.title 0/60）。挂牌描述常达 1500 字，分段版本才可读。
    description: str | None = None
    # exContent.oriPrice，划线原价；实测仅 6/30 条给出
    original_price_text: str | None = None

    seller_avatar_url: str | None = None
    # exContent.userIdentityShow，如「闲鱼严选卖家」；实测 5/30 条给出
    seller_identity: str | None = None
    # fishTags.r4，如「卖家信用极好」「卖家信用优秀」；实测 56/60 条给出
    seller_credit: str | None = None
    # userFishShopLabel「318条评价」→ 318
    seller_review_count: int | None = None
    # userFishShopLabel「好评率39%」→「39%」
    seller_positive_rate: str | None = None

    # fishTags.r2，如「8小时前发布」——平台自报的相对时间，与 published_at 各自独立
    published_text: str | None = None
    # fishTags.r3「8人想要」→ 8
    want_count: int | None = None
    # fishTags.r3「券已抵50元」。关系价格口径：展示价可能已扣券，必须让调用方看见
    coupon_text: str | None = None
    # fishTags.r1 的 freeShippingIcon
    free_shipping: bool = False
    # fishTags.r1 其余标签，实测取值为「严选」「验货宝」
    labels: tuple[str, ...] = ()
    has_video: bool = False


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
    # 是否已向平台主动校验过。本项目默认 False：主动校验会触发上游销毁凭证的分支
    verified: bool = False


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

    async def reload(self) -> AuthStatus:
        """重新加载本地凭证（用户在别处登录后调用，免重启）。"""
        ...
