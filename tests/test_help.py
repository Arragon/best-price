"""`GET /help` 测试：agent 自助发现 API 用法。

核心是**防漂移**：help 里的端点清单必须与真实路由一致，字段清单必须与请求模型一致，
错误码必须全覆盖且每个都带「agent 该做什么」。否则 help 会变成一份会撒谎的文档。
"""

from __future__ import annotations

import pytest

from xps.api.schemas import SearchSubmitRequest
from xps.errors import AGENT_ACTIONS, ALL_CODES, http_status, is_retryable, requires_human
from xps.cli import render_failure
from tests.helpers import real_route_set


@pytest.fixture
def help_body(client) -> dict:
    response = client.get("/help")
    assert response.status_code == 200
    return response.json()


# ---------------------------------------------------------------- 基本可用


def test_help_is_served_at_root_level(client) -> None:
    assert client.get("/help").status_code == 200


def test_root_points_agents_to_help(client) -> None:
    """agent 常先探根路径；给它一个明确的指路，而不是 404。"""
    response = client.get("/")

    assert response.status_code == 200
    assert "/help" in response.text


def test_help_declares_price_semantics(help_body) -> None:
    """最重要的一条：不说清口径，agent 会把挂牌价当成交价报给用户。"""
    semantics = help_body["price_semantics"]

    assert "在售报价" in semantics["what_it_is"]
    assert any("成交价" in item for item in semantics["what_it_is_not"])
    assert semantics["currency"] == "CNY"


def test_help_gives_an_ordered_call_flow(help_body) -> None:
    flow = help_body["call_flow"]

    assert [step["method"] for step in flow] == ["POST", "GET", "GET", "GET"]
    assert flow[0]["path"] == "/v1/search"
    assert any("run_id" in step["path"] for step in flow)


def test_help_mentions_the_one_shot_client(help_body) -> None:
    """有 shell 权限的 agent 应当被告知更省事的入口。"""
    assert "query-price.sh" in help_body["one_shot_client"]


# ---------------------------------------------------------------- 防漂移


def test_help_lists_exactly_the_real_routes(client, help_body) -> None:
    """拿真实路由定义交叉核对，而不是和 openapi() 自证（help 正是从 openapi() 派生的）。"""
    listed = {(entry["method"], entry["path"]) for entry in help_body["endpoints"]}

    assert listed == real_route_set(client.app)


def test_route_set_helper_actually_finds_routes(client) -> None:
    """护栏：helper 若因 FastAPI 内部结构变化而返回空集，上面的相等断言就成了假阳性。"""
    real = real_route_set(client.app)

    assert ("POST", "/v1/search") in real
    assert ("GET", "/help") in real
    assert ("POST", "/v1/auth/reload") in real
    assert len(real) >= 9


def test_help_documents_every_search_request_field(help_body) -> None:
    documented = set(help_body["search_request"]["fields"])

    assert documented == set(SearchSubmitRequest.model_fields)


def test_help_declares_unverified_filters_as_unsupported(help_body) -> None:
    """必须主动告知，否则 agent 会反复撞 422 或误以为平台已过滤。"""
    assert set(help_body["search_request"]["unsupported_filters"]) == {
        "city",
        "province",
        "publish_days",
    }


def test_help_states_the_real_limits(help_body) -> None:
    limits = help_body["limits"]

    assert limits["max_pages_default"] == 1
    assert limits["keyword_max_length"] == 64
    assert limits["products_max_limit"] == 100


def test_help_documents_the_search_request_body(help_body) -> None:
    """POST 的参数在请求体里，不在 query。只列 query 参数会让 agent 以为无需入参。"""
    entry = next(item for item in help_body["endpoints"] if item["path"] == "/v1/search")

    assert entry["request_body"]["required"] == ["keyword"]
    assert set(entry["request_body"]["fields"]) == set(SearchSubmitRequest.model_fields)


def test_endpoints_without_a_body_omit_the_field(help_body) -> None:
    entry = next(item for item in help_body["endpoints"] if item["path"] == "/health")

    assert "request_body" not in entry


# ---------------------------------------------------------------- 错误码即行动指引


def test_help_covers_every_error_code(help_body) -> None:
    assert set(help_body["error_codes"]) == ALL_CODES


def test_every_error_code_entry_is_complete_and_consistent(help_body) -> None:
    for code, entry in help_body["error_codes"].items():
        assert entry["agent_action"], f"{code} 缺少 agent 行动指引"
        assert entry["http_status"] == http_status(code)
        assert entry["retryable"] is is_retryable(code)
        assert entry["requires_human_action"] is requires_human(code)


def test_agent_actions_are_total_over_all_codes() -> None:
    """单一真相来源：cli 与 /help 共用同一份指引，不能各写一套。"""
    assert set(AGENT_ACTIONS) == ALL_CODES


@pytest.mark.parametrize("code", sorted(ALL_CODES))
def test_cli_failure_rendering_uses_the_shared_action_text(code: str) -> None:
    run = {
        "run_id": "r1",
        "status": "failed",
        "pages_fetched": 0,
        "pages_requested": 1,
        "error": {
            "code": code,
            "message": "合成原因",
            "retryable": is_retryable(code),
            "requires_human_action": requires_human(code),
        },
    }

    assert AGENT_ACTIONS[code] in render_failure(run)


# ---------------------------------------------------------------- 纪律条款


def test_help_lists_what_the_agent_must_report(help_body) -> None:
    joined = " ".join(help_body["must_report"])

    for required in ("口径", "样本量", "排除", "链接", "采集"):
        assert required in joined


def test_help_lists_what_the_agent_must_not_do(help_body) -> None:
    joined = " ".join(help_body["must_not"])

    assert any("成交价" in item for item in help_body["must_not"])
    assert any("200" in item or "空列表" in item for item in help_body["must_not"])
    assert "重试" in joined


def test_help_explains_how_to_read_a_failed_run(help_body) -> None:
    """§0.6：失败不得被读成「平台没有商品」。help 必须把这条讲明白。"""
    assert help_body["failure_semantics"]["empty_list_means"]
    assert "采集失败" in help_body["failure_semantics"]["failed_run_means"]


# ---------------------------------------------------------------- 文本格式


def test_help_supports_a_plain_text_form(client) -> None:
    response = client.get("/help?format=text")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "在售报价" in response.text
    assert "/v1/search" in response.text


def test_help_rejects_an_unknown_format(client) -> None:
    response = client.get("/help?format=yaml")

    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_QUERY"


def test_help_does_not_leak_credentials_or_internal_paths(client) -> None:
    blob = client.get("/help").text.lower()

    for forbidden in ("cookie", "_m_h5_tk", "sgcookie", "session.json"):
        assert forbidden not in blob
