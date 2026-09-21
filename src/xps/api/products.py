"""GET /v1/products（指南 §8.3）：本轮真实商品，可分页。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from xps.api.deps import get_repo, load_usable_run
from xps.api.schemas import (
    PRODUCTS_DEFAULT_LIMIT,
    PRODUCTS_MAX_LIMIT,
    ProductItem,
    ProductPage,
)
from xps.services.normalize import fen_to_yuan
from xps.storage.repository import Repository

router = APIRouter(prefix="/v1", tags=["products"])


@router.get(
    "/products",
    response_model=ProductPage,
    summary="本轮搜索到的商品",
    description=(
        "只返回该 run_id 本轮实际观察到的商品。可见字段缺失即为 null，"
        "不会插入「暂无」之类的占位伪数据。"
    ),
)
async def list_products(
    run_id: str = Query(..., description="POST /v1/search 返回的 run_id"),
    eligible_only: bool = Query(False, description="只看可进入价格统计的样本"),
    limit: int = Query(PRODUCTS_DEFAULT_LIMIT, ge=1, le=PRODUCTS_MAX_LIMIT),
    offset: int = Query(0, ge=0),
    repo: Repository = Depends(get_repo),
) -> ProductPage:
    run = load_usable_run(repo, run_id)
    rows, total = repo.run_products(
        run_id, eligible_only=eligible_only, limit=limit, offset=offset
    )
    return ProductPage(
        run_id=run_id,
        run_status=run.status,
        partial=run.status == "partial",
        items=[
            ProductItem(
                product_id=row.product_id,
                title=row.title,
                canonical_url=row.canonical_url,
                price_text=row.price_raw,
                price_yuan=fen_to_yuan(row.price_fen),
                observed_at=row.observed_at,
                published_at=row.published_at,
                area=row.area,
                seller_display_name=row.seller_display_name,
                image_url=row.image_url,
                flags=list(row.flags),
                item_kind=row.item_kind,
                excluded=row.excluded,
                exclusion_reasons=list(row.exclusion_reasons),
                needs_review=row.needs_review,
                price_parse_status=row.price_parse_status,
                source_run_id=row.run_id,
            )
            for row in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )
