"""路由共享依赖。"""

from __future__ import annotations

from fastapi import Request

from xps.errors import AUTH_REQUIRED, INVALID_QUERY, ServiceError, is_retryable, requires_human
from xps.services.search_service import SearchService
from xps.settings import Settings
from xps.storage.repository import Repository, RunRecord

# 这两种状态说明本轮没有可信结果；必须报错，不能退化成 200 + []
_UNUSABLE_STATUSES = frozenset({"failed", "blocked_login"})

# 已移除的筛选参数。FastAPI 默认**静默忽略**未声明的 query 参数，
# 而拿着旧接口记忆传 item_kind=body 的调用方会以为结果已经筛过 —— 那是谎报口径。
# 所以显式 422，和 UNSUPPORTED_FILTER 同一个理由。
_REMOVED_FILTER_PARAMS = {
    "item_kind": "本服务不再做单机身/套机之类的相关性筛选",
    "eligible_only": "已改名 priced_only，且它只表示「价格能解析成数字」，不是相关性筛选",
    "exclude_rental": "本服务不排除任何条目，租赁盘照常返回",
    "flags": "分类标签体系已移除",
}


def reject_removed_filters(request: Request) -> None:
    present = [name for name in _REMOVED_FILTER_PARAMS if name in request.query_params]
    if not present:
        return
    detail = "；".join(f"{name}（{_REMOVED_FILTER_PARAMS[name]}）" for name in sorted(present))
    raise ServiceError(
        INVALID_QUERY,
        f"以下参数已移除，不会被静默忽略：{detail}。"
        "请改用 /v1/products 读原始字段自行判断可比性。",
        status_code=422,
    )


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_repo(request: Request) -> Repository:
    return request.app.state.repo


def get_service(request: Request) -> SearchService:
    return request.app.state.service


def load_run(repo: Repository, run_id: str) -> RunRecord:
    run = repo.get_run(run_id)
    if run is None:
        raise ServiceError(
            INVALID_QUERY,
            f"run_id 不存在：{run_id}",
            run_id=run_id,
            status_code=404,
        )
    return run


def load_usable_run(repo: Repository, run_id: str) -> RunRecord:
    """§0.6：采集失败时不得用 200 + 空列表伪装「闲鱼目前没有商品」。"""
    run = load_run(repo, run_id)
    if run.status in _UNUSABLE_STATUSES:
        raise ServiceError(
            run.error_code or AUTH_REQUIRED,
            run.error_message or f"本轮采集未成功（status={run.status}），没有可用结果",
            run_id=run_id,
        )
    return run


def run_error_body(run: RunRecord) -> dict | None:
    if not run.error_code:
        return None
    return {
        "code": run.error_code,
        "message": run.error_message,
        "retryable": is_retryable(run.error_code),
        "requires_human_action": requires_human(run.error_code),
    }
