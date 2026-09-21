"""路由共享依赖。"""

from __future__ import annotations

from fastapi import Request

from xps.errors import AUTH_REQUIRED, INVALID_QUERY, ServiceError, is_retryable, requires_human
from xps.services.search_service import SearchService
from xps.settings import Settings
from xps.storage.repository import Repository, RunRecord

# 这两种状态说明本轮没有可信结果；必须报错，不能退化成 200 + []
_UNUSABLE_STATUSES = frozenset({"failed", "blocked_login"})


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
