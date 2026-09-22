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
        "purpose": (
            "取本轮商品的完整原始字段（描述、卖家信用、地址、平台标记、主图、链接）。"
            "这是主要产出：run_id 必填"
        ),
    },
    {
        "method": "GET",
        "path": "/v1/stats",
        "purpose": (
            "取**未筛选**的中位数 / 分位数 / 样本量（run_id 必填，绝不跨轮混合）。"
            "租赁、拍卖、配件、广告位都在样本里"
        ),
    },
)

_RESEARCH_FLOW = (
    "POST /v1/researches 创建带预算和来源化约束的研究",
    "POST /v1/researches/{id}/runs 显式关联已完成的 search run",
    "POST /v1/text-analyses 提取可定位的文本证据",
    "POST /v1/evaluations 写入版本化挂牌评估",
    "GET /v1/researches/{id}/comparable-stats 读取同 SKU 去重统计",
    "GET /v1/researches/{id}/ranked 读取型号层与挂牌层分桶报告",
)

_STATUS_VALUES = {
    "pending": "已入库，排队中（单实例串行，可能因节流而等待）",
    "running": "正在采集",
    "succeeded": "结果可用；可能抓满请求页，也可能因平台正常末页 exhausted 提前结束",
    "partial": "只抓到部分页；结果可用但样本不完整，必须转述 partial_pages",
    "failed": "采集失败。**不等于**平台没有商品；/v1/products 与 /v1/stats 会直接报错",
    "blocked_login": "需要登录才能继续；请用户本人完成登录后重试",
}

_MUST_REPORT = (
    "价格口径：这是采集时刻的公开在售报价，不是成交价",
    "样本量：priced_count，以及 insufficient_sample 是否为真",
    "样本**未筛选**：sample_quality 恒含 unfiltered，租赁盘/拍卖起拍价/配件/广告位都在分布里",
    "无价条目：unpriced_count 与 unpriced_by_status（面议/缺价格控件/无法解析）",
    "平台标记：auction_count 与 ad_count",
    "可追溯链接：lowest_items 或商品列表里的 canonical_url",
    "采集时间与状态：started_at / ended_at / status / auth_mode / partial",
    "采集质量限制：sample_quality 数组原样转述",
)

_MUST_NOT = (
    "不得把挂牌价说成成交价，也不得把「在售报价」表述为「市场行情」或「公允价」",
    "不得把 stats 的分布说成已清洗过的结果——本服务不做相关性筛选，判断由你自己做",
    "不得把 status=failed 读成「平台没有商品」；空列表只在平台自报 hasItems=false 时才成立",
    "requires_human_action=true 时不得自动重试，必须把具体动作交还用户",
    "RATE_LIMITED / CHALLENGE_REQUIRED 时不得换账号、换代理或调小节流参数硬撞",
    "insufficient_sample=true 时不得给出确定性结论，只能说明已知样本",
    "不得跨 run_id 混合历史数据来凑样本量",
    "不得用图片或大模型推断未公开的商品参数来补齐规格",
)

# 透传字段清单。写死在这里而不是从 pydantic 反射，是为了给出**语义**而非只有字段名：
# 调用方需要知道每个字段是平台原话还是本服务解析结果。
_PASSTHROUGH = {
    "principle": (
        "本服务只做采集 + 清洗 + 去重，**不做相关性筛选**。"
        "平台给出的字段原样透出，缺失即 null，不填占位值，不替调用方判断哪条商品可比。"
    ),
    "platform_verbatim": {
        "title": "搜索页展示的单行标题（实测平台从不在此放换行）",
        "description": "同一篇挂牌文字但保留换行分段，最长约 1500 字。判断租赁/配件/求购看这个",
        "price.raw": "价格控件原文，如「¥5642.50」「面议」「¥90」",
        "price.original_text": "划线原价原文（少数条目才有）",
        "price.coupon_text": "券标签原文，如「券已抵50元」——展示价可能已扣券",
        "seller.credit": "信用标签原文，实测「卖家信用极好」「卖家信用优秀」",
        "seller.positive_rate": "好评率，如「39%」",
        "seller.review_count": "评价数",
        "seller.identity": "平台身份标识原文，如「闲鱼严选卖家」",
        "area": "卖家所在地（省市粒度，平台只给到这个精度）",
        "signals.published_text": "平台自报相对时间，如「8小时前发布」",
        "signals.want_count": "想要人数",
        "signals.labels": "徽标原文，实测「严选」「验货宝」",
        "signals.free_shipping": "是否包邮",
        "signals.is_auction": "拍卖位——起拍价不是普通在售报价",
        "signals.is_ad": "广告位——不是自然搜索结果",
        "media.image_url": "主图，每个商品只有 1 张",
        "media.has_video": "是否带视频",
    },
    "derived_by_service": {
        "price.yuan / price.fen": "价格解析结果，全程 Decimal；金额以字符串出 API",
        "price.parse_status": "valid / ambiguous（面议、区间、租金）/ missing / invalid",
        "canonical_url": "剥除跟踪参数后的可打开链接",
        "observed_at": "本服务采集时刻（UTC）",
    },
    "not_available": {
        "多张图片": (
            "搜索响应每个商品只给 1 张主图。多图需商品详情接口 "
            "mtop.taobao.idle.pc.detail（已从前端 bundle 核实存在，入参 {itemId}），"
            "但 2026-09-22 实测 guest 身份调用直接返回 RGV587 风控挑战。"
            "未登录拿不到，也不得绕过。"
        ),
        "成交价 / 历史价格": "平台搜索接口不返回；本服务也不做跨轮历史留存统计",
        "精确地址": "平台只给到省市（area），没有更细粒度",
    },
}


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
            "在本地对闲鱼做真实搜索，把商品去重入库并**原样透出**平台字段；"
            "另提供有预算的购买研究、证据约束文本分析、可复现挂牌评估、"
            "同 SKU 可比统计与可审计新品报价导入。"
            "原始层包括"
            "（完整描述、卖家信用、好评率、地址、想要人数、券抵扣、拍卖/广告标记、主图）"
            "与价格解析结果。本服务不在原始层做相关性筛选。"
            "另给出未筛选的算术分布（中位数/分位数）。单平台 MVP，只监听 127.0.0.1。"
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
            'scripts/query-price.sh "富士 X-T4" --pages 2 --format json --output result.json'
            "  —— JSON 会遍历本地分页并保留完整描述；有 shell 权限时优先用它"
        ),
        "call_flow": list(_CALL_FLOW),
        "research_flow": list(_RESEARCH_FLOW),
        "endpoints": _endpoints(spec),
        "search_request": {
            "content_type": "application/json",
            "fields": list(SearchSubmitRequest.model_fields),
            "sort_values": ["newest", "price_asc", "price_desc", "default"],
            "pace_values": ["economy", "balanced", "fast"],
            "cache_policy_values": ["prefer_fresh", "force_refresh"],
            "unsupported_filters": list(UNVERIFIED_FILTERS),
            "unsupported_reason": (
                "上游 SearchFilters 支持这些参数，但平台是否真按其过滤**未经实测验证**。"
                "传非 null 会得到 422 UNSUPPORTED_FILTER，而不是被静默忽略或谎称已过滤。"
            ),
            "no_relevance_filters": (
                "本服务不接受任何相关性筛选参数（历史上的 item_kind 已移除）。"
                "要按品类/成色/配置收窄，请自己读 /v1/products 的原始字段判断。"
            ),
        },
        "passthrough": dict(_PASSTHROUGH),
        "limits": {
            "max_pages_default": 1,
            "max_pages_ceiling": settings.max_search_pages,
            "keyword_max_length": KEYWORD_MAX_LENGTH,
            "products_default_limit": PRODUCTS_DEFAULT_LIMIT,
            "products_max_limit": PRODUCTS_MAX_LIMIT,
            "min_sample_threshold": settings.min_sample_threshold,
            "min_seconds_between_searches": settings.min_seconds_between_searches,
            "max_concurrent_searches": settings.max_concurrent_searches,
            "max_pending_jobs": settings.max_pending_jobs,
            "search_cache_ttl_seconds": settings.search_cache_ttl_seconds,
            "platform_floor_seconds": settings.platform_floor_seconds,
            "allow_faster_pace": settings.allow_faster_pace,
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
            "exhausted_means": (
                "平台明确报告没有下一页，是正常完成而不是 partial；"
                "search run 返回 exhausted=true 与实际 pages_fetched。"
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
            ".agents/skills/best-price/SKILL.md",
            "docs/optimization/external-gates.md",
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
    lines += ["", "【购买研究流程】"]
    lines += [f"  {index}. {step}" for index, step in enumerate(payload["research_flow"], start=1)]

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
        f"  sort: {' | '.join(request['sort_values'])}",
        f"  pace: {' | '.join(request['pace_values'])}",
        f"  cache_policy: {' | '.join(request['cache_policy_values'])}",
        "  暂不支持: " + ", ".join(request["unsupported_filters"]),
        f"    原因: {request['unsupported_reason']}",
        f"  筛选: {request['no_relevance_filters']}",
    ]

    passthrough = payload["passthrough"]
    lines += ["", "【透传字段】", f"  {passthrough['principle']}", "", "  平台原话："]
    for field, meaning in passthrough["platform_verbatim"].items():
        lines.append(f"    {field:<26} {meaning}")
    lines += ["", "  本服务解析结果："]
    for field, meaning in passthrough["derived_by_service"].items():
        lines.append(f"    {field:<26} {meaning}")
    lines += ["", "  拿不到（别试错撞墙）："]
    for field, meaning in passthrough["not_available"].items():
        lines.append(f"    {field}: {meaning}")

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
