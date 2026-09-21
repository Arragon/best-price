"""GET /help：让 agent 自助发现本服务怎么用，不必先去读文档。

端点清单**从 `app.openapi()` 派生**，请求字段清单**从 `SearchSubmitRequest` 派生**，
错误码行动指引**复用 `errors.AGENT_ACTIONS`** —— 三个来源都是运行时真相，
所以 help 不会和实际 API 漂移（tests/test_help.py 里另有拿真实 APIRoute 反向核对的测试）。

OpenAPI 只描述「有什么参数」；本端点补上它不表达的两件事：
**价格口径纪律**，以及**遇到每个错误码时调用方该做什么**。
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse

from xps import __version__
from xps.api.schemas import (
    KEYWORD_MAX_LENGTH,
    PRODUCTS_DEFAULT_LIMIT,
    PRODUCTS_MAX_LIMIT,
    UNVERIFIED_FILTERS,
    SearchSubmitRequest,
)
from xps.errors import AGENT_ACTIONS, ALL_CODES, http_status, is_retryable, requires_human
from xps.settings import Settings

router = APIRouter(tags=["help"])

_SERVICE_NAME = "闲鱼本地商品搜索与价格统计服务"

_CALL_FLOW = (
    {
        "method": "POST",
        "path": "/v1/search",
        "purpose": "提交一次真实搜索；立即返回 202 + run_id，不要在这里等结果",
    },
    {
        "method": "GET",
        "path": "/v1/search-runs/{run_id}",
        "purpose": "轮询直到 status 进入 succeeded / partial / failed / blocked_login；请设超时上限",
    },
    {
        "method": "GET",
        "path": "/v1/products",
        "purpose": "取本轮真实商品与可打开的原始链接（run_id 必填）",
    },
    {
        "method": "GET",
        "path": "/v1/stats",
        "purpose": "取中位数 / 分位数 / 样本量 / 排除原因（run_id 必填，绝不跨轮混合）",
    },
)

_STATUS_VALUES = {
    "pending": "已入库，排队中（单实例串行，可能因节流而等待）",
    "running": "正在采集",
    "succeeded": "请求的页全部抓到；结果可用",
    "partial": "只抓到部分页；结果可用但样本不完整，必须转述 partial_pages",
    "failed": "采集失败。**不等于**平台没有商品；/v1/products 与 /v1/stats 会直接报错",
    "blocked_login": "需要登录才能继续；请用户本人完成登录后重试",
}

_MUST_REPORT = (
    "价格口径：这是采集时刻的公开在售报价，不是成交价",
    "样本量：eligible_count，以及 insufficient_sample 是否为真",
    "排除情况：excluded_count、excluded_by_reason 分组、needs_review_count",
    "可追溯链接：lowest_items 或商品列表里的 canonical_url",
    "采集时间与状态：started_at / ended_at / status / auth_mode / partial",
    "采集质量限制：sample_quality 数组原样转述",
)

_MUST_NOT = (
    "不得把挂牌价说成成交价，也不得把「在售报价」表述为「市场行情」或「公允价」",
    "不得把 status=failed 读成「平台没有商品」；空列表只在平台自报 hasItems=false 时才成立",
    "requires_human_action=true 时不得自动重试，必须把具体动作交还用户",
    "RATE_LIMITED / CHALLENGE_REQUIRED 时不得换账号、换代理或调小节流参数硬撞",
    "insufficient_sample=true 时不得给出确定性结论，只能说明已知样本",
    "不得跨 run_id 混合历史数据来凑样本量",
    "不得用图片或大模型推断未公开的商品参数来补齐规格",
)


def _endpoints(spec: dict[str, Any]) -> list[dict[str, Any]]:
    schemas = (spec.get("components") or {}).get("schemas") or {}
    entries: list[dict[str, Any]] = []
    for path, methods in sorted(spec.get("paths", {}).items()):
        for method, operation in sorted(methods.items()):
            parameters = operation.get("parameters") or []
            entry: dict[str, Any] = {
                "method": method.upper(),
                "path": path,
                "summary": operation.get("summary") or "",
                "required_params": [
                    item["name"] for item in parameters if item.get("required")
                ],
                "optional_params": [
                    item["name"] for item in parameters if not item.get("required")
                ],
            }
            # POST 的入参在请求体里，不在 query；不标出来 agent 会以为无需入参
            body_schema = (
                ((operation.get("requestBody") or {}).get("content") or {})
                .get("application/json", {})
                .get("schema", {})
            )
            reference = body_schema.get("$ref")
            if reference:
                name = reference.rsplit("/", 1)[-1]
                resolved = schemas.get(name) or {}
                entry["request_body"] = {
                    "schema": name,
                    "required": list(resolved.get("required") or []),
                    "fields": list((resolved.get("properties") or {}).keys()),
                }
            entries.append(entry)
    return entries


def build_help(app: Any, settings: Settings) -> dict[str, Any]:
    spec = app.openapi()
    return {
        "service": _SERVICE_NAME,
        "version": __version__,
        "purpose": (
            "在本地对闲鱼做真实搜索，把商品去重入库，按规格筛掉租赁/求购/配件/故障/定金占位等"
            "非可比样本，给出可追溯的在售报价分布。单平台 MVP，只监听 127.0.0.1。"
        ),
        "price_semantics": {
            "what_it_is": "采集时刻的公开在售报价（挂牌价）",
            "what_it_is_not": [
                "成交价",
                "含国补 / 优惠券 / 议价后的价格",
                "历史最低价",
            ],
            "currency": "CNY",
            "api_unit": '保留两位的人民币字符串（如 "5249.50"）或 null；内部一律存整数分',
            "quantiles": (
                "inclusive 线性插值，等价 statistics.quantiles(n=4, method='inclusive')，"
                "全程 Decimal 实现，结果按 ROUND_HALF_UP 取整到分"
            ),
            "null_means": "字段缺失就是 null，服务不会填「暂无」之类的占位值",
        },
        "one_shot_client": (
            'scripts/query-price.sh "富士 X-T4" --kind body --pages 2'
            "  —— 已封装四步调用、超时、错误分诊与转述纪律；有 shell 权限时优先用它"
        ),
        "call_flow": list(_CALL_FLOW),
        "endpoints": _endpoints(spec),
        "search_request": {
            "content_type": "application/json",
            "fields": list(SearchSubmitRequest.model_fields),
            "item_kind_values": ["body", "kit", "any"],
            "sort_values": ["newest", "price_asc", "price_desc", "default"],
            "unsupported_filters": list(UNVERIFIED_FILTERS),
            "unsupported_reason": (
                "上游 SearchFilters 支持这些参数，但平台是否真按其过滤**未经实测验证**。"
                "传非 null 会得到 422 UNSUPPORTED_FILTER，而不是被静默忽略或谎称已过滤。"
            ),
            "item_kind_note": "item_kind 是**本地后置过滤**，不是平台过滤条件",
        },
        "limits": {
            "max_pages_default": 1,
            "max_pages_ceiling": settings.max_search_pages,
            "keyword_max_length": KEYWORD_MAX_LENGTH,
            "products_default_limit": PRODUCTS_DEFAULT_LIMIT,
            "products_max_limit": PRODUCTS_MAX_LIMIT,
            "min_sample_threshold": settings.min_sample_threshold,
            "min_seconds_between_searches": settings.min_seconds_between_searches,
            "max_concurrent_searches": settings.max_concurrent_searches,
        },
        "status_values": dict(_STATUS_VALUES),
        "error_codes": {
            code: {
                "http_status": http_status(code),
                "retryable": is_retryable(code),
                "requires_human_action": requires_human(code),
                "agent_action": AGENT_ACTIONS[code],
            }
            for code in sorted(ALL_CODES)
        },
        "failure_semantics": {
            "empty_list_means": (
                "200 + 空列表**只**表示经核验的真实无结果（平台自报 "
                "resultInfo.searchResControlFields.hasItems=false），并会带 "
                "page_N_verified_empty 警告。"
            ),
            "failed_run_means": (
                "status=failed 表示采集失败，此时 /v1/products 与 /v1/stats 会按上游错误码"
                "直接报错而不是返回空列表。这是采集失败，不是平台查不到商品。"
            ),
            "partial_run_means": (
                "status=partial 表示部分页成功；/v1/stats 会同时给出 partial=true 与 "
                "sample_quality 里的 partial_pages:已抓/请求。可用，但必须转述该限制。"
            ),
            "interrupted_run_means": (
                "服务重启会把遗留的 pending/running 判为 failed + RUN_INTERRUPTED，"
                "不会永远挂在 running。"
            ),
        },
        "must_report": list(_MUST_REPORT),
        "must_not": list(_MUST_NOT),
        "auth": {
            "login_required_for_search": False,
            "note": (
                "实测未登录(guest)即可搜索。/v1/auth/status 只读本地凭证快照，"
                "verified=false 表示未向平台主动校验——这是刻意的：主动校验会在网络抖动时"
                "销毁已保存的登录凭证。真实失效会在搜索时以 AUTH_EXPIRED 被动报出。"
            ),
            "reload": "用户在别处登录后调 POST /v1/auth/reload 即可生效，无需重启服务",
        },
        "further_reading": [
            "/openapi.json",
            "/docs",
            "README.md",
            "IMPLEMENTATION_REPORT.md",
            "docs/superpowers/specs/2026-09-22-xianyu-price-service-design.md",
        ],
    }


def render_help_text(payload: dict[str, Any]) -> str:
    """纯文本形态：便于直接塞进模型上下文，不必解析嵌套 JSON。"""
    semantics = payload["price_semantics"]
    lines = [
        f"{payload['service']} v{payload['version']}",
        "=" * 60,
        payload["purpose"],
        "",
        "【价格口径】" + semantics["what_it_is"],
        "  不是：" + "、".join(semantics["what_it_is_not"]),
        f"  单位：{semantics['api_unit']}",
        f"  分位数：{semantics['quantiles']}",
        "",
        "【一条命令】" + payload["one_shot_client"],
        "",
        "【调用流程】",
    ]
    for index, step in enumerate(payload["call_flow"], start=1):
        lines.append(f"  {index}. {step['method']} {step['path']} —— {step['purpose']}")

    lines += ["", "【端点】"]
    for entry in payload["endpoints"]:
        required = ",".join(entry["required_params"]) or "-"
        lines.append(f"  {entry['method']:<5} {entry['path']}")
        if entry["summary"]:
            lines.append(f"        {entry['summary']}")
        lines.append(f"        必填 query 参数: {required}")
        body = entry.get("request_body")
        if body:
            lines.append(
                f"        请求体 {body['schema']}：必填 "
                f"{','.join(body['required']) or '-'}；全部字段 "
                f"{', '.join(body['fields'])}"
            )

    request = payload["search_request"]
    lines += [
        "",
        "【POST /v1/search 请求体】",
        "  字段: " + ", ".join(request["fields"]),
        f"  item_kind: {' | '.join(request['item_kind_values'])}"
        f"（{request['item_kind_note']}）",
        f"  sort: {' | '.join(request['sort_values'])}",
        "  暂不支持: " + ", ".join(request["unsupported_filters"]),
        f"    原因: {request['unsupported_reason']}",
    ]

    lines += ["", "【上限】"]
    for key, value in payload["limits"].items():
        lines.append(f"  {key} = {value}")

    lines += ["", "【任务状态】"]
    for state, meaning in payload["status_values"].items():
        lines.append(f"  {state:<14} {meaning}")

    lines += ["", "【失败语义】"]
    for key, meaning in payload["failure_semantics"].items():
        lines.append(f"  {key}: {meaning}")

    lines += ["", "【错误码 → 你该做什么】"]
    for code, entry in payload["error_codes"].items():
        flags = []
        if entry["retryable"]:
            flags.append("可重试")
        if entry["requires_human_action"]:
            flags.append("需人工")
        suffix = f"（{'/'.join(flags)}）" if flags else ""
        lines.append(f"  {code} → HTTP {entry['http_status']}{suffix}")
        lines.append(f"    {entry['agent_action']}")

    lines += ["", "【转述给用户时必须包含】"]
    lines += [f"  - {item}" for item in payload["must_report"]]
    lines += ["", "【禁止】"]
    lines += [f"  - {item}" for item in payload["must_not"]]

    lines += ["", "【登录】", f"  {payload['auth']['note']}", f"  {payload['auth']['reload']}"]
    lines += ["", "【延伸阅读】" + ", ".join(payload["further_reading"])]
    return "\n".join(lines)


@router.get(
    "/",
    summary="服务指路",
    description="给先探根路径的调用方一个明确入口，而不是 404。",
)
async def root() -> dict[str, str]:
    return {
        "service": _SERVICE_NAME,
        "version": __version__,
        "help": "/help",
        "help_text": "/help?format=text",
        "docs": "/docs",
        "openapi": "/openapi.json",
        "health": "/health",
    }


@router.get(
    "/help",
    summary="agent 自助使用说明",
    description=(
        "机器可读的本服务使用说明：价格口径、调用流程、端点与参数、上限、任务状态语义、"
        "失败语义、错误码对应的行动指引、以及转述给用户时必须包含/禁止的内容。\n\n"
        "端点清单从 OpenAPI 派生，字段清单从请求模型派生，因此不会与实际 API 漂移。"
    ),
)
async def get_help(
    request: Request,
    format: Literal["json", "text"] = Query(  # noqa: A002  与 OpenAPI 惯例一致的参数名
        "json", description="json（默认）或 text（便于直接塞进模型上下文）"
    ),
):
    payload = build_help(request.app, request.app.state.settings)
    if format == "text":
        return PlainTextResponse(render_help_text(payload))
    return payload
