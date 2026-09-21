"""适配器生命周期测试：上游 init 的调用次数、逐页串行、错误可诊断性。

不打网络。用桩替换上游 mtop 模块，只验证我们自己的调用策略。
"""

from __future__ import annotations

import asyncio

import pytest

from xps.adapters.xianyu import XianyuUpstreamAdapter
from xps.errors import UpstreamError


class StubMtop:
    def __init__(self, init_error: Exception | None = None) -> None:
        self.init_calls = 0
        self.probe_calls = 0
        self.search_calls = 0
        self.active = 0
        self.max_active = 0
        self._init_error = init_error

    async def init(self) -> None:
        self.init_calls += 1
        if self._init_error is not None:
            raise self._init_error

    async def probe_login(self) -> dict:
        self.probe_calls += 1
        return {"logged_in": False}

    async def search(self, keyword: str, page: int = 1, filters=None) -> dict:
        self.search_calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.01)
            return {"ret": ["SUCCESS::调用成功"], "data": {"resultList": [], "resultInfo": {}}}
        finally:
            self.active -= 1


class StubFilters:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


def stubbed_adapter(stub: StubMtop) -> XianyuUpstreamAdapter:
    adapter = XianyuUpstreamAdapter(seconds_between_pages=0)
    adapter._mtop = stub  # noqa: SLF001  桩替换，避免真实导入上游
    adapter._filters_cls = StubFilters  # noqa: SLF001
    adapter._load = lambda: None  # noqa: SLF001
    return adapter


async def test_init_runs_once_across_repeated_auth_probes() -> None:
    """上游 init 每次都要多打 2 个真实请求（GET 首页 + 取 token）。

    反复调用会放大对平台的请求量，违背低频要求（指南 §9）。
    """
    stub = StubMtop()
    adapter = stubbed_adapter(stub)

    await adapter.auth_status()
    await adapter.auth_status()
    await adapter.auth_status()

    assert stub.init_calls == 1
    assert stub.probe_calls == 3


async def test_init_runs_once_per_search_not_once_per_page() -> None:
    stub = StubMtop()
    adapter = stubbed_adapter(stub)

    await adapter.search("富士 X-T4", 3, "newest", None, None, None)

    assert stub.init_calls == 1
    assert stub.search_calls == 3


def test_init_repeats_when_the_event_loop_changes() -> None:
    """上游 client 绑定创建它的 loop；换 loop 必须重新 init，
    否则会拿到已关闭的连接池并报 "Event loop is closed"。"""
    stub = StubMtop()
    adapter = stubbed_adapter(stub)

    async def probe() -> None:
        await adapter.auth_status()

    asyncio.run(probe())
    asyncio.run(probe())

    assert stub.init_calls == 2


async def test_init_failure_reports_the_real_cause() -> None:
    """不能把所有失败都笼统说成「取不到匿名 token」，
    否则本地生命周期问题会被误判成平台故障。"""
    stub = StubMtop(init_error=RuntimeError("Event loop is closed"))
    adapter = stubbed_adapter(stub)

    with pytest.raises(UpstreamError) as excinfo:
        await adapter.search("富士 X-T4", 1, "newest", None, None, None)

    assert excinfo.value.code == "UPSTREAM_UNAVAILABLE"
    assert "Event loop is closed" in excinfo.value.message
    assert excinfo.value.retryable is True


async def test_pages_are_fetched_sequentially_not_concurrently() -> None:
    """逐页串行是刻意的：上游 Semaphore(3) 会同时打 3 个请求。"""
    stub = StubMtop()
    adapter = stubbed_adapter(stub)

    result = await adapter.search("富士 X-T4", 3, "newest", None, None, None)

    assert stub.max_active == 1
    assert result.pages_requested == 3
    assert result.pages_fetched == 3


async def test_auth_probe_failure_degrades_to_unknown_not_an_exception() -> None:
    class FailingProbe(StubMtop):
        async def probe_login(self) -> dict:
            raise RuntimeError("boom")

    status = await stubbed_adapter(FailingProbe()).auth_status()

    assert status.state == "unknown"
