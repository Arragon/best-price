"""POST /v1/search 与 GET /v1/search-runs/{run_id}（指南 §8.1 / §8.2）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from xps.api.deps import get_repo, get_service, get_settings, load_run, run_error_body
from xps.api.schemas import SearchAccepted, SearchRunResponse, SearchSubmitRequest
from xps.errors import INVALID_QUERY, UNSUPPORTED_FILTER, ServiceError
from xps.services.search_service import SearchRequest
from xps.settings import Settings
from xps.storage.repository import Repository

router = APIRouter(prefix="/v1", tags=["search"])


@router.post(
    "/search",
    status_code=202,
    response_model=SearchAccepted,
    summary="提交一次真实搜索（异步）",
    description=(
        "立即返回 202 与 run_id，慢速采集不占住调用方的 HTTP 会话。"
        "任务状态全部落库，进程崩溃后重启会被判定为 RUN_INTERRUPTED。"
    ),
)
async def submit_search(
    payload: SearchSubmitRequest,
    service=Depends(get_service),
    settings: Settings = Depends(get_settings),
) -> SearchAccepted:
    if payload.max_pages > settings.max_search_pages:
        raise ServiceError(
            INVALID_QUERY,
            f"max_pages={payload.max_pages} 超过上限 {settings.max_search_pages}"
            "（MAX_SEARCH_PAGES 可调，默认保守）",
        )

    unverified = payload.unverified_filters()
    if unverified:
        # §8.1：未经验证支持的搜索条件必须显式拒绝，不能暗称已由平台过滤
        raise ServiceError(
            UNSUPPORTED_FILTER,
            f"以下筛选条件尚未实测验证平台是否真过滤，暂不开放：{', '.join(unverified)}",
        )

    run_id = service.submit(
        SearchRequest(
            keyword=payload.keyword,
            max_pages=payload.max_pages,
            sort=payload.sort,
            min_price=payload.min_price_yuan,
            max_price=payload.max_price_yuan,
            city=payload.city,
            item_kind=payload.item_kind,
        )
    )
    return SearchAccepted(
        run_id=run_id, status="pending", status_url=f"/v1/search-runs/{run_id}"
    )


@router.get(
    "/search-runs/{run_id}",
    response_model=SearchRunResponse,
    summary="查询一次搜索任务的状态与分层计数",
)
async def get_search_run(
    run_id: str,
    repo: Repository = Depends(get_repo),
) -> SearchRunResponse:
    run = load_run(repo, run_id)
    return SearchRunResponse(
        run_id=run.run_id,
        status=run.status,
        platform=run.platform,
        keyword=run.keyword,
        auth_mode=run.auth_mode,
        pages_requested=run.pages_requested,
        pages_fetched=run.pages_fetched,
        raw_count=run.raw_count,
        distinct_count=repo.count_observations_for_run(run_id),
        eligible_count=run.eligible_count,
        started_at=run.started_at,
        ended_at=run.ended_at,
        warnings=list(run.warnings),
        error=run_error_body(run),  # type: ignore[arg-type]
        adapter_version=run.adapter_version,
        source_commit=run.source_commit,
    )
