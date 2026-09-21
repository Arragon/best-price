"""⚠️ 真实闲鱼集成测试 —— 默认**永不**运行。

    .venv/bin/python -m pytest -m live -q

pyproject.toml 里 `addopts = '-m "not live"'` 把这些用例默认排除，CI 绝不会触发。

约束（指南 §5.3 / §9）：
- 会对闲鱼发**真实**请求，请手动、低频运行，不要放进循环或 CI
- 不访问用户账号，不索取也不打印 Cookie / token / user_id
- 出现验证码、风控或拒绝时立即停止，不重试硬撞
- 断言的是「拿到了真实、可追溯、ID 稳定的商品」，**不断言任何具体价格**，
  因为市场价随时在变，硬编码价格会让测试变成假阳性来源
"""

from __future__ import annotations

import asyncio
import os

import pytest

from xps.adapters.xianyu import XianyuUpstreamAdapter, extract_listings
from xps.services.identity import resolve_identity

# 上游 mtop 的 client 是模块级 httpx.AsyncClient，连接池绑定创建它的事件循环。
# pytest 默认每个测试一个 loop，第一个测试结束后共享 client 就会报
# "Event loop is closed"。因此本模块所有用例共用一个 session 级 loop 与一个适配器。
pytestmark = [pytest.mark.live, pytest.mark.asyncio(loop_scope="session")]

KEYWORD = os.environ.get("LIVE_KEYWORD", "富士 X-T4")
SECONDS_BETWEEN_PAGES = float(os.environ.get("LIVE_SECONDS_BETWEEN_PAGES", "4"))
SECONDS_BETWEEN_ROUNDS = float(os.environ.get("LIVE_SECONDS_BETWEEN_ROUNDS", "15"))
TRUSTED_HOST = "https://www.goofish.com/item?id="


_ADAPTER: XianyuUpstreamAdapter | None = None


def make_adapter() -> XianyuUpstreamAdapter:
    global _ADAPTER
    if _ADAPTER is None:
        _ADAPTER = XianyuUpstreamAdapter(seconds_between_pages=SECONDS_BETWEEN_PAGES)
    return _ADAPTER


async def test_real_one_page_search_yields_traceable_listings() -> None:
    """Gate A：至少一轮真实商品列表，链接可打开、ID 稳定。"""
    result = await make_adapter().search(KEYWORD, 1, "newest", None, None, None)

    assert result.pages_fetched == 1, result.warnings
    listings = result.listings
    assert listings, f"真实搜索返回 0 条；warnings={result.warnings}"

    identities = [resolve_identity(item.source_id, item.url) for item in listings]
    assert all(identity.id_source == "platform_item_id" for identity in identities)
    assert all(item.url.startswith(TRUSTED_HOST) for item in listings)
    # 跟踪参数必须已被剥除
    assert all("gulSource" not in item.url for item in listings)
    # 绝大多数条目应当带可解析的价格文本
    priced = [item for item in listings if item.price_text]
    assert len(priced) >= len(listings) * 0.8

    # 缺失字段必须是 None，不能是上游那种「暂无」「价格异常」占位值
    for item in listings:
        for value in (item.title, item.area, item.seller_name, item.price_text):
            assert value not in {"暂无", "未知标题", "地区未知", "匿名卖家", "价格异常"}


async def test_second_page_is_real_pagination_not_a_repeat() -> None:
    """Gate A：确认真的翻页，不是重复抓第一页。"""
    result = await make_adapter().search(KEYWORD, 2, "newest", None, None, None)

    assert result.pages_requested == 2
    assert result.pages_fetched == 2, result.warnings
    first, second = result.pages[0].listings, result.pages[1].listings
    assert first and second

    first_ids = {item.source_id for item in first}
    second_ids = {item.source_id for item in second}
    assert first_ids.isdisjoint(second_ids), (
        f"第二页与第一页重叠 {len(first_ids & second_ids)} 条，可能没有真翻页"
    )


async def test_repeated_search_still_returns_old_listings_this_round() -> None:
    """Gate A 的核心：重抓时老商品仍进入**本轮**结果。

    上游此时会报 new_records=0 / new_record_ids=[]，但那绝不等于本轮没有商品。
    """
    adapter = make_adapter()
    first = await adapter.search(KEYWORD, 1, "newest", None, None, None)
    await asyncio.sleep(SECONDS_BETWEEN_ROUNDS)
    second = await adapter.search(KEYWORD, 1, "newest", None, None, None)

    first_ids = {item.source_id for item in first.listings}
    second_ids = {item.source_id for item in second.listings}
    assert first_ids and second_ids

    overlap = first_ids & second_ids
    assert overlap, "两轮完全没有重叠，可能是 sort=newest 下商品全被刷新，请人工复核"
    # 重叠的商品就是「老商品」，它们必须仍然出现在第二轮结果里
    assert len(second.listings) == len(second_ids), "同轮出现重复 ID，去重语义有问题"


async def test_raw_mtop_response_shape_is_unchanged() -> None:
    """上游/平台结构漂移的哨兵：字段路径变了就应当让这条测试失败。"""
    adapter = make_adapter()
    adapter._load()  # noqa: SLF001  live 测试专用
    await adapter._ensure_init()  # noqa: SLF001
    raw = await adapter._mtop.search(KEYWORD, 1)  # noqa: SLF001

    assert any(str(code).startswith("SUCCESS") for code in raw.get("ret") or []), raw.get("ret")
    entries = (raw.get("data") or {}).get("resultList") or []
    assert entries
    assert extract_listings(raw), "resultList 非空但解析出 0 条，说明字段路径已漂移"
