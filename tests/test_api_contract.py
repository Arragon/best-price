"""API 契约测试（指南 §8）。

全部通过 tests/fake_adapter.py 的合成数据驱动，**不打网络、不访问真实闲鱼**。
返回的价格均为虚构，不得当作市场结果。
"""

from __future__ import annotations

import logging
import time
from decimal import Decimal
from logging.handlers import RotatingFileHandler

import pytest

from tests.fake_adapter import FakeAdapter, body_listings, synthetic_listing
from tests.helpers import build_client, search_and_wait, submit, wait_for_run
from xps.main import configure_logging
from xps.settings import Settings

# ---------------------------------------------------------------- 提交与轮询


def test_search_returns_202_with_run_id_and_status_url(client) -> None:
    response = submit(client)

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "pending"
    assert body["run_id"]
    assert body["status_url"] == f"/v1/search-runs/{body['run_id']}"


def test_completed_run_reports_layered_counts(client) -> None:
    run = search_and_wait(client)

    assert run["status"] == "succeeded"
    assert run["platform"] == "xianyu"
    assert run["keyword"] == "富士 X-T4"
    assert run["auth_mode"] == "guest"
    assert run["pages_requested"] == 1
    assert run["pages_fetched"] == 1
    assert run["raw_count"] == 8
    assert run["distinct_count"] == 8
    assert run["priced_count"] == 8
    assert run["error"] is None
    assert run["started_at"] and run["ended_at"]


def test_max_pages_defaults_to_one(client, adapter) -> None:
    submit(client, {"keyword": "富士 X-T4"})

    deadline = time.monotonic() + 5
    while not adapter.calls and time.monotonic() < deadline:
        time.sleep(0.02)

    assert adapter.calls[0]["max_pages"] == 1


def test_price_filters_are_passed_as_decimal(client, adapter) -> None:
    search_and_wait(
        client,
        {"keyword": "富士 X-T4", "min_price_yuan": "2500", "max_price_yuan": "6500.50"},
    )

    assert adapter.calls[0]["min_price"] == Decimal(2500)
    assert adapter.calls[0]["max_price"] == Decimal("6500.50")


def test_relevance_filters_are_not_accepted_at_all(client, adapter) -> None:
    """本服务不做相关性筛选，所以不接受任何相关性参数。

    历史上这里有 item_kind（单机身/套机）。它靠相机词表判断，换个品类就把
    全部商品推去 review、样本归零。参数已移除：宁可 422 明确拒绝，
    也不要收下一个会被静默忽略、让调用方以为已过滤的参数。
    """
    response = submit(client, {"keyword": "富士 X-T4", "item_kind": "body"})

    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_QUERY"
    assert adapter.calls == [], "被拒的请求不得触发对平台的真实搜索"


def test_unknown_run_returns_404(client) -> None:
    response = client.get("/v1/search-runs/does-not-exist")

    assert response.status_code == 404
    assert response.json()["code"] == "INVALID_QUERY"


# ---------------------------------------------------------------- 入参校验


@pytest.mark.parametrize(
    "payload",
    [
        {"keyword": ""},
        {"keyword": "   "},
        {},
        {"keyword": "x" * 65},
        {"keyword": "富士 X-T4", "max_pages": 0},
        {"keyword": "富士 X-T4", "max_pages": 4},
        {"keyword": "富士 X-T4", "sort": "cheapest"},
        {"keyword": "富士 X-T4", "min_price_yuan": "abc"},
        {"keyword": "富士 X-T4", "min_price_yuan": "-1"},
        {"keyword": "富士 X-T4", "min_price_yuan": "100", "max_price_yuan": "50"},
        {"keyword": "富士 X-T4", "item_kind": "lens"},
    ],
)
def test_invalid_input_is_rejected_422(client, payload) -> None:
    response = submit(client, payload)

    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_QUERY"


@pytest.mark.parametrize(
    "payload",
    [
        {"keyword": "富士 X-T4", "city": "深圳"},
        {"keyword": "富士 X-T4", "province": "广东"},
        {"keyword": "富士 X-T4", "publish_days": 7},
    ],
)
def test_unverified_filters_are_rejected_as_unsupported(client, payload) -> None:
    """§8.1：未验证支持的搜索条件返回 422 UNSUPPORTED_FILTER，不暗称已由平台过滤。"""
    response = submit(client, payload)

    assert response.status_code == 422
    assert response.json()["code"] == "UNSUPPORTED_FILTER"


def test_null_city_is_accepted(client) -> None:
    assert submit(client, {"keyword": "富士 X-T4", "city": None}).status_code == 202


# ---------------------------------------------------------------- 商品列表


def test_products_returns_this_rounds_items(client) -> None:
    run = search_and_wait(client)

    response = client.get(f"/v1/products?run_id={run['run_id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 8
    assert len(body["items"]) == 8


def test_product_item_exposes_every_required_field(client) -> None:
    run = search_and_wait(client)

    item = client.get(f"/v1/products?run_id={run['run_id']}").json()["items"][0]

    for field in (
        "product_id",
        "title",
        "description",
        "canonical_url",
        "price",
        "seller",
        "area",
        "media",
        "signals",
        "published_at",
        "observed_at",
        "source_run_id",
    ):
        assert field in item
    assert item["source_run_id"] == run["run_id"]
    assert item["canonical_url"].startswith("https://www.goofish.com/item?id=")
    for tracking in ("gulSource", "referPageArgs", "spm", "extra"):
        assert tracking not in item["canonical_url"]


def test_product_item_passes_platform_fields_through_verbatim(tmp_path) -> None:
    """这是本端点存在的理由：平台给出的判断依据必须完整到达调用方。

    少透一个字段，agent 就少一份判断「这条报价可不可比」的依据。
    """
    adapter = FakeAdapter(
        pages=[
            [
                synthetic_listing(
                    "7001",
                    title="合成 X-T4 单机身",
                    description="合成 X-T4 单机身\n无拆无修\n配件：电池2块",
                    credit="卖家信用极好",
                    review_count=318,
                    positive_rate="39%",
                    seller_identity="闲鱼严选卖家",
                    published_text="6小时前发布",
                    want_count=8,
                    coupon_text="券已抵50元",
                    free_shipping=True,
                    badges=("验货宝",),
                    ori_price="¥6999",
                    has_video=True,
                    is_auction=True,
                    is_ad=True,
                )
            ]
        ]
    )
    with build_client(tmp_path, adapter) as test_client:
        run = search_and_wait(test_client)
        item = test_client.get(f"/v1/products?run_id={run['run_id']}").json()["items"][0]

    assert item["description"] == "合成 X-T4 单机身\n无拆无修\n配件：电池2块"
    assert item["seller"] == {
        "display_name": "合成卖家",
        "credit": "卖家信用极好",
        "review_count": 318,
        "positive_rate": "39%",
        "identity": "闲鱼严选卖家",
        "avatar_url": "https://img.example.invalid/synthetic-avatar.jpg",
    }
    assert item["signals"] == {
        "published_text": "6小时前发布",
        "want_count": 8,
        "free_shipping": True,
        "labels": ["验货宝"],
        "is_auction": True,
        "is_ad": True,
    }
    assert item["price"]["coupon_text"] == "券已抵50元"
    assert item["price"]["original_text"] == "¥6999"
    assert item["media"] == {
        "image_url": "https://img.example.invalid/synthetic.jpg",
        "has_video": True,
    }


def test_price_yuan_is_a_string_with_two_decimals(client) -> None:
    run = search_and_wait(client)

    item = client.get(f"/v1/products?run_id={run['run_id']}").json()["items"][0]

    assert item["price"]["yuan"] == "4800.00"
    assert item["price"]["fen"] == 480_000
    assert item["price"]["raw"] == "¥4800"
    assert item["price"]["parse_status"] == "valid"


def test_missing_fields_are_null_never_placeholders(tmp_path) -> None:
    """§8.3：可见字段缺失即 null，不能插入「暂无」的占位伪数据。"""
    adapter = FakeAdapter(
        pages=[
            [
                synthetic_listing(
                    "7777",
                    title=None,
                    area=None,
                    nick=None,
                    price_text=None,
                    credit=None,
                    review_count=None,
                    positive_rate=None,
                    avatar_url=None,
                    published_text=None,
                )
            ]
        ]
    )
    with build_client(tmp_path, adapter) as test_client:
        run = search_and_wait(test_client)
        item = test_client.get(f"/v1/products?run_id={run['run_id']}").json()["items"][0]

    assert item["title"] is None
    assert item["area"] is None
    assert item["price"]["yuan"] is None
    assert item["price"]["raw"] is None
    assert item["seller"]["display_name"] is None
    assert item["seller"]["credit"] is None
    assert item["seller"]["review_count"] is None
    assert item["seller"]["positive_rate"] is None
    assert item["seller"]["avatar_url"] is None
    assert item["signals"]["published_text"] is None
    assert item["signals"]["want_count"] is None
    blob = str(item).lower()
    for placeholder in ("暂无", "未知", "匿名"):
        assert placeholder not in blob


def test_products_paginates_with_default_50_and_max_100(tmp_path) -> None:
    adapter = FakeAdapter(pages=[body_listings(7001, 120)])
    with build_client(tmp_path, adapter) as test_client:
        run = search_and_wait(test_client)
        default_page = test_client.get(f"/v1/products?run_id={run['run_id']}").json()
        capped = test_client.get(f"/v1/products?run_id={run['run_id']}&limit=1000")

    assert len(default_page["items"]) == 50
    assert default_page["total"] == 120
    assert capped.status_code == 422


def test_products_priced_only_keeps_anything_with_a_parseable_price(tmp_path) -> None:
    """priced_only 不是相关性筛选：¥50 的「电池」照样算有价条目。

    旧实现在这里把配件判成 accessory_only 排除掉。本服务不再判断可比性，
    要排除请调用方自己读 description 与 signals 决定。
    """
    adapter = FakeAdapter(
        pages=[[synthetic_listing("7001"), synthetic_listing("7002", title="X-T4 电池", price_text="50")]]
    )
    with build_client(tmp_path, adapter) as test_client:
        run = search_and_wait(test_client)
        body = test_client.get(
            f"/v1/products?run_id={run['run_id']}&priced_only=true"
        ).json()

    assert body["total"] == 2
    assert {item["title"] for item in body["items"]} == {"合成 富士 X-T4 单机身", "X-T4 电池"}


def test_unpriced_listings_are_still_returned_with_their_raw_text(tmp_path) -> None:
    """「面议」解析不出数字，但条目与原文都必须还在——这是调用方的判断依据。"""
    adapter = FakeAdapter(
        pages=[[synthetic_listing("7001", title="合成 面议", price_text="面议")]]
    )
    with build_client(tmp_path, adapter) as test_client:
        run = search_and_wait(test_client)
        priced = test_client.get(
            f"/v1/products?run_id={run['run_id']}&priced_only=true"
        ).json()
        everything = test_client.get(f"/v1/products?run_id={run['run_id']}").json()

    assert priced["total"] == 0
    assert everything["total"] == 1
    item = everything["items"][0]
    assert item["price"]["raw"] == "¥面议"
    assert item["price"]["yuan"] is None
    assert item["price"]["parse_status"] == "ambiguous"


def test_products_requires_run_id(client) -> None:
    assert client.get("/v1/products").status_code == 422


# ---------------------------------------------------------------- 统计


def test_stats_reports_median_and_sample_size(client) -> None:
    run = search_and_wait(client)

    body = client.get(f"/v1/stats?run_id={run['run_id']}").json()

    # 8 件合成商品：480000..550000 分，步长 10000
    assert body["priced_count"] == 8
    assert body["unpriced_count"] == 0
    assert body["min_yuan"] == "4800.00"
    assert body["max_yuan"] == "5500.00"
    assert body["median_yuan"] == "5150.00"
    assert body["insufficient_sample"] is False
    assert body["currency"] == "CNY"


def test_stats_always_declares_itself_unfiltered(client) -> None:
    """这份分布没有清洗过，任何转述都不能假装它清洗过。"""
    run = search_and_wait(client)

    body = client.get(f"/v1/stats?run_id={run['run_id']}").json()

    assert "unfiltered" in body["sample_quality"]


def test_stats_exposes_traceable_extremes(client) -> None:
    run = search_and_wait(client)

    body = client.get(f"/v1/stats?run_id={run['run_id']}").json()

    assert body["lowest_items"]
    assert body["highest_items"]
    assert all(entry["canonical_url"] for entry in body["lowest_items"])
    assert body["lowest_items"][0]["price_yuan"] == "4800.00"
    assert body["highest_items"][0]["price_yuan"] == "5500.00"


def test_stats_requires_run_id(client) -> None:
    """§8.4：run_id 必填，默认绝不跨日期/跨关键词混合历史数据。"""
    assert client.get("/v1/stats").status_code == 422


def test_stats_rejects_the_removed_item_kind_filter(client) -> None:
    """item_kind 已从服务里移除；传它必须 422，不能被静默忽略。"""
    run = search_and_wait(client)

    response = client.get(f"/v1/stats?run_id={run['run_id']}&item_kind=body")

    assert response.status_code == 422


def test_stats_includes_auction_and_ad_items_and_says_so(tmp_path) -> None:
    """拍卖起拍价与广告位都留在分布里，但必须计数并给出限制标记。"""
    adapter = FakeAdapter(
        pages=[
            [
                synthetic_listing("7001", price_text="5000"),
                synthetic_listing("7002", price_text="100", is_auction=True),
                synthetic_listing("7003", price_text="9000", is_ad=True),
            ]
        ]
    )
    with build_client(tmp_path, adapter) as test_client:
        run = search_and_wait(test_client)
        body = test_client.get(f"/v1/stats?run_id={run['run_id']}").json()

    assert body["priced_count"] == 3
    assert body["auction_count"] == 1
    assert body["ad_count"] == 1
    assert body["min_yuan"] == "100.00"
    assert body["max_yuan"] == "9000.00"
    assert "includes_auction_start_prices" in body["sample_quality"]
    assert "includes_promoted_ads" in body["sample_quality"]


def test_stats_marks_insufficient_sample(client, tmp_path) -> None:
    adapter = FakeAdapter(pages=[body_listings(7001, 3)])
    with build_client(tmp_path, adapter) as test_client:
        run = search_and_wait(test_client)
        body = test_client.get(f"/v1/stats?run_id={run['run_id']}").json()

    assert body["priced_count"] == 3
    assert body["insufficient_sample"] is True
    assert "insufficient_sample" in body["sample_quality"]


def test_stats_reports_unpriced_breakdown(tmp_path) -> None:
    adapter = FakeAdapter(
        pages=[
            [
                synthetic_listing("7001", price_text="5000"),
                synthetic_listing("7002", title="合成 面议", price_text="面议"),
                synthetic_listing("7003", title="合成 无价格", price_text=None),
            ]
        ]
    )
    with build_client(tmp_path, adapter) as test_client:
        run = search_and_wait(test_client)
        body = test_client.get(f"/v1/stats?run_id={run['run_id']}").json()

    assert body["priced_count"] == 1
    assert body["unpriced_count"] == 2
    assert body["unpriced_by_status"] == {"ambiguous": 1, "missing": 1}
    assert "some_prices_unparsed" in body["sample_quality"]


# ---------------------------------------------------------------- 失败语义


def test_adapter_failure_is_not_disguised_as_empty_results(tmp_path) -> None:
    """§0.6：采集器失败必须保存失败状态与原因，不能 200 + 空列表伪装「没有商品」。"""
    adapter = FakeAdapter(pages=[[]], page_errors={1: "CHALLENGE_REQUIRED"})
    with build_client(tmp_path, adapter) as test_client:
        run = search_and_wait(test_client)

        assert run["status"] == "failed"
        assert run["error"]["code"] == "CHALLENGE_REQUIRED"
        assert run["error"]["requires_human_action"] is True

        products = test_client.get(f"/v1/products?run_id={run['run_id']}")
        assert products.status_code == 409
        assert products.json()["code"] == "CHALLENGE_REQUIRED"


def test_partial_run_reports_missing_pages(tmp_path) -> None:
    adapter = FakeAdapter(
        pages=[body_listings(7001, 4)], page_errors={2: "UPSTREAM_TIMEOUT"}
    )
    with build_client(tmp_path, adapter) as test_client:
        response = submit(test_client, {"keyword": "富士 X-T4", "max_pages": 2})
        run = wait_for_run(test_client, response.json()["run_id"])

        assert run["status"] == "partial"
        assert run["pages_requested"] == 2
        assert run["pages_fetched"] == 1
        assert run["error"]["code"] == "UPSTREAM_TIMEOUT"

        stats = test_client.get(f"/v1/stats?run_id={run['run_id']}").json()
        assert stats["partial"] is True
        assert "partial_pages:1/2" in stats["sample_quality"]


def test_verified_empty_result_is_the_only_legitimate_empty_list(tmp_path) -> None:
    adapter = FakeAdapter(pages=[[]], verified_empty=True)
    with build_client(tmp_path, adapter) as test_client:
        run = search_and_wait(test_client)

        assert run["status"] == "succeeded"
        assert run["raw_count"] == 0
        assert "page_1_verified_empty" in run["warnings"]

        products = test_client.get(f"/v1/products?run_id={run['run_id']}")
        assert products.status_code == 200
        assert products.json()["items"] == []


def test_error_body_has_the_uniform_shape(tmp_path) -> None:
    adapter = FakeAdapter(pages=[[]], page_errors={1: "RATE_LIMITED"})
    with build_client(tmp_path, adapter) as test_client:
        run = search_and_wait(test_client)
        body = test_client.get(f"/v1/products?run_id={run['run_id']}").json()

    for field in ("code", "message", "run_id", "retryable", "requires_human_action"):
        assert field in body
    assert body["run_id"] == run["run_id"]
    assert body["retryable"] is True


# ---------------------------------------------------------------- 并发与恢复


def test_searches_are_serialised(tmp_path) -> None:
    """§8.1：单实例串行锁；两次搜索不得同时打平台。"""
    adapter = FakeAdapter(pages=[body_listings(7001, 2)], delay=0.05)
    with build_client(tmp_path, adapter) as test_client:
        first = submit(test_client, {"keyword": "富士 X-T4"}).json()["run_id"]
        second = submit(test_client, {"keyword": "9950X3D"}).json()["run_id"]
        wait_for_run(test_client, first)
        wait_for_run(test_client, second)

    assert adapter.max_active == 1
    assert len(adapter.calls) == 2


def test_interrupted_runs_are_recovered_on_startup(tmp_path) -> None:
    """§11：运行途中进程重启 → 旧 running 任务可识别为中断。"""
    database = tmp_path / "price.sqlite3"
    # 延迟留足，确保退出 with 块时任务仍在飞行中
    blocking = FakeAdapter(pages=[body_listings(7001, 2)], delay=1.5)
    with build_client(tmp_path, blocking, database_path=database) as test_client:
        run_id = submit(test_client).json()["run_id"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if test_client.get(f"/v1/search-runs/{run_id}").json()["status"] == "running":
                break
            time.sleep(0.02)

    # with 块退出即模拟进程结束；重启后应改判中断
    with build_client(tmp_path, FakeAdapter(), database_path=database) as restarted:
        body = restarted.get(f"/v1/search-runs/{run_id}").json()

    assert body["status"] == "failed"
    assert body["error"]["code"] == "RUN_INTERRUPTED"


# ---------------------------------------------------------------- 健康与登录态


def test_health_reports_app_and_database(client) -> None:
    body = client.get("/health").json()

    assert body["status"] == "ok"
    assert body["database"] == "ok"


def test_health_is_not_failed_by_a_missing_login(client) -> None:
    """§8.5：不能将登录态丢失直接当成进程不健康。"""
    assert client.get("/health").status_code == 200


def test_auth_status_exposes_machine_readable_state(client) -> None:
    body = client.get("/v1/auth/status").json()

    assert body["state"] == "guest"
    assert body["requires_human_action"] is False


def test_auth_status_never_leaks_credentials(tmp_path) -> None:
    adapter = FakeAdapter(auth_mode="expired")
    with build_client(tmp_path, adapter) as test_client:
        response = test_client.get("/v1/auth/status")

    blob = response.text.lower()
    for forbidden in ("cookie", "_m_h5_tk", "unb=", "sgcookie", "set-cookie"):
        assert forbidden not in blob
    assert response.json()["state"] == "expired"
    assert response.json()["requires_human_action"] is True


def test_auth_status_declares_it_is_not_platform_verified(client) -> None:
    """不向平台主动校验就必须如实说明，否则调用方会误以为凭证已确认有效。"""
    assert client.get("/v1/auth/status").json()["verified"] is False


def test_auth_reload_picks_up_new_credentials_without_restart(tmp_path) -> None:
    """用户在另一个终端跑完 scripts/login.sh 后，不必重启服务。"""
    adapter = FakeAdapter(auth_mode="guest")
    with build_client(tmp_path, adapter) as test_client:
        assert test_client.get("/v1/auth/status").json()["state"] == "guest"

        adapter.auth_mode = "logged_in"  # 模拟 session.json 被登录脚本写入
        body = test_client.post("/v1/auth/reload").json()

        assert body["state"] == "logged_in"
        assert adapter.reload_calls == 1
        assert test_client.get("/v1/auth/status").json()["state"] == "logged_in"


def test_auth_reload_is_not_reachable_by_get(client) -> None:
    """改状态的操作必须是 POST，避免被预取/爬虫式 GET 意外触发。"""
    assert client.get("/v1/auth/reload").status_code == 405


# ---------------------------------------------------------------- 文档


def test_openapi_document_is_served(client) -> None:
    body = client.get("/openapi.json").json()

    assert {"/v1/search", "/v1/products", "/v1/stats", "/health", "/v1/auth/status"} <= set(
        body["paths"]
    )
    assert "/v1/search-runs/{run_id}" in body["paths"]


# ---------------------------------------------------------------- 配置安全


def test_settings_refuse_non_loopback_bind_without_explicit_opt_in() -> None:
    """§9：不得只改成 0.0.0.0 就宣布安全。"""
    with pytest.raises(ValueError, match="ALLOW_REMOTE_ACCESS"):
        Settings(app_host="0.0.0.0", _env_file=None)


def test_settings_allow_remote_bind_only_with_explicit_opt_in() -> None:
    settings = Settings(app_host="0.0.0.0", allow_remote_access=True, _env_file=None)

    assert settings.app_host == "0.0.0.0"


def test_settings_default_to_loopback() -> None:
    settings = Settings(_env_file=None)

    assert settings.app_host == "127.0.0.1"
    assert settings.max_search_pages == 3


def test_settings_refuse_concurrent_searches() -> None:
    """SQLite 单写者 + 低频访问平台；配错了要立刻炸，不能静默忽略。"""
    with pytest.raises(ValueError, match="max_concurrent_searches"):
        Settings(max_concurrent_searches=4, _env_file=None)


def test_logging_uses_bounded_rotating_file(tmp_path, monkeypatch) -> None:
    log_path = tmp_path / "logs" / "bestprice.log"
    settings = Settings(
        log_file=log_path,
        log_max_bytes=1024,
        log_backup_count=2,
        _env_file=None,
    )

    captured = {}
    monkeypatch.setattr(logging, "basicConfig", lambda **kwargs: captured.update(kwargs))
    configure_logging(settings)
    try:
        rotating = [
            handler
            for handler in captured["handlers"]
            if isinstance(handler, RotatingFileHandler)
        ]
        assert len(rotating) == 1
        assert rotating[0].baseFilename == str(log_path)
        assert rotating[0].maxBytes == 1024
        assert rotating[0].backupCount == 2
    finally:
        for handler in captured["handlers"]:
            handler.close()


# ---------------------------------------------------------------- 采集来源可追溯


def test_source_commit_falls_back_to_the_recorded_file(tmp_path) -> None:
    """§6：search_runs.source_commit 用于事后判断某轮数据是哪个上游版本抓的。"""
    commit_file = tmp_path / "upstream-commit.txt"
    commit_file.write_text("eb52bd4d1901eee9ba8035e860583cddf50ead4c\n", encoding="utf-8")

    settings = Settings(upstream_commit_file=commit_file, _env_file=None)

    assert settings.resolved_source_commit() == "eb52bd4d1901eee9ba8035e860583cddf50ead4c"


def test_explicit_source_commit_wins_over_the_file(tmp_path) -> None:
    commit_file = tmp_path / "upstream-commit.txt"
    commit_file.write_text("from-file\n", encoding="utf-8")

    settings = Settings(
        xianyu_source_commit="from-env", upstream_commit_file=commit_file, _env_file=None
    )

    assert settings.resolved_source_commit() == "from-env"


def test_missing_commit_file_yields_none_not_a_guess(tmp_path) -> None:
    settings = Settings(upstream_commit_file=tmp_path / "nope.txt", _env_file=None)

    assert settings.resolved_source_commit() is None


def test_run_response_exposes_provenance(tmp_path) -> None:
    commit_file = tmp_path / "upstream-commit.txt"
    commit_file.write_text("abc123\n", encoding="utf-8")
    adapter = FakeAdapter(pages=[body_listings(7001, 2)])

    with build_client(tmp_path, adapter, upstream_commit_file=commit_file) as test_client:
        run = search_and_wait(test_client)

    assert run["source_commit"] == "abc123"
    assert run["adapter_version"]
