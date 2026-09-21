"""GET /v1/stats（指南 §7 / §8.4）：本轮在售报价统计。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from xps.api.deps import get_repo, get_settings, load_usable_run
from xps.api.schemas import ItemKind, LowestItem, StatsResponse
from xps.services.normalize import fen_to_yuan
from xps.services.statistics import compute_stats
from xps.settings import Settings
from xps.storage.repository import Repository

router = APIRouter(prefix="/v1", tags=["stats"])


@router.get(
    "/stats",
    response_model=StatsResponse,
    summary="本轮在售报价统计",
    description=(
        "run_id 必填：绝不跨日期或跨关键词混合历史数据。口径为**采集时刻的公开在售报价**，"
        "不是成交价，不含国补 / 优惠券 / 议价结果。分位数为 inclusive 线性插值。"
    ),
)
async def get_stats(
    run_id: str = Query(..., description="POST /v1/search 返回的 run_id"),
    item_kind: ItemKind = Query("any", description="body=单机身 / kit=套机 / any=不限"),
    repo: Repository = Depends(get_repo),
    settings: Settings = Depends(get_settings),
) -> StatsResponse:
    run = load_usable_run(repo, run_id)
    result = compute_stats(
        run_id=run_id,
        items=repo.stats_items(run_id),
        raw_count=run.raw_count,
        item_kind=item_kind,
        min_sample=settings.min_sample_threshold,
        pages_requested=run.pages_requested,
        pages_fetched=run.pages_fetched,
        auth_mode=run.auth_mode or "unknown",
        status=run.status,
    )
    return StatsResponse(
        run_id=run_id,
        keyword=run.keyword,
        run_status=run.status,
        item_kind=result.item_kind,
        currency=result.currency,
        partial=result.partial,
        auth_mode=run.auth_mode,
        raw_count=result.raw_count,
        distinct_count=result.distinct_count,
        eligible_count=result.eligible_count,
        excluded_count=result.excluded_count,
        excluded_by_reason=dict(result.excluded_by_reason),
        needs_review_count=result.needs_review_count,
        suspicious_price_count=result.suspicious_price_count,
        min_yuan=fen_to_yuan(result.min_fen),
        p25_yuan=fen_to_yuan(result.p25_fen),
        median_yuan=fen_to_yuan(result.median_fen),
        p75_yuan=fen_to_yuan(result.p75_fen),
        max_yuan=fen_to_yuan(result.max_fen),
        min_fen=result.min_fen,
        p25_fen=result.p25_fen,
        median_fen=result.median_fen,
        p75_fen=result.p75_fen,
        max_fen=result.max_fen,
        lowest_items=[
            LowestItem(
                product_id=entry.product_id,
                title=entry.title,
                canonical_url=entry.canonical_url,
                price_yuan=fen_to_yuan(entry.price_fen) or "",
            )
            for entry in result.lowest_items
        ],
        insufficient_sample=result.insufficient_sample,
        sample_quality=list(result.sample_quality),
        started_at=run.started_at,
        ended_at=run.ended_at,
    )
