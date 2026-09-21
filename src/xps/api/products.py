"""GET /v1/products（指南 §8.3）：本轮真实商品，可分页。

本端点是这个服务的主要产出：平台字段清洗后原样透出，判断交给调用方。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from xps.api.deps import get_repo, load_usable_run, reject_removed_filters
from xps.api.schemas import (
    PRODUCTS_DEFAULT_LIMIT,
    PRODUCTS_MAX_LIMIT,
    ListingSignals,
    MediaInfo,
    PriceInfo,
    ProductItem,
    ProductPage,
    SellerInfo,
)
from xps.services.normalize import fen_to_yuan
from xps.storage.repository import ObservationRecord, Repository

router = APIRouter(prefix="/v1", tags=["products"])


def _to_item(row: ObservationRecord) -> ProductItem:
    return ProductItem(
        product_id=row.product_id,
        source_run_id=row.run_id,
        observed_at=row.observed_at,
        canonical_url=row.canonical_url,
        title=row.title,
        description=row.description,
        price=PriceInfo(
            raw=row.price_raw,
            yuan=fen_to_yuan(row.price_fen),
            fen=row.price_fen,
            parse_status=row.price_parse_status,
            original_text=row.original_price_text,
            coupon_text=row.coupon_text,
        ),
        seller=SellerInfo(
            display_name=row.seller_display_name,
            credit=row.seller_credit,
            review_count=row.seller_review_count,
            positive_rate=row.seller_positive_rate,
            identity=row.seller_identity,
            avatar_url=row.seller_avatar_url,
        ),
        area=row.area,
        media=MediaInfo(image_url=row.image_url, has_video=row.has_video),
        published_at=row.published_at,
        signals=ListingSignals(
            published_text=row.published_text,
            want_count=row.want_count,
            free_shipping=row.free_shipping,
            labels=list(row.labels),
            is_auction=row.is_auction,
            is_ad=row.is_ad,
        ),
    )


@router.get(
    "/products",
    response_model=ProductPage,
    summary="本轮搜索到的商品（清洗后原样透出，不做相关性筛选）",
    description=(
        "只返回该 run_id 本轮实际观察到的商品。可见字段缺失即为 null，"
        "不会插入「暂无」之类的占位伪数据。\n\n"
        "租赁盘、拍卖、配件、广告位**都会照常返回**：`signals` 里给出平台自报的事实标记，"
        "是不是可比样本由调用方判断。`priced_only=true` 只是「价格能解析成数字」，"
        "不是相关性筛选；被它挡掉的条目仍能用 `priced_only=false` 取到价格原文。"
    ),
)
async def list_products(
    request: Request,
    run_id: str = Query(..., description="POST /v1/search 返回的 run_id"),
    priced_only: bool = Query(False, description="只看价格可解析成数字的条目"),
    limit: int = Query(PRODUCTS_DEFAULT_LIMIT, ge=1, le=PRODUCTS_MAX_LIMIT),
    offset: int = Query(0, ge=0),
    repo: Repository = Depends(get_repo),
) -> ProductPage:
    reject_removed_filters(request)
    run = load_usable_run(repo, run_id)
    rows, total = repo.run_products(
        run_id, priced_only=priced_only, limit=limit, offset=offset
    )
    return ProductPage(
        run_id=run_id,
        run_status=run.status,
        partial=run.status == "partial",
        items=[_to_item(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )
