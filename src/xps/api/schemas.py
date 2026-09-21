"""外部 API 数据类型（指南 §8）。

金额以**保留两位的人民币字符串**出 API，避免 JSON 浮点失真；内部一律整数分。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ItemKind = Literal["body", "kit", "any"]
SortOption = Literal["newest", "price_asc", "price_desc", "default"]

KEYWORD_MAX_LENGTH = 64
PRODUCTS_DEFAULT_LIMIT = 50
PRODUCTS_MAX_LIMIT = 100


class SearchSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keyword: str = Field(min_length=1, max_length=KEYWORD_MAX_LENGTH)
    max_pages: int = Field(1, ge=1, le=3)
    sort: SortOption = "newest"
    min_price_yuan: Decimal | None = Field(default=None, ge=0)
    max_price_yuan: Decimal | None = Field(default=None, ge=0)
    item_kind: ItemKind = "any"

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
        return [
            name
            for name, value in (
                ("city", self.city),
                ("province", self.province),
                ("publish_days", self.publish_days),
            )
            if value is not None
        ]


class SearchAccepted(BaseModel):
    run_id: str
    status: str
    status_url: str


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
    eligible_count: int
    started_at: str
    ended_at: str | None
    warnings: list[str]
    error: RunError | None


class ProductItem(BaseModel):
    product_id: int
    title: str | None
    canonical_url: str | None
    price_text: str | None
    price_yuan: str | None
    observed_at: str
    published_at: str | None
    area: str | None
    seller_display_name: str | None
    image_url: str | None
    flags: list[str]
    item_kind: str | None
    excluded: bool
    exclusion_reasons: list[str]
    needs_review: bool
    price_parse_status: str
    source_run_id: str


class ProductPage(BaseModel):
    run_id: str
    run_status: str
    partial: bool
    items: list[ProductItem]
    total: int
    limit: int
    offset: int


class LowestItem(BaseModel):
    product_id: int
    title: str
    canonical_url: str | None
    price_yuan: str


class StatsResponse(BaseModel):
    run_id: str
    keyword: str
    run_status: str
    item_kind: str
    currency: str
    partial: bool
    auth_mode: str | None

    raw_count: int
    distinct_count: int
    eligible_count: int
    excluded_count: int
    excluded_by_reason: dict[str, int]
    needs_review_count: int
    suspicious_price_count: int

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

    lowest_items: list[LowestItem]
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


class ErrorResponse(BaseModel):
    code: str
    message: str
    run_id: str | None = None
    retryable: bool
    requires_human_action: bool
