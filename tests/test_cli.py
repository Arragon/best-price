"""一键查询客户端测试。

⚠️ 所有 payload 均为**合成数据**，价格虚构，不是真实市场报价。

重点不在 HTTP 编排，而在 `render_report` / `render_failure` 这两个纯函数：
它们负责把「必须转述的口径与限制」固化下来，让 agent 无法只报一个中位数就走。
"""

from __future__ import annotations

import httpx
import pytest

from xps.cli import QueryResult, build_client, query, render_failure, render_report

RUN_ID = "run-synthetic-0001"

RUN_OK = {
    "run_id": RUN_ID,
    "status": "succeeded",
    "platform": "xianyu",
    "keyword": "富士 X-T4",
    "auth_mode": "guest",
    "pages_requested": 2,
    "pages_fetched": 2,
    "raw_count": 60,
    "distinct_count": 60,
    "priced_count": 57,
    "started_at": "2026-09-22T03:00:00Z",
    "ended_at": "2026-09-22T03:00:06Z",
    "warnings": [],
    "error": None,
    "adapter_version": "xps-xianyu/0.2.0",
    "source_commit": "eb52bd4d1901eee9ba8035e860583cddf50ead4c",
}

STATS_OK = {
    "run_id": RUN_ID,
    "keyword": "富士 X-T4",
    "run_status": "succeeded",
    "currency": "CNY",
    "partial": False,
    "auth_mode": "guest",
    "raw_count": 60,
    "distinct_count": 60,
    "priced_count": 57,
    "unpriced_count": 3,
    "unpriced_by_status": {"ambiguous": 2, "missing": 1},
    "auction_count": 1,
    "ad_count": 2,
    "min_yuan": "50.00",
    "p25_yuan": "4390.00",
    "median_yuan": "5249.50",
    "p75_yuan": "5349.25",
    "max_yuan": "7500.00",
    "lowest_items": [
        {
            "product_id": 61,
            "title": "合成 X-T4 电池",
            "canonical_url": "https://www.goofish.com/item?id=1086862848999",
            "price_yuan": "50.00",
        }
    ],
    "highest_items": [
        {
            "product_id": 58,
            "title": "合成 富士X-T4微单机身 黑色",
            "canonical_url": "https://www.goofish.com/item?id=1086862848607",
            "price_yuan": "7500.00",
        }
    ],
    "insufficient_sample": False,
    "sample_quality": [
        "unfiltered",
        "includes_auction_start_prices",
        "includes_promoted_ads",
        "some_prices_unparsed",
        "guest_auth",
    ],
    "started_at": "2026-09-22T03:00:00Z",
    "ended_at": "2026-09-22T03:00:06Z",
}

PRODUCTS_OK = {
    "run_id": RUN_ID,
    "run_status": "succeeded",
    "partial": False,
    "total": 57,
    "limit": 5,
    "offset": 0,
    "items": [
        {
            "product_id": 58,
            "source_run_id": RUN_ID,
            "observed_at": "2026-09-22T03:00:05Z",
            "canonical_url": "https://www.goofish.com/item?id=1086862848607",
            "title": "合成 富士X-T4微单机身 黑色",
            "description": "合成 富士X-T4微单机身 黑色\n无拆无修\n配件：电池2块",
            "price": {
                "raw": "¥4390",
                "yuan": "4390.00",
                "fen": 439_000,
                "parse_status": "valid",
                "original_text": "¥6999",
                "coupon_text": "券已抵50元",
            },
            "seller": {
                "display_name": "合成卖家",
                "credit": "卖家信用极好",
                "review_count": 318,
                "positive_rate": "39%",
                "identity": "闲鱼严选卖家",
                "avatar_url": "https://img.example.invalid/synthetic-avatar.jpg",
            },
            "area": "上海",
            "media": {
                "image_url": "https://img.example.invalid/synthetic.jpg",
                "has_video": False,
            },
            "published_at": "2026-09-21T11:13:52Z",
            "signals": {
                "published_text": "6小时前发布",
                "want_count": 8,
                "free_shipping": True,
                "labels": ["验货宝"],
                "is_auction": False,
                "is_ad": False,
            },
        }
    ],
}


def report(stats=None, run=None, products=None, **kwargs) -> str:
    return render_report(
        run=run or RUN_OK,
        stats=stats or STATS_OK,
        products=(products or PRODUCTS_OK)["items"],
        **kwargs,
    )


# ---------------------------------------------------------------- 口径声明不可省略


def test_report_always_states_that_prices_are_listings_not_sales() -> None:
    """最重要的纪律：挂牌价不是成交价。少了这句，用户会当成成交行情。"""
    text = report()

    assert "在售报价" in text
    assert "不是成交价" in text


def test_report_states_excluded_discount_and_negotiation() -> None:
    text = report()

    assert "国补" in text or "优惠券" in text


def test_report_shows_sample_size_next_to_the_median() -> None:
    """不允许只报一个中位数。样本量必须紧挨着出现。"""
    text = report()

    assert "5249.50" in text
    assert "57" in text
    assert "样本" in text


def test_report_shows_the_full_five_number_summary() -> None:
    text = report()

    for value in ("50.00", "4390.00", "5249.50", "5349.25", "7500.00"):
        assert value in text


def test_report_declares_the_sample_is_unfiltered() -> None:
    """最重要的一条：这份分布没有清洗过。少了这句，用户会把含租赁盘的中位数当行情。"""
    text = report()

    assert "未筛选" in text
    assert "unfiltered" in text


def test_report_lists_unpriced_breakdown_instead_of_exclusions() -> None:
    """「面议」「平台没给价格」不是被排除，是没进算术——必须说清楚是哪一种。"""
    text = report()

    assert "无价 3" in text
    assert "ambiguous 2" in text
    assert "missing 1" in text


def test_report_counts_auction_and_ad_items() -> None:
    """拍卖起拍价与广告位仍在样本里，但条数必须露出来。"""
    text = report()

    assert "拍卖 1" in text
    assert "广告位 2" in text


def test_report_shows_seller_credit_next_to_the_price() -> None:
    """判断一条报价可不可信，卖家信用与好评率是最直接的依据。"""
    text = report()

    assert "卖家信用极好" in text
    assert "好评率39%(318评价)" in text
    assert "闲鱼严选卖家" in text


def test_report_flags_coupon_when_it_qualifies_the_price() -> None:
    """「券已抵50元」意味着展示价可能已扣券，属价格口径。"""
    assert "券已抵50元" in report()


def test_report_lists_sample_quality_verbatim() -> None:
    text = report()

    assert "guest_auth" in text


def test_report_includes_traceable_source_links() -> None:
    text = report()

    assert "https://www.goofish.com/item?id=1086862848607" in text


def test_report_includes_provenance() -> None:
    """可复现：run_id、上游 commit、采集时间都要在。"""
    text = report()

    assert RUN_ID in text
    assert "eb52bd4d1901eee9ba8035e860583cddf50ead4c" in text
    assert "2026-09-22T03:00:00Z" in text


# ---------------------------------------------------------------- 不得过度确定


def test_insufficient_sample_produces_a_prominent_warning() -> None:
    thin = {**STATS_OK, "priced_count": 3, "insufficient_sample": True,
            "sample_quality": ["insufficient_sample", "guest_auth"]}

    text = report(stats=thin)

    assert "insufficient_sample" in text
    assert "样本不足" in text


def test_insufficient_sample_never_presents_a_fair_market_price() -> None:
    """§7：样本过少时不得产出过度确定的「市场公允价」。"""
    thin = {**STATS_OK, "priced_count": 3, "insufficient_sample": True,
            "sample_quality": ["insufficient_sample"]}

    text = report(stats=thin)

    assert "公允价" not in text
    assert "市场价" not in text.replace("在售报价", "")


def test_partial_run_is_flagged() -> None:
    partial_run = {**RUN_OK, "status": "partial", "pages_fetched": 1}
    partial_stats = {
        **STATS_OK,
        "partial": True,
        "run_status": "partial",
        "sample_quality": ["partial_pages:1/2", "guest_auth"],
    }

    text = report(run=partial_run, stats=partial_stats)

    assert "partial_pages:1/2" in text
    assert "部分" in text


def test_zero_priced_samples_explains_why_instead_of_showing_a_median() -> None:
    """一条价格都解析不出来时，必须说清楚是哪种情况，不能显示一个空的中位数。"""
    empty = {
        **STATS_OK,
        "priced_count": 0,
        "unpriced_count": 12,
        "unpriced_by_status": {"ambiguous": 9, "missing": 3},
        "min_yuan": None,
        "p25_yuan": None,
        "median_yuan": None,
        "p75_yuan": None,
        "max_yuan": None,
        "lowest_items": [],
        "highest_items": [],
        "insufficient_sample": True,
    }

    text = report(stats=empty, products=[])

    assert "有价样本 0 件" in text
    assert "ambiguous 9" in text, "必须指出价格为什么进不了算术"
    assert "missing 3" in text


# ---------------------------------------------------------------- 失败分诊


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("CHALLENGE_REQUIRED", "闲鱼 App"),
        ("RATE_LIMITED", "不要"),
        ("AUTH_EXPIRED", "scripts/login.sh"),
        ("AUTH_REQUIRED", "scripts/login.sh"),
        ("UPSTREAM_TIMEOUT", "重试"),
        ("UPSTREAM_CHANGED", "verify_upstream.py"),
        ("RUN_INTERRUPTED", "重新发起"),
    ],
)
def test_failure_rendering_gives_an_actionable_next_step(code: str, expected: str) -> None:
    run = {
        **RUN_OK,
        "status": "failed",
        "error": {
            "code": code,
            "message": "合成失败原因",
            "retryable": False,
            "requires_human_action": True,
        },
    }

    text = render_failure(run)

    assert code in text
    assert expected in text


def test_failure_rendering_never_says_there_are_no_listings() -> None:
    """§0.6：采集失败不能表述成「闲鱼没有商品」。"""
    run = {
        **RUN_OK,
        "status": "failed",
        "error": {"code": "CHALLENGE_REQUIRED", "message": "x", "retryable": False,
                  "requires_human_action": True},
    }

    text = render_failure(run)

    assert "没有商品" not in text
    assert "无结果" not in text
    assert "采集未成功" in text


def test_failure_rendering_surfaces_requires_human_action() -> None:
    run = {
        **RUN_OK,
        "status": "failed",
        "error": {"code": "CHALLENGE_REQUIRED", "message": "x", "retryable": False,
                  "requires_human_action": True},
    }

    assert "requires_human_action" in render_failure(run) or "需要人工" in render_failure(run)


# ---------------------------------------------------------------- HTTP 编排


def make_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://xps.test")


def test_client_never_routes_loopback_through_a_proxy(monkeypatch) -> None:
    """本 CLI 只连 127.0.0.1，必须彻底忽略代理设置。

    实测坑：macOS 系统代理（如 127.0.0.1:7890）的 ExceptionsList 虽然包含
    127.0.0.1，但 urllib.request.getproxies() **不应用** bypass 列表，httpx 用的
    正是它 —— 于是连本机 API 也被塞给代理，拿回 502。curl 会自己应用 bypass，
    所以 curl 通、httpx 不通，极易误判成服务挂了。
    """
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")

    with httpx.Client(base_url="http://127.0.0.1:8765") as default_client:
        assert default_client._mounts, "前提不成立：httpx 默认本应挂载代理"  # noqa: SLF001

    with build_client("http://127.0.0.1:8765") as client:
        assert not client._mounts  # noqa: SLF001  没有挂载任何代理


def test_build_client_uses_the_given_base_url() -> None:
    with build_client("http://127.0.0.1:9999") as client:
        assert str(client.base_url) == "http://127.0.0.1:9999"


def test_query_happy_path_returns_report_and_exit_zero() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/health":
            return httpx.Response(200, json={"status": "ok", "database": "ok"})
        if path == "/v1/auth/status":
            return httpx.Response(200, json={"state": "guest", "requires_human_action": False})
        if path == "/v1/search":
            return httpx.Response(202, json={"run_id": RUN_ID, "status": "pending",
                                            "status_url": f"/v1/search-runs/{RUN_ID}"})
        if path == f"/v1/search-runs/{RUN_ID}":
            return httpx.Response(200, json=RUN_OK)
        if path == "/v1/stats":
            return httpx.Response(200, json=STATS_OK)
        if path == "/v1/products":
            return httpx.Response(200, json=PRODUCTS_OK)
        return httpx.Response(404, json={"code": "INVALID_QUERY", "message": "?"})

    with make_client(handler) as client:
        result = query(client, keyword="富士 X-T4", poll_interval=0)

    assert isinstance(result, QueryResult)
    assert result.exit_code == 0
    assert "5249.50" in result.report
    assert "在售报价" in result.report


def test_json_mode_fetches_every_product_page_with_full_descriptions() -> None:
    items = [
        {
            **PRODUCTS_OK["items"][0],
            "product_id": index,
            "description": f"合成长描述 {index}",
        }
        for index in range(1, 151)
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/health":
            return httpx.Response(200, json={"status": "ok", "database": "ok"})
        if path == "/v1/auth/status":
            return httpx.Response(200, json={"state": "guest", "requires_human_action": False})
        if path == "/v1/search":
            return httpx.Response(202, json={"run_id": RUN_ID, "status": "pending",
                                            "status_url": f"/v1/search-runs/{RUN_ID}"})
        if path == f"/v1/search-runs/{RUN_ID}":
            return httpx.Response(200, json=RUN_OK)
        if path == "/v1/stats":
            return httpx.Response(200, json=STATS_OK)
        if path == "/v1/products":
            offset = int(request.url.params.get("offset", "0"))
            limit = int(request.url.params.get("limit", "100"))
            return httpx.Response(
                200,
                json={**PRODUCTS_OK, "items": items[offset : offset + limit], "total": len(items),
                      "limit": limit, "offset": offset},
            )
        raise AssertionError(path)

    with make_client(handler) as client:
        result = query(client, keyword="富士 X-T4", poll_interval=0, fetch_all=True)

    assert len(result.products) == 150
    assert result.products[-1]["description"] == "合成长描述 150"


def test_reuse_run_skips_search_submission() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        seen.append(path)
        if path == "/health":
            return httpx.Response(200, json={"status": "ok", "database": "ok"})
        if path == "/v1/auth/status":
            return httpx.Response(200, json={"state": "guest", "requires_human_action": False})
        if path == f"/v1/search-runs/{RUN_ID}":
            return httpx.Response(200, json=RUN_OK)
        if path == "/v1/stats":
            return httpx.Response(200, json=STATS_OK)
        if path == "/v1/products":
            return httpx.Response(200, json=PRODUCTS_OK)
        raise AssertionError(path)

    with make_client(handler) as client:
        result = query(client, keyword="", reuse_run_id=RUN_ID, poll_interval=0)

    assert result.exit_code == 0
    assert "/v1/search" not in seen


def test_query_stops_and_explains_when_the_run_failed() -> None:
    failed_run = {
        **RUN_OK,
        "status": "failed",
        "error": {"code": "CHALLENGE_REQUIRED", "message": "需要验证", "retryable": False,
                  "requires_human_action": True},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/health":
            return httpx.Response(200, json={"status": "ok", "database": "ok"})
        if path == "/v1/auth/status":
            return httpx.Response(200, json={"state": "guest", "requires_human_action": False})
        if path == "/v1/search":
            return httpx.Response(202, json={"run_id": RUN_ID, "status": "pending",
                                            "status_url": f"/v1/search-runs/{RUN_ID}"})
        if path == f"/v1/search-runs/{RUN_ID}":
            return httpx.Response(200, json=failed_run)
        raise AssertionError(f"失败 run 不该继续请求 {path}")

    with make_client(handler) as client:
        result = query(client, keyword="富士 X-T4", poll_interval=0)

    assert result.exit_code == 3
    assert "CHALLENGE_REQUIRED" in result.report
    assert "闲鱼 App" in result.report


def test_query_tells_the_agent_to_start_the_service_when_it_is_down() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with make_client(handler) as client:
        result = query(client, keyword="富士 X-T4", poll_interval=0)

    assert result.exit_code == 2
    assert "start-local.sh" in result.report


def test_query_reports_validation_errors_without_submitting_a_crawl() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "database": "ok"})
        if request.url.path == "/v1/auth/status":
            return httpx.Response(200, json={"state": "guest", "requires_human_action": False})
        return httpx.Response(422, json={"code": "UNSUPPORTED_FILTER", "message": "city 未验证",
                                         "run_id": None, "retryable": False,
                                         "requires_human_action": False})

    with make_client(handler) as client:
        result = query(client, keyword="富士 X-T4", poll_interval=0)

    assert result.exit_code == 2
    assert "UNSUPPORTED_FILTER" in result.report
    assert "/v1/search-runs" not in seen, "被拒后不该再去轮询"


def test_query_gives_up_after_the_timeout_instead_of_polling_forever() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/health":
            return httpx.Response(200, json={"status": "ok", "database": "ok"})
        if path == "/v1/auth/status":
            return httpx.Response(200, json={"state": "guest", "requires_human_action": False})
        if path == "/v1/search":
            return httpx.Response(202, json={"run_id": RUN_ID, "status": "pending",
                                            "status_url": f"/v1/search-runs/{RUN_ID}"})
        return httpx.Response(200, json={**RUN_OK, "status": "running"})

    with make_client(handler) as client:
        result = query(client, keyword="富士 X-T4", poll_interval=0, timeout=0.05)

    assert result.exit_code == 4
    assert RUN_ID in result.report
    assert "超时" in result.report
