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


@router.get(
    "/v1/auth/status",
    response_model=AuthStatusResponse,
    summary="机器可读的登录态",
    description="只回传状态枚举，绝不回传 Cookie、token 或 user_id 原值。",
)
async def auth_status(request: Request) -> AuthStatusResponse:
    status = await request.app.state.adapter.auth_status()
    return AuthStatusResponse(
        state=status.state,
        # 失效/需人工时即使适配器没标注也强制置真，避免调用方误以为可以继续
        requires_human_action=status.requires_human_action or status.state in _HUMAN_STATES,
        hint=status.hint,
    )
