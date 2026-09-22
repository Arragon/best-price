"""外部 API 数据类型（指南 §8）。

金额以**保留两位的人民币字符串**出 API，避免 JSON 浮点失真；内部一律整数分。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SortOption = Literal["newest", "price_asc", "price_desc", "default"]
PaceOption = Literal["economy", "balanced", "fast"]
CachePolicy = Literal["prefer_fresh", "force_refresh"]

KEYWORD_MAX_LENGTH = 64
PRODUCTS_DEFAULT_LIMIT = 50
PRODUCTS_MAX_LIMIT = 100

# 上游 SearchFilters 支持这三项，但**平台是否真过滤未经实测验证**。
# 声明出来是为了给出明确的 UNSUPPORTED_FILTER，而不是静默忽略或谎称已过滤。
UNVERIFIED_FILTERS = ("city", "province", "publish_days")


class SearchSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keyword: str = Field(min_length=1, max_length=KEYWORD_MAX_LENGTH)
    # 上限由 MAX_SEARCH_PAGES 配置决定，在端点里校验；此处只挡明显非法值
    max_pages: int = Field(1, ge=1)
    sort: SortOption = "newest"
    min_price_yuan: Decimal | None = Field(default=None, ge=0)
    max_price_yuan: Decimal | None = Field(default=None, ge=0)
    pace: PaceOption = "balanced"
    cache_policy: CachePolicy = "prefer_fresh"
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=128)

    # 上游 SearchFilters 支持这三项，但平台是否真过滤**未经实测验证**，
    # 故声明出来只为给出明确的 UNSUPPORTED_FILTER，而不是静默忽略。
    city: str | None = None
    province: str | None = None
    publish_days: int | None = Field(default=None, ge=1, le=180)

    @field_validator("keyword")
    @classmethod
    def _keyword_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("keyword 不能只含空白字符")
        return stripped

    @model_validator(mode="after")
    def _price_range_ordered(self) -> SearchSubmitRequest:
        if (
            self.min_price_yuan is not None
            and self.max_price_yuan is not None
            and self.min_price_yuan > self.max_price_yuan
        ):
            raise ValueError("min_price_yuan 不能大于 max_price_yuan")
        return self

    def unverified_filters(self) -> list[str]:
        return [name for name in UNVERIFIED_FILTERS if getattr(self, name) is not None]


class SearchAccepted(BaseModel):
    run_id: str
    status: str
    status_url: str
    reused: bool = False
    cache_hit: bool = False


class RunError(BaseModel):
    code: str
    message: str | None
    retryable: bool
    requires_human_action: bool


class SearchRunResponse(BaseModel):
    run_id: str
    status: str
    platform: str
    keyword: str
    auth_mode: str | None
    pages_requested: int
    pages_fetched: int
    raw_count: int
    distinct_count: int
    # 去重后价格可解析的条目数。不叫 eligible：本服务不做合格性筛选。
    priced_count: int
    started_at: str
    ended_at: str | None
    warnings: list[str]
    error: RunError | None
    # 采集来源可追溯：事后能判断这轮数据是哪个适配器/上游版本抓的
    adapter_version: str | None = None
    source_commit: str | None = None
    exhausted: bool = False
    request_fingerprint: str | None = None


class SellerInfo(BaseModel):
    """卖家侧的平台自报信息。缺失即 null，不做任何推断或补齐。"""

    display_name: str | None
    # 平台信用标签原文，实测取值「卖家信用极好」「卖家信用优秀」（60 条中 56 条给出）
    credit: str | None
    review_count: int | None
    # 「好评率39%」→「39%」
    positive_rate: str | None
    # 平台身份标识原文，如「闲鱼严选卖家」
    identity: str | None
    avatar_url: str | None


class PriceInfo(BaseModel):
    """采集时刻的公开在售报价。不是成交价，不含国补 / 优惠券 / 议价结果。"""

    # 平台价格控件原文，如「¥5642.50」「面议」「¥90」
    raw: str | None
    yuan: str | None
    fen: int | None
    # valid / ambiguous（面议、区间、租金等）/ missing（平台没给）/ invalid（给了但解析不了）
    parse_status: str
    # 划线原价原文；实测仅少数条目给出
    original_text: str | None
    # 平台券标签原文，如「券已抵50元」。展示价可能已扣券，属价格口径的一部分。
    coupon_text: str | None


class MediaInfo(BaseModel):
    # 搜索响应每个商品只给 1 张主图。多图需商品详情接口，实测 guest 调用
    # mtop.taobao.idle.pc.detail 返回 RGV587 风控挑战，故只有单图。
    image_url: str | None
    has_video: bool


class ListingSignals(BaseModel):
    """平台事实标记。**不是**本服务的排除依据——要不要采信由调用方判断。"""

    # 平台自报的相对时间原文，如「8小时前发布」
    published_text: str | None
    want_count: int | None
    free_shipping: bool
    # 徽标原文，实测取值「严选」「验货宝」
    labels: list[str]
    # 起拍价不是普通在售报价
    is_auction: bool
    # 广告位不是自然搜索结果
    is_ad: bool


class ProductItem(BaseModel):
    """一件商品的清洗后快照：平台真实给出的字段原样透出，附加本服务的解析结果。"""

    product_id: int
    source_run_id: str
    observed_at: str
    # 已剥除跟踪参数的可打开链接
    canonical_url: str | None
    # 搜索页展示的单行标题（实测平台从不在这里放换行）
    title: str | None
    # 同一篇挂牌文字，但保留换行分段，最长约 1500 字。判断是不是租赁/配件/求购看这个。
    description: str | None
    price: PriceInfo
    seller: SellerInfo
    area: str | None
    media: MediaInfo
    published_at: str | None
    signals: ListingSignals


class ProductPage(BaseModel):
    run_id: str
    run_status: str
    partial: bool
    items: list[ProductItem]
    total: int
    limit: int
    offset: int


class PricedItem(BaseModel):
    product_id: int
    title: str
    canonical_url: str | None
    price_yuan: str


class StatsResponse(BaseModel):
    """**未筛选**的算术结果：租赁盘、拍卖起拍价、配件、广告位全都在样本里。

    要看构成就配 /v1/products 一起读；sample_quality 里恒含 `unfiltered`，
    转述时不得省略。
    """

    run_id: str
    keyword: str
    run_status: str
    currency: str
    partial: bool
    auth_mode: str | None

    raw_count: int
    distinct_count: int
    # 参与算术的条目数（去重后 price_parse_status=valid）
    priced_count: int
    # 未参与算术的条目数，按解析状态分组；这些条目仍可从 /v1/products 取到原文
    unpriced_count: int
    unpriced_by_status: dict[str, int]
    auction_count: int
    ad_count: int

    min_yuan: str | None
    p25_yuan: str | None
    median_yuan: str | None
    p75_yuan: str | None
    max_yuan: str | None
    min_fen: int | None
    p25_fen: int | None
    median_fen: int | None
    p75_fen: int | None
    max_fen: int | None

    lowest_items: list[PricedItem]
    highest_items: list[PricedItem]
    insufficient_sample: bool
    sample_quality: list[str]
    started_at: str
    ended_at: str | None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["ok", "error"]
    version: str
    adapter: str


class AuthStatusResponse(BaseModel):
    state: str
    requires_human_action: bool
    hint: str | None = None
    # False 表示只读了本地凭证、未向平台主动校验（主动校验会触发上游销毁凭证的分支）
    verified: bool = False


class ErrorResponse(BaseModel):
    code: str
    message: str
    run_id: str | None = None
    retryable: bool
    requires_human_action: bool
