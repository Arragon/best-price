"""FastAPI 装配、生命周期与统一错误处理。

默认只监听 127.0.0.1；跨设备访问需显式 ALLOW_REMOTE_ACCESS=true（指南 §9）。
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from xps import __version__
from xps.adapters.xianyu import XianyuUpstreamAdapter
from xps.api import products, search, stats, system
from xps.errors import DB_ERROR, INVALID_QUERY, ServiceError, is_retryable, requires_human
from xps.services.search_service import SearchService
from xps.settings import Settings
from xps.storage.db import connect, migrate
from xps.storage.repository import Repository

logger = logging.getLogger("xps")

APP_TITLE = "闲鱼本地商品搜索与价格统计服务"
APP_DESCRIPTION = """
单平台（闲鱼）本地 MVP。**只监听 127.0.0.1**，不分发、不商用。

## 价格口径

返回的是**采集时刻的公开在售报价**，不是成交价，不含国补 / 优惠券 / 议价结果。
金额内部一律以人民币**分（整数）**存储，API 以保留两位的字符串返回。

## 调用流程

1. `POST /v1/search` → `202` + `run_id`
2. 轮询 `GET /v1/search-runs/{run_id}` 直到状态为 `succeeded` / `partial` / `failed`
3. `GET /v1/products?run_id=...` 取本轮商品与原始链接
4. `GET /v1/stats?run_id=...&item_kind=body` 取中位数 / 分位数 / 样本量 / 排除原因

## 失败语义

采集失败**不会**退化成 `200 + []`。`200` 且空列表只表示经核验的真实无结果。
错误响应统一为 `{code, message, run_id, retryable, requires_human_action}`。
`requires_human_action=true` 时请到本机合法客户端处理（重新登录 / 完成验证），
不要继续自动重试。
"""

_Routers = (search, products, stats, system)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    conn = connect(settings.database_path)
    migrate(conn)
    repo = Repository(conn)
    service = SearchService(repo, app.state.adapter, settings)
    # 进程重启后，上一轮遗留的 pending/running 一律判定为中断，不能永远挂着
    service.recover_interrupted_runs()

    app.state.conn = conn
    app.state.repo = repo
    app.state.service = service
    logger.info("数据库就绪：%s", settings.database_path)
    try:
        yield
    finally:
        await service.shutdown()
        conn.close()


def _summarize_validation(exc: RequestValidationError) -> str:
    parts = []
    for error in exc.errors()[:5]:
        location = ".".join(
            str(piece) for piece in error.get("loc", ()) if piece not in ("body", "query")
        )
        parts.append(f"{location or 'body'}: {error.get('msg')}")
    return "参数校验失败 -> " + "; ".join(parts)


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ServiceError)
    async def handle_service_error(request: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_body())

    @app.exception_handler(RequestValidationError)
    async def handle_validation(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "code": INVALID_QUERY,
                "message": _summarize_validation(exc),
                "run_id": None,
                "retryable": False,
                "requires_human_action": False,
            },
        )

    @app.exception_handler(sqlite3.Error)
    async def handle_sqlite_error(request: Request, exc: sqlite3.Error) -> JSONResponse:
        logger.exception("数据库错误")
        return JSONResponse(
            status_code=500,
            content={
                "code": DB_ERROR,
                "message": f"本地数据库错误：{type(exc).__name__}",
                "run_id": None,
                "retryable": is_retryable(DB_ERROR),
                "requires_human_action": requires_human(DB_ERROR),
            },
        )


def create_app(settings: Settings | None = None, *, adapter: object | None = None) -> FastAPI:
    app_settings = settings or Settings()
    app = FastAPI(
        title=APP_TITLE,
        description=APP_DESCRIPTION,
        version=__version__,
        lifespan=lifespan,
    )
    app.state.settings = app_settings
    app.state.adapter = adapter or XianyuUpstreamAdapter(
        app_settings.xianyu_upstream_path,
        seconds_between_pages=app_settings.seconds_between_pages,
    )

    for module in _Routers:
        app.include_router(module.router)
    _register_exception_handlers(app)
    return app


def main() -> None:
    import uvicorn

    settings = Settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger.info(
        "启动 %s v%s，监听 http://%s:%d，数据库 %s",
        APP_TITLE,
        __version__,
        settings.app_host,
        settings.app_port,
        settings.database_path,
    )
    uvicorn.run(
        create_app(settings),
        host=settings.app_host,
        port=settings.app_port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
