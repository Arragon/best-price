"""GET /health 与 GET /v1/auth/status（指南 §8.5）。"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Request

from xps import __version__
from xps.adapters.base import AUTH_EXPIRED, AUTH_HUMAN_ACTION
from xps.api.deps import get_repo
from xps.api.schemas import AuthStatusResponse, HealthResponse

router = APIRouter(tags=["system"])

_HUMAN_STATES = frozenset({AUTH_EXPIRED, AUTH_HUMAN_ACTION})


@router.get("/health", response_model=HealthResponse, summary="进程与本地 SQLite 是否可用")
async def health(request: Request) -> HealthResponse:
    """登录态丢失**不算**进程不健康（§8.5）。"""
    repo = get_repo(request)
    try:
        repo.conn.execute("SELECT 1").fetchone()
        database = "ok"
    except sqlite3.Error:
        database = "error"

    adapter = request.app.state.adapter
    return HealthResponse(
        status="ok" if database == "ok" else "degraded",
        database=database,
        version=__version__,
        adapter=type(adapter).__name__,
    )


def _to_response(status) -> AuthStatusResponse:
    return AuthStatusResponse(
        state=status.state,
        # 失效/需人工时即使适配器没标注也强制置真，避免调用方误以为可以继续
        requires_human_action=status.requires_human_action or status.state in _HUMAN_STATES,
        hint=status.hint,
        verified=status.verified,
    )


@router.get(
    "/v1/auth/status",
    response_model=AuthStatusResponse,
    summary="机器可读的登录态（只读本地凭证）",
    description=(
        "只回传状态枚举，绝不回传 Cookie、token 或 user_id 原值。\n\n"
        "`verified=false` 表示**未向平台主动校验**：上游 `probe_login()` 在任何异常时"
        "都会删除 `session.json`，一次网络抖动就能毁掉扫码换来的登录态。本项目改为"
        "「一次登录后进程内长期有效，不主动过期」，真实失效会在搜索时以 `AUTH_EXPIRED` 报出。"
    ),
)
async def auth_status(request: Request) -> AuthStatusResponse:
    return _to_response(await request.app.state.adapter.auth_status())


@router.post(
    "/v1/auth/reload",
    response_model=AuthStatusResponse,
    summary="重新读取登录态（登录后免重启）",
    description=(
        "在本机另开终端跑完 `scripts/login.sh` 后调用，服务会重跑上游 `init()` "
        "重新加载 `session.json`。不需要重启进程。\n\n"
        "该操作不向平台校验、不修改也不删除任何凭证文件。"
    ),
)
async def reload_auth(request: Request) -> AuthStatusResponse:
    status = await request.app.state.adapter.reload()
    request.app.state.service.scheduler.acknowledge_human_action()
    return _to_response(status)
