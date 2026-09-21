"""闲鱼适配器：复用上游的签名/协议层，解析与身份判定全部自有。

许可边界：上游 `superboyyy/xianyu_spider` 无 LICENSE 文件（README 声称 MIT 但引用
文件不存在），故本项目**不复制其任何源码**，只以独立 checkout + 进程内 import 的
方式调用其公开函数。详见 spec §1.3。

刻意不复用上游的 `scrape_xianyu_http` / `handle_data` / `save_to_db`：
- gather 并发抓页拿不到逐页成败，无法如实报告 pages_fetched / partial
- handle_data 会注入「暂无」「价格异常」占位伪数据，且用 float 换算「万」
- get_link_unique_key 用 `link.split("&", 1)[0]` 造身份键（指南 §7 明令禁止）
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from xps.adapters.base import (
    AUTH_EXPIRED,
    AUTH_GUEST,
    AUTH_LOGGED_IN,
    AUTH_UNKNOWN,
    AuthStatus,
    CrawlResult,
    PageOutcome,
    RawListing,
)
from xps.errors import UpstreamError
from xps.services.identity import resolve_identity

logger = logging.getLogger(__name__)

ADAPTER_VERSION = "xps-xianyu/0.1.0"

# 上游 checkout 位置；许可未确认，永不 vendor 进本仓库
DEFAULT_UPSTREAM_PATH = Path(__file__).resolve().parents[3] / "upstream" / "xianyu_spider"

# 原始载荷限长：够追溯，又不会把 SQLite 撑爆
RAW_PAYLOAD_MAX_BYTES = 4096
_SOURCE_URL_BYTES = 2048
_SHORT_FIELD_BYTES = 64
_MAX_PRICE_PARTS = 6

_PUBLISH_TIME_DIGITS = 13
_FLEAMARKET_PREFIX = "fleamarket://"


def _truncate_bytes(text: str, limit: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    return encoded[:limit].decode("utf-8", "ignore") + "…"


def _clean(value: Any) -> str | None:
    """把空串、None、纯空白统一成 None —— 缺失就是缺失，不填占位值。"""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _path(node: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _publish_time_to_iso(raw: Any) -> str | None:
    text = _clean(raw)
    # 只接受 13 位毫秒时间戳。上游若改成秒级，宁可返回 None 也不产出 1970 年的假日期。
    if not text or not text.isdigit() or len(text) != _PUBLISH_TIME_DIGITS:
        return None
    moment = datetime.fromtimestamp(int(text) / 1000, tz=timezone.utc)
    if not 1990 <= moment.year <= 2100:
        return None
    return moment.isoformat().replace("+00:00", "Z")


def _price_text(parts: Any) -> str | None:
    """实测价格由 sign/integer/decimal 三段拼成，漏掉 decimal 会少 5 角。"""
    if not isinstance(parts, list):
        return None
    joined = "".join(
        str(part.get("text", "")) for part in parts if isinstance(part, dict)
    ).strip()
    return joined or None


def _image_url(value: Any) -> str | None:
    text = _clean(value)
    if text is None:
        return None
    return f"https:{text}" if text.startswith("//") else text


def _build_raw_payload(
    source_url: str | None,
    item_id: str | None,
    publish_time: str | None,
    parts: Any,
) -> dict[str, Any]:
    """最小必要原始字段。canonical_url 剥掉了跟踪参数，原始形态在此留档以便追溯。

    只放公开商品字段；Cookie / token / 用户身份一律不入内。
    """
    payload: dict[str, Any] = {}
    if source_url:
        payload["source_url"] = _truncate_bytes(source_url, _SOURCE_URL_BYTES)
    if item_id:
        payload["item_id"] = _truncate_bytes(item_id, _SHORT_FIELD_BYTES)
    if publish_time:
        payload["publish_time"] = _truncate_bytes(publish_time, _SHORT_FIELD_BYTES)
    if isinstance(parts, list):
        payload["price_parts"] = [
            {
                "type": _truncate_bytes(str(part.get("type", "")), _SHORT_FIELD_BYTES),
                "text": _truncate_bytes(str(part.get("text", "")), _SHORT_FIELD_BYTES),
            }
            for part in parts[:_MAX_PRICE_PARTS]
            if isinstance(part, dict)
        ]
    if len(json.dumps(payload, ensure_ascii=False).encode()) > RAW_PAYLOAD_MAX_BYTES:
        payload.pop("price_parts", None)
    return payload


def entry_to_raw_listing(entry: Any) -> RawListing:
    """单个 mtop resultList 条目 → RawListing。结构漂移时降级为坏条目而非抛异常。"""
    main = _path(entry, "data", "item", "main")
    ex_content = _path(main, "exContent")
    click_args = _path(main, "clickParam", "args")

    source_url = _clean(_path(main, "targetUrl"))
    item_id = _clean(_path(ex_content, "itemId")) or _clean(_path(click_args, "item_id"))
    price_parts = ex_content.get("price") if isinstance(ex_content, dict) else None

    # 身份解析同时给出剥除跟踪参数后的 canonical_url
    canonical = resolve_identity(item_id, source_url).canonical_url or ""

    return RawListing(
        source_id=item_id,
        url=canonical,
        title=_clean(_path(ex_content, "title")),
        price_text=_price_text(price_parts),
        seller_name=_clean(_path(ex_content, "userNickName")),
        area=_clean(_path(ex_content, "area")),
        image_url=_image_url(_path(ex_content, "picUrl")),
        published_at=_publish_time_to_iso(_path(click_args, "publishTime")),
        is_auction=bool(_path(ex_content, "isAuction")),
        is_ad=bool(_path(ex_content, "isAliMaMaAD")),
        raw_payload=_build_raw_payload(source_url, item_id, _clean(_path(click_args, "publishTime")), price_parts),
    )


def result_info(raw: Any) -> dict[str, Any]:
    info = _path(raw, "data", "resultInfo")
    return info if isinstance(info, dict) else {}


def extract_listings(raw: Any) -> list[RawListing]:
    entries = _path(raw, "data", "resultList")
    if not isinstance(entries, list):
        return []
    return [entry_to_raw_listing(entry) for entry in entries]


class XianyuUpstreamAdapter:
    """进程内包装上游 mtop 层。上游导入延迟到首次使用，离线测试无需上游存在。"""

    def __init__(
        self,
        upstream_path: Path | str = DEFAULT_UPSTREAM_PATH,
        *,
        seconds_between_pages: float = 3.0,
        source_commit: str | None = None,
    ) -> None:
        self._upstream_path = Path(upstream_path)
        self._seconds_between_pages = seconds_between_pages
        self._mtop: Any = None
        self._filters_cls: Any = None
        self.source_commit = source_commit

    # -- 上游装载 ----------------------------------------------------------

    def _load(self) -> None:
        if self._mtop is not None:
            return
        if not self._upstream_path.is_dir():
            raise UpstreamError(
                "UPSTREAM_UNAVAILABLE",
                f"未找到上游 checkout：{self._upstream_path}（先运行 scripts/setup.sh）",
            )
        path_text = str(self._upstream_path)
        if path_text not in sys.path:
            sys.path.insert(0, path_text)
        try:
            from xianyu import mtop  # noqa: PLC0415  上游包名，非本项目模块
            from xianyu.search_query import SearchFilters  # noqa: PLC0415
        except ImportError as exc:
            raise UpstreamError(
                "UPSTREAM_UNAVAILABLE",
                f"无法导入上游 xianyu 包：{exc}（依赖是否已装入同一 venv？）",
            ) from exc
        self._mtop = mtop
        self._filters_cls = SearchFilters

    async def _ensure_init(self) -> None:
        self._load()
        try:
            await self._mtop.init()
        except Exception as exc:
            raise UpstreamError(
                "UPSTREAM_UNAVAILABLE",
                f"上游初始化失败（取不到匿名 token）：{type(exc).__name__}",
                retryable=True,
            ) from exc

    # -- 契约实现 ----------------------------------------------------------

    async def auth_status(self) -> AuthStatus:
        try:
            await self._ensure_init()
            snapshot = await self._mtop.probe_login()
        except UpstreamError as exc:
            return AuthStatus(AUTH_UNKNOWN, hint=exc.message)
        except Exception as exc:
            logger.warning("probe_login 失败：%s", type(exc).__name__)
            return AuthStatus(AUTH_UNKNOWN, hint="登录态探测失败")

        if snapshot.get("logged_in"):
            return AuthStatus(AUTH_LOGGED_IN)
        if snapshot.get("login_expired"):
            # 只回传机器可读状态，绝不回传 Cookie / user_id 原值
            return AuthStatus(
                AUTH_EXPIRED,
                requires_human_action=True,
                hint="登录已失效；如需登录态数据请在本机运行 scripts/login.sh",
            )
        return AuthStatus(AUTH_GUEST)

    async def search(
        self,
        keyword: str,
        max_pages: int,
        sort: str,
        min_price: Decimal | None,
        max_price: Decimal | None,
        city: str | None,
    ) -> CrawlResult:
        await self._ensure_init()

        auth = await self.auth_status()
        filters = self._filters_cls(
            sort=sort,
            min_price=int(min_price) if min_price is not None else None,
            max_price=int(max_price) if max_price is not None else None,
            city=city,
        )

        listings: list[RawListing] = []
        outcomes: list[PageOutcome] = []
        warnings: list[str] = []
        has_next: bool | None = None

        for page in range(1, max_pages + 1):
            if page > 1:
                # 逐页串行 + 节流，而不是上游那种 Semaphore(3) 并发
                await asyncio.sleep(self._seconds_between_pages)

            if has_next is False:
                outcomes.append(
                    PageOutcome(page, False, 0, "NO_MORE_PAGES", "平台报告没有下一页")
                )
                warnings.append(f"page_{page}_not_available:hasNextPage=false")
                continue

            try:
                raw = await self._mtop.search(keyword, page, filters=filters)
            except UpstreamError:
                raise
            except Exception as exc:
                code, message = _classify_upstream_exception(exc)
                outcomes.append(PageOutcome(page, False, 0, code, message))
                warnings.append(f"page_{page}_failed:{code}")
                if code in {"CHALLENGE_REQUIRED", "RATE_LIMITED", "AUTH_REQUIRED"}:
                    # 风控/验证/需登录：立即停止，不继续撞
                    break
                continue

            info = result_info(raw)
            has_next = info.get("hasNextPage")
            page_listings = extract_listings(raw)
            outcomes.append(PageOutcome(page, True, len(page_listings)))
            listings.extend(page_listings)

            if not page_listings:
                control = info.get("searchResControlFields") or {}
                if control.get("hasItems") is False:
                    warnings.append(f"page_{page}_verified_empty")
                else:
                    warnings.append(f"page_{page}_empty_but_platform_says_has_items")

        return CrawlResult(
            listings=tuple(listings),
            auth_mode=auth.state,
            pages_requested=max_pages,
            pages_fetched=sum(1 for outcome in outcomes if outcome.fetched),
            warnings=tuple(warnings),
            pages=tuple(outcomes),
            has_next_page=has_next,
        )


def _classify_upstream_exception(exc: Exception) -> tuple[str, str]:
    """把上游 RuntimeError(f"搜索接口调用失败: {ret}") 的 ret 码映射成本项目错误码。"""
    text = str(exc).upper()
    if isinstance(exc, TimeoutError) or "TIMEOUT" in text or "TIMED OUT" in text:
        return "UPSTREAM_TIMEOUT", "上游请求超时"
    if "RGV587" in text or "USER_VALIDATE" in text or "滑块" in str(exc) or "验证" in str(exc):
        return "CHALLENGE_REQUIRED", "平台要求人工验证，已停止自动操作"
    if "FLOW_LIMIT" in text or "ILLEGAL_ACCESS" in text or "LIMIT" in text:
        return "RATE_LIMITED", "触发平台频率限制，已停止自动重试"
    if "SESSION_EXPIRED" in text or "NEED_LOGIN" in text:
        return "AUTH_REQUIRED", "需要登录态"
    if "TOKEN_EMPTY" in text or "TOKEN_EXPIRED" in text or "_EXPIRED" in text:
        return "AUTH_EXPIRED", "上游 token 失效"
    if "FAIL_SYS" in text or "调用失败" in str(exc):
        return "UPSTREAM_CHANGED", f"上游接口返回异常：{str(exc)[:200]}"
    return "UPSTREAM_UNAVAILABLE", f"{type(exc).__name__}: {str(exc)[:200]}"
