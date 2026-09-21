"""适配器生命周期测试：上游 init 幂等、逐页串行、登录态不被主动销毁、错误可诊断性。

不打网络。用桩替换上游 mtop 模块，只验证我们自己的调用策略。

桩忠实模拟上游的两个关键行为：
- `init()` 会把磁盘上的 session.json 读进内存（`load_session`）
- `probe_login()` 在**任何**异常时都会走 `invalidate_expired_login()`，
  它 `client.cookies.clear()` 并 `clear_session()` —— 也就是**删除 session.json**
"""

from __future__ import annotations

import asyncio

import pytest

from xps.adapters.base import AUTH_GUEST, AUTH_LOGGED_IN, AUTH_UNKNOWN
from xps.adapters.xianyu import XianyuUpstreamAdapter
from xps.errors import UpstreamError


class StubMtop:
    def __init__(
        self,
        *,
        session_logged_in: bool = False,
        init_error: Exception | None = None,
        probe_error: Exception | None = None,
    ) -> None:
        self.session_logged_in = session_logged_in  # 磁盘上的 session.json
        self.logged_in = False  # 内存态，只有 init() 会从磁盘加载
        self.init_calls = 0
        self.probe_calls = 0
        self.search_calls = 0
        self.active = 0
        self.max_active = 0
        self.session_file_exists = True
        self._init_error = init_error
        self._probe_error = probe_error

    async def init(self) -> None:
        self.init_calls += 1
        if self._init_error is not None:
            raise self._init_error
        self.logged_in = self.session_logged_in  # 模拟 load_session()

    def login_snapshot(self) -> dict:
        """纯内存，无网络，无副作用 —— 与上游同名函数一致。"""
        return {"logged_in": self.logged_in, "user_id": "u1" if self.logged_in else ""}

    async def probe_login(self) -> dict:
        self.probe_calls += 1
        if self._probe_error is not None:
            # 忠实复刻上游：探测失败即清空内存 cookie 并删除 session.json
            self.logged_in = False
            self.session_file_exists = False
            raise self._probe_error
        return self.login_snapshot()

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


# ---------------------------------------------------------------- init 幂等


async def test_init_runs_once_across_repeated_auth_status_calls() -> None:
    """上游 init 每次都要多打 2 个真实请求（GET 首页 + 取 token）。

    反复调用会放大对平台的请求量，违背低频要求（指南 §9）。
    """
    stub = StubMtop()
    adapter = stubbed_adapter(stub)

    await adapter.auth_status()
    await adapter.auth_status()
    await adapter.auth_status()

    assert stub.init_calls == 1


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


# ------------------------------------------------- 登录态：一次登录，进程内长期有效


async def test_auth_status_never_probes_the_platform() -> None:
    """核心需求：不主动过期。

    上游 probe_login() 在 fetch_login_user() 抛任何异常（含一次网络抖动）时都会
    invalidate_expired_login() → cookies.clear() + clear_session()，直接删掉
    session.json，用户得重新扫码。故 auth_status 只读本地内存快照。
    """
    stub = StubMtop(session_logged_in=True)
    adapter = stubbed_adapter(stub)

    await adapter.auth_status()
    await adapter.search("富士 X-T4", 1, "newest", None, None, None)

    assert stub.probe_calls == 0


async def test_a_transient_probe_failure_cannot_destroy_saved_credentials() -> None:
    """即使上游探测会摧毁凭证，我们也绝不触发它。"""
    stub = StubMtop(session_logged_in=True, probe_error=RuntimeError("网络抖动"))
    adapter = stubbed_adapter(stub)

    status = await adapter.auth_status()

    assert status.state == AUTH_LOGGED_IN
    assert stub.session_file_exists is True, "session.json 被删了 —— 登录态被主动销毁"
    assert stub.logged_in is True


async def test_auth_status_reports_logged_in_from_the_local_session() -> None:
    status = await stubbed_adapter(StubMtop(session_logged_in=True)).auth_status()

    assert status.state == AUTH_LOGGED_IN
    assert status.requires_human_action is False


async def test_auth_status_reports_guest_without_a_session() -> None:
    status = await stubbed_adapter(StubMtop(session_logged_in=False)).auth_status()

    assert status.state == AUTH_GUEST
    assert status.requires_human_action is False


async def test_auth_status_declares_itself_unverified() -> None:
    """不向平台校验就必须如实说明，不能让调用方误以为已确认有效。"""
    status = await stubbed_adapter(StubMtop(session_logged_in=True)).auth_status()

    assert status.verified is False


async def test_reload_picks_up_new_credentials_without_restarting() -> None:
    """用户在另一个终端登录后，不必重启服务即可生效。"""
    stub = StubMtop(session_logged_in=False)
    adapter = stubbed_adapter(stub)
    assert (await adapter.auth_status()).state == AUTH_GUEST

    stub.session_logged_in = True  # 模拟 scripts/login.sh 写入了 session.json
    status = await adapter.reload()

    assert status.state == AUTH_LOGGED_IN
    assert stub.init_calls == 2, "reload 必须重跑 init() 才会重新读取 session.json"


async def test_real_credential_expiry_surfaces_from_search_not_from_probing() -> None:
    """真实失效应由平台在搜索时告知（ret 码），而不是我们主动去问然后把凭证删掉。"""
    stub = StubMtop(session_logged_in=True)

    async def rejecting_search(keyword, page=1, filters=None):
        raise RuntimeError("搜索接口调用失败: ['FAIL_SYS_SESSION_EXPIRED::Session过期']")

    stub.search = rejecting_search  # type: ignore[method-assign]
    result = await stubbed_adapter(stub).search("富士 X-T4", 1, "newest", None, None, None)

    assert result.pages[0].error_code == "AUTH_EXPIRED"
    assert stub.session_file_exists is True


# ---------------------------------------------------------------- 错误与节流


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


async def test_auth_status_degrades_to_unknown_when_the_snapshot_fails() -> None:
    stub = StubMtop()

    def exploding_snapshot():
        raise RuntimeError("boom")

    stub.login_snapshot = exploding_snapshot  # type: ignore[method-assign]

    status = await stubbed_adapter(stub).auth_status()

    assert status.state == AUTH_UNKNOWN
