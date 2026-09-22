"""跨测试模块共享的构造与轮询辅助。

fixture 放在 conftest.py，纯函数放在这里，避免各测试文件互相 import。
"""

from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from xps.main import create_app
from xps.settings import Settings
from tests.fake_adapter import FakeAdapter

TERMINAL = {"succeeded", "partial", "failed", "blocked_login"}

_SKIP_METHODS = {"HEAD", "OPTIONS"}


def real_route_set(app: FastAPI) -> set[tuple[str, str]]:
    """直接遍历路由定义，取出真实的 (method, path) 集合。

    刻意不走 `app.openapi()`：help 端点正是从 openapi() 派生的，用同一条路径去核对
    等于自证。另外这个 FastAPI 版本（0.141.x）把 include_router 的结果包成
    `_IncludedRouter`，不会展平进 `app.routes`，所以要经 `original_router` 下钻。
    """
    found: set[tuple[str, str]] = set()

    def collect(routes) -> None:
        for route in routes:
            included = getattr(route, "original_router", None)
            if included is not None:
                collect(included.routes)
                continue
            if isinstance(route, APIRoute):
                # 注意：这个 FastAPI 版本在 add_api_route 时已把 router.prefix 拼进
                # route.path，此处不能再加一遍前缀，否则会得到 /v1/v1/products。
                for method in route.methods or ():
                    if method not in _SKIP_METHODS:
                        found.add((method, route.path))

    collect(app.routes)
    return found


def build_client(tmp_path, adapter: FakeAdapter, **overrides) -> TestClient:
    options: dict = {
        "database_path": tmp_path / "price.sqlite3",
        "min_seconds_between_searches": 0,
        "seconds_between_pages": 0,
        "platform_floor_seconds": 0,
        "economy_gap_seconds": 0,
        "balanced_gap_seconds": 0,
        "fast_gap_seconds": 0,
        "allow_faster_pace": True,
        "_env_file": None,
    }
    options.update(overrides)
    return TestClient(create_app(Settings(**options), adapter=adapter))


def wait_for_run(client: TestClient, run_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/v1/search-runs/{run_id}")
        assert response.status_code == 200, response.text
        body = response.json()
        if body["status"] in TERMINAL:
            return body
        time.sleep(0.02)
    pytest.fail(f"run {run_id} 在 {timeout}s 内未结束（任务卡住）")


def submit(client: TestClient, payload: dict | None = None):
    # 用 `is None` 而非真值判断：空 dict 是合法的「缺字段」测试输入
    return client.post(
        "/v1/search",
        json={"keyword": "富士 X-T4", "max_pages": 1} if payload is None else payload,
    )


def search_and_wait(client: TestClient, payload: dict | None = None, **overrides) -> dict:
    response = submit(client, payload)
    assert response.status_code == 202, response.text
    return wait_for_run(client, response.json()["run_id"], **overrides)
