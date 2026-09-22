"""一条命令完成 POST → poll → stats → products，输出可直接转述给用户的结果。

设计重点不在 HTTP 编排，而在 `render_report` / `render_failure`：它们把
「必须转述的口径与限制」固化成代码，让调用方（人或 agent）无法只报一个中位数就走。
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import httpx

from xps.errors import AGENT_ACTIONS

TERMINAL_STATUSES = frozenset({"succeeded", "partial", "failed", "blocked_login"})

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_CRAWL_FAILED = 3
EXIT_TIMEOUT = 4

SORT_OPTIONS = ("newest", "price_asc", "price_desc", "default")

_DEFAULT_STEP = "查看服务日志；必要时重跑 scripts/verify_upstream.py 核对上游接口。"


@dataclass(frozen=True)
class QueryResult:
    exit_code: int
    report: str
    run: dict[str, Any] | None = None
    stats: dict[str, Any] | None = None
    products: tuple[dict[str, Any], ...] = ()

    def as_json(self) -> dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "message": self.report,
            "run": self.run,
            "stats": self.stats,
            "products": list(self.products),
        }


def _yuan(value: str | None) -> str:
    return f"¥{value}" if value else "—"


def _seller_line(item: dict[str, Any]) -> str:
    """把卖家侧信号压成一行：这些正是判断「这个报价可不可信」要看的。"""
    seller = item.get("seller") or {}
    signals = item.get("signals") or {}
    parts = [str(seller.get("display_name")) if seller.get("display_name") else "卖家未知"]
    if item.get("area"):
        parts.append(str(item["area"]))
    if seller.get("credit"):
        parts.append(str(seller["credit"]))
    rate = seller.get("positive_rate")
    if rate and seller.get("review_count") is not None:
        parts.append(f"好评率{rate}({seller['review_count']}评价)")
    elif rate:
        parts.append(f"好评率{rate}")
    if seller.get("identity"):
        parts.append(str(seller["identity"]))
    marks = []
    if signals.get("is_auction"):
        marks.append("拍卖起拍价")
    if signals.get("is_ad"):
        marks.append("广告位")
    price = item.get("price") or {}
    if price.get("coupon_text"):
        marks.append(str(price["coupon_text"]))
    if price.get("parse_status") and price["parse_status"] != "valid":
        marks.append(f"价格{price['parse_status']}")
    if signals.get("want_count") is not None:
        marks.append(f"{signals['want_count']}人想要")
    if marks:
        parts.append("⚠ " + "/".join(marks))
    return " · ".join(parts)


# ---------------------------------------------------------------- 渲染


def render_report(
    *,
    run: dict[str, Any],
    stats: dict[str, Any],
    products: Sequence[dict[str, Any]],
    top: int = 5,
) -> str:
    priced = stats.get("priced_count") or 0
    lines: list[str] = [
        f"══ 闲鱼在售报价 · {run.get('keyword')} ══",
        "",
        "口径：以下是**采集时刻的公开在售报价**，不是成交价，不含国补 / 优惠券 / 议价结果。",
        "样本：**未筛选**。租赁盘、拍卖起拍价、配件、广告位全都在里面，由你自己判断可比性。",
        "",
    ]

    if priced:
        lines.append(f"中位数 {_yuan(stats.get('median_yuan'))}    有价样本 {priced} 件")
        lines.append(
            f"  min {_yuan(stats.get('min_yuan'))} · P25 {_yuan(stats.get('p25_yuan'))}"
            f" · P75 {_yuan(stats.get('p75_yuan'))} · max {_yuan(stats.get('max_yuan'))}"
        )
    else:
        lines.append("中位数 —    有价样本 0 件（没有任何一条价格能解析成数字）")

    unpriced = stats.get("unpriced_by_status") or {}
    unpriced_text = (
        " · ".join(f"{name} {count}" for name, count in sorted(unpriced.items()))
        if unpriced
        else "无"
    )
    lines += [
        "",
        f"样本构成：原始 {stats.get('raw_count')} 条 → 去重 {stats.get('distinct_count')} 件",
        f"  有价 {priced} · 无价 {stats.get('unpriced_count')}（{unpriced_text}）",
        f"  其中平台标记：拍卖 {stats.get('auction_count')} · 广告位 {stats.get('ad_count')}",
        "",
    ]

    if stats.get("insufficient_sample"):
        lines.append(
            f"⚠ 样本不足：有价样本 {priced} 件 < 8。"
            "以上数字只反映已采集到的样本，不要据此下确定性结论。"
        )
    if stats.get("partial"):
        lines.append("⚠ 本轮仅部分页采集成功，样本可能不完整。")
    quality = stats.get("sample_quality") or []
    if quality:
        lines.append("采集质量限制：" + " · ".join(str(item) for item in quality))

    entries = list(products[:top])
    if entries:
        lines += ["", f"最低 {len(entries)} 件（可点开核对；链接已剥除跟踪参数）："]
        for entry in entries:
            title = str(entry.get("title") or "(无标题)").replace("\n", " ")[:46]
            price = (entry.get("price") or {}).get("yuan")
            lines.append(f"  {_yuan(price):>10}  {title}")
            lines.append(f"{'':>13}{_seller_line(entry)}")
            if entry.get("canonical_url"):
                lines.append(f"{'':>13}{entry['canonical_url']}")

    lines += [
        "",
        f"来源：run_id={run.get('run_id')}",
        f"      采集 {run.get('started_at')} → {run.get('ended_at')}"
        f" · auth={run.get('auth_mode')}"
        f" · pages={run.get('pages_fetched')}/{run.get('pages_requested')}",
        f"      adapter={run.get('adapter_version')}"
        f" · upstream_commit={run.get('source_commit')}",
        "",
        "转述给用户时必须包含：口径（在售报价，非成交价）、样本量、样本未筛选这一事实、"
        "采集质量限制、商品链接。要判断某条是否可比，用 /v1/products 读完整描述与卖家信用。",
    ]
    return "\n".join(lines)


def render_failure(run: dict[str, Any]) -> str:
    error = run.get("error") or {}
    code = error.get("code") or "UNKNOWN"
    lines = [
        "✗ 本轮采集未成功。这是采集失败，不是「平台查不到商品」。",
        "",
        f"run_id : {run.get('run_id')}",
        f"status : {run.get('status')}",
        f"错误码 : {code}",
        f"原因   : {error.get('message') or '（未提供）'}",
        f"可重试 : {'是' if error.get('retryable') else '否'}",
        "requires_human_action : "
        + ("是（需要人工处理）" if error.get("requires_human_action") else "否"),
        f"已抓页数 : {run.get('pages_fetched')}/{run.get('pages_requested')}",
        "",
        "下一步：" + AGENT_ACTIONS.get(code, _DEFAULT_STEP),
    ]
    warnings = run.get("warnings") or []
    if warnings:
        lines.append("")
        lines.append("警告：" + " · ".join(str(item) for item in warnings))
    return "\n".join(lines)


def _service_down_text(detail: str) -> str:
    return "\n".join(
        [
            "✗ 连不上本地服务。",
            f"  {detail}",
            "",
            "先启动： scripts/start-local.sh",
            "默认地址 http://127.0.0.1:8765；改过端口就用 --base-url 指定。",
        ]
    )


def _error_text(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"✗ 请求被拒（HTTP {response.status_code}）：{response.text[:300]}"
    lines = [
        f"✗ 请求被拒（HTTP {response.status_code}）",
        f"错误码：{body.get('code')}",
        f"说明：{body.get('message')}",
    ]
    if body.get("retryable"):
        lines.append("可重试：是")
    if body.get("requires_human_action"):
        lines.append("需要人工处理：是")
    step = AGENT_ACTIONS.get(str(body.get("code")))
    if step:
        lines.append("下一步：" + step)
    return "\n".join(lines)


def _timeout_text(run_id: str, run: dict[str, Any] | None, timeout: float) -> str:
    return "\n".join(
        [
            f"✗ 轮询超时（{timeout:.0f}s），任务仍未结束。",
            f"run_id : {run_id}",
            f"最后状态：{(run or {}).get('status')}",
            "",
            f"稍后自行查询： curl -sS <BASE>/v1/search-runs/{run_id}",
            "注意：服务重启会把遗留任务判为 failed + RUN_INTERRUPTED。",
        ]
    )


# ---------------------------------------------------------------- 编排


def query(
    client: httpx.Client,
    *,
    keyword: str,
    max_pages: int = 1,
    sort: str = "newest",
    min_price: str | None = None,
    max_price: str | None = None,
    poll_interval: float = 2.0,
    timeout: float = 300.0,
    top: int = 5,
    pace: str = "balanced",
    cache_policy: str = "prefer_fresh",
    reuse_run_id: str | None = None,
    fetch_all: bool = False,
) -> QueryResult:
    try:
        health = client.get("/health")
    except httpx.HTTPError as exc:
        return QueryResult(EXIT_USAGE, _service_down_text(f"{type(exc).__name__}: {exc}"))
    if health.status_code != 200:
        return QueryResult(EXIT_USAGE, _service_down_text(f"/health → HTTP {health.status_code}"))

    try:
        auth = client.get("/v1/auth/status").json()
    except (httpx.HTTPError, ValueError):
        auth = {}

    if reuse_run_id:
        run_id = reuse_run_id
    else:
        payload: dict[str, Any] = {
            "keyword": keyword,
            "max_pages": max_pages,
            "sort": sort,
            "pace": pace,
            "cache_policy": cache_policy,
        }
        if min_price is not None:
            payload["min_price_yuan"] = str(min_price)
        if max_price is not None:
            payload["max_price_yuan"] = str(max_price)

        try:
            accepted = client.post("/v1/search", json=payload)
        except httpx.HTTPError as exc:
            return QueryResult(EXIT_USAGE, _service_down_text(f"{type(exc).__name__}: {exc}"))
        if accepted.status_code != 202:
            return QueryResult(EXIT_USAGE, _error_text(accepted))
        run_id = accepted.json()["run_id"]

    deadline = time.monotonic() + timeout
    run: dict[str, Any] = {}
    while True:
        try:
            run = client.get(f"/v1/search-runs/{run_id}").json()
        except (httpx.HTTPError, ValueError) as exc:
            return QueryResult(EXIT_USAGE, _service_down_text(f"轮询失败：{exc}"))
        if run.get("status") in TERMINAL_STATUSES:
            break
        if time.monotonic() >= deadline:
            return QueryResult(EXIT_TIMEOUT, _timeout_text(run_id, run, timeout), run=run)
        time.sleep(poll_interval)

    if run.get("status") in {"failed", "blocked_login"}:
        return QueryResult(EXIT_CRAWL_FAILED, render_failure(run), run=run)

    try:
        stats = client.get("/v1/stats", params={"run_id": run_id}).json()
        page_limit = 100 if fetch_all else max(1, min(top, 100))
        product_items: list[dict[str, Any]] = []
        offset = 0
        while True:
            products = client.get(
                "/v1/products",
                params={
                    "run_id": run_id,
                    "priced_only": False if fetch_all else True,
                    "limit": page_limit,
                    "offset": offset,
                },
            ).json()
            items = products.get("items") or []
            product_items.extend(items)
            offset += len(items)
            if not fetch_all or not items or offset >= int(products.get("total") or 0):
                break
    except (httpx.HTTPError, ValueError) as exc:
        return QueryResult(EXIT_USAGE, _service_down_text(f"取结果失败：{exc}"), run=run)

    header = f"登录态：{auth.get('state', 'unknown')}"
    if auth.get("verified") is False:
        header += "（本地凭证，未向平台主动校验）"

    body = render_report(
        run=run, stats=stats, products=product_items, top=top
    )
    return QueryResult(
        EXIT_OK,
        f"{header}\n\n{body}",
        run=run,
        stats=stats,
        products=tuple(product_items),
    )


def build_client(base_url: str, timeout: float = 60.0) -> httpx.Client:
    """只连本机，故 `trust_env=False`：不读 HTTP_PROXY，也不读 macOS 系统代理。

    实测坑：macOS 系统代理（如 127.0.0.1:7890）的 ExceptionsList 虽然包含 127.0.0.1，
    但 `urllib.request.getproxies()` **不应用** bypass 列表，而 httpx 用的正是它 ——
    于是连本机 API 也被塞给代理，拿回 502。curl 会自己应用 bypass，所以
    curl 通、httpx 不通，极易被误判成服务挂了。
    """
    return httpx.Client(base_url=base_url, timeout=timeout, trust_env=False)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="闲鱼在售报价一键查询（POST → poll → stats → products）",
        epilog="退出码：0 成功 / 2 服务或参数问题 / 3 采集失败 / 4 轮询超时",
    )
    parser.add_argument("keyword", nargs="?", help="搜索关键词，如「富士 X-T4」「RTX 4090」")
    parser.add_argument("--pages", type=int, default=1, help="抓取页数，默认 1")
    parser.add_argument("--sort", default="newest", choices=SORT_OPTIONS)
    parser.add_argument("--min-price", help="最低价（元）")
    parser.add_argument("--max-price", help="最高价（元）")
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--timeout", type=float, default=300.0, help="轮询总超时秒数")
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--top", type=int, default=5, help="列出最低 N 件样本")
    parser.add_argument("--pace", choices=("economy", "balanced", "fast"), default="balanced")
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="跳过近期结果缓存；仍受全局节流和风控停止状态约束",
    )
    parser.add_argument("--reuse-run", metavar="RUN_ID", help="复用已有 run，跳过 POST")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--output", type=Path, help="把输出写入文件；JSON 模式包含全量分页商品")
    args = parser.parse_args(argv)

    if not args.keyword and not args.reuse_run:
        parser.error("必须提供 keyword，或使用 --reuse-run RUN_ID")

    with build_client(args.base_url) as client:
        result = query(
            client,
            keyword=args.keyword or "",
            max_pages=args.pages,
            sort=args.sort,
            min_price=args.min_price,
            max_price=args.max_price,
            poll_interval=args.poll_interval,
            timeout=args.timeout,
            top=args.top,
            pace=args.pace,
            cache_policy="force_refresh" if args.force_refresh else "prefer_fresh",
            reuse_run_id=args.reuse_run,
            fetch_all=args.format == "json",
        )
    rendered = (
        json.dumps(result.as_json(), ensure_ascii=False, indent=2)
        if args.format == "json"
        else result.report
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
