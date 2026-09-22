"""Verified quote import and explicit SKU matching; no mock prices enter storage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from xps.errors import (
    INVALID_QUERY,
    RETAIL_MOCK_REJECTED,
    RETAIL_NOT_CONFIGURED,
    SKU_MISMATCH,
    ServiceError,
)
from xps.storage.evaluation_repository import EvaluationRepository
from xps.storage.retail_repository import RetailRepository

router = APIRouter(prefix="/v1", tags=["retail-prices"])

_PLATFORM_HOSTS = {
    "jd": ("jd.com", "jd.hk"),
    "taobao": ("taobao.com",),
    "tmall": ("tmall.com",),
    "pdd": ("pinduoduo.com", "yangkeduo.com"),
}


class QuoteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    platform: Literal["jd", "taobao", "tmall", "pdd"]
    source_kind: Literal[
        "official_api", "api_verified", "browser_observed", "agent_submitted",
        "manual_user", "mock_test", "dry_run"
    ]
    external_listing_id: str | None = None
    canonical_product_url: str
    sku_key: str = Field(min_length=1, max_length=300)
    brand: str | None = None
    model: str = Field(min_length=1, max_length=200)
    variant: str | None = None
    bundle: list[str] = Field(default_factory=list)
    listed_price_fen: int | None = Field(default=None, ge=0)
    payable_price_fen: int | None = Field(default=None, ge=0)
    shipping_price_fen: int | None = Field(default=None, ge=0)
    price_conditions: list[str] = Field(default_factory=list)
    eligibility_context: list[str] = Field(default_factory=list)
    stock_status: str = "unknown"
    verification_status: Literal["verified", "conditional", "unverified", "stale"]
    observed_at: datetime

    @field_validator("canonical_product_url")
    @classmethod
    def safe_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("报价 URL 必须是无凭据的 https URL")
        identity_query = urlencode([
            (key, item) for key, item in parse_qsl(parsed.query)
            if key in {"id", "itemId", "skuId"}
        ])
        return urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path, identity_query, ""))

    @model_validator(mode="after")
    def platform_host_and_price(self):
        host = (urlsplit(self.canonical_product_url).hostname or "").lower()
        allowed = _PLATFORM_HOSTS.get(self.platform)
        if allowed and not any(host == suffix or host.endswith("." + suffix) for suffix in allowed):
            raise ValueError("URL host 与 platform 不匹配")
        if self.listed_price_fen is None and self.payable_price_fen is None:
            raise ValueError("至少提供 listed_price_fen 或 payable_price_fen")
        return self


class QuoteMatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    quote_id: str
    match_status: Literal["exact", "equivalent_adjusted", "incomparable", "unknown"]
    used_shipping_price_fen: int | None = Field(default=None, ge=0)
    mandatory_used_fees_fen: int | None = Field(default=None, ge=0)


@router.post("/new-prices/quotes", status_code=201, summary="导入可追溯新品报价")
async def import_quote(payload: QuoteInput, request: Request):
    if payload.source_kind in {"mock_test", "dry_run"}:
        raise ServiceError(RETAIL_MOCK_REJECTED, "Mock/Dry-run 报价禁止进入正式报价表")
    return RetailRepository(request.app.state.conn).import_quote(payload.model_dump(mode="json"))


@router.get("/new-prices", summary="按 SKU 查询新品报价及新鲜度")
async def list_quotes(
    request: Request,
    sku_key: str = Query(min_length=1, max_length=300),
    max_age_hours: int = Query(default=24, ge=1, le=720),
):
    quotes = RetailRepository(request.app.state.conn).list_for_sku(sku_key)
    cutoff = datetime.now(UTC) - timedelta(hours=max_age_hours)
    for quote in quotes:
        observed = datetime.fromisoformat(str(quote["observed_at"]))
        quote["freshness_status"] = "fresh" if observed.astimezone(UTC) >= cutoff else "stale"
    return {"sku_key": sku_key, "quotes": quotes, "automatic_adapter_status": "not_configured"}


@router.post("/new-prices/search", summary="请求已启用的自动零售适配器")
async def search_new_prices():
    raise ServiceError(
        RETAIL_NOT_CONFIGURED,
        "尚无通过许可证、凭据、SKU 与真实价格闸门的自动零售适配器；请使用 Quote Import",
    )


@router.post("/evaluations/{evaluation_id}/quote-match", summary="记录 SKU 匹配并在成本完整时计算价差")
async def match_quote(evaluation_id: str, payload: QuoteMatchInput, request: Request):
    try:
        return EvaluationRepository(request.app.state.conn).match_quote(
            evaluation_id, payload.quote_id, payload.match_status,
            payload.used_shipping_price_fen, payload.mandatory_used_fees_fen,
        )
    except KeyError as exc:
        raise ServiceError(INVALID_QUERY, "evaluation_id 或 quote_id 不存在", status_code=404) from exc
    except ValueError as exc:
        raise ServiceError(SKU_MISMATCH, "exact 匹配要求 evaluation 与 quote 的 sku_key 相同") from exc
