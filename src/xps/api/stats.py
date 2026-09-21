"""GET /v1/stats（指南 §7 / §8.4）：本轮在售报价的算术分布。

**不做任何筛选**：租赁盘、拍卖起拍价、配件、广告位全都留在样本里。
要判断可比性请配 /v1/products 读原始字段，那边有卖家信用、完整描述、
平台事实标记（is_auction / is_ad / 券抵扣 / 想要人数）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from xps.api.deps import get_repo, get_settings, load_usable_run, reject_removed_filters
from xps.api.schemas import PricedItem as PricedItemModel
from xps.api.schemas import StatsResponse
from xps.services.normalize import fen_to_yuan
from xps.services.statistics import PricedItem, compute_stats
from xps.settings import Settings
from xps.storage.repository import Repository

router = APIRouter(prefix="/v1", tags=["stats"])


def _priced(entry: PricedItem) -> PricedItemModel:
    return PricedItemModel(
        product_id=entry.product_id,
        title=entry.title,
        canonical_url=entry.canonical_url,
        price_yuan=fen_to_yuan(entry.price_fen) or "",
    )


@router.get(
    "/stats",
    response_model=StatsResponse,
    summary="本轮在售报价的算术分布（未筛选）",
    description=(
        "run_id 必填：绝不跨日期或跨关键词混合历史数据。口径为**采集时刻的公开在售报价**，"
        "不是成交价，不含国补 / 优惠券 / 议价结果。分位数为 inclusive 线性插值。\n\n"
        "样本是**未筛选**的：平台返回什么就算什么。`sample_quality` 恒含 `unfiltered`，"
        "并在存在拍卖 / 广告位 / 价格无法解析时追加对应标记。转述时不得省略这些限制。"
    ),
)
async def get_stats(
    request: Request,
    run_id: str = Query(..., description="POST /v1/search 返回的 run_id"),
    repo: Repository = Depends(get_repo),
    settings: Settings = Depends(get_settings),
) -> StatsResponse:
    reject_removed_filters(request)
    run = load_usable_run(repo, run_id)
    result = compute_stats(
        run_id=run_id,
        items=repo.stats_items(run_id),
        raw_count=run.raw_count,
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
        currency=result.currency,
        partial=result.partial,
        auth_mode=run.auth_mode,
        raw_count=result.raw_count,
        distinct_count=result.distinct_count,
        priced_count=result.priced_count,
        unpriced_count=result.unpriced_count,
        unpriced_by_status=dict(result.unpriced_by_status),
        auction_count=result.auction_count,
        ad_count=result.ad_count,
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
        lowest_items=[_priced(entry) for entry in result.lowest_items],
        highest_items=[_priced(entry) for entry in result.highest_items],
        insufficient_sample=result.insufficient_sample,
        sample_quality=list(result.sample_quality),
        started_at=run.started_at,
        ended_at=run.ended_at,
    )
