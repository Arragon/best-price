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
import re
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

from xps.adapters.base import (
    AUTH_GUEST,
    AUTH_LOGGED_IN,
    AUTH_UNKNOWN,
    AuthStatus,
    CrawlResult,
    PageOutcome,
    RawListing,
)
from xps.errors import HALT_CODES, UpstreamError
from xps.services.identity import resolve_identity

logger = logging.getLogger(__name__)

ADAPTER_VERSION = "xps-xianyu/0.2.0"

# 上游 checkout 位置；许可未确认，永不 vendor 进本仓库
DEFAULT_UPSTREAM_PATH = Path(__file__).resolve().parents[3] / "upstream" / "xianyu_spider"

# 原始载荷限长：够追溯，又不会把 SQLite 撑爆
RAW_PAYLOAD_MAX_BYTES = 4096
_SOURCE_URL_BYTES = 2048
_SHORT_FIELD_BYTES = 64
_MAX_PRICE_PARTS = 6
_MAX_TAG_LABELS = 12

_PUBLISH_TIME_DIGITS = 13
_FLEAMARKET_PREFIX = "fleamarket://"

# 平台标签文案（fishTags / userFishShopLabel）的实测形态。
# 只从中取数字，取不到就是 None——不按猜测填值。
_FREE_SHIPPING_ICON = "freeShippingIcon"
_WANT_RE = re.compile(r"(\d+)\s*人想要")
_REVIEW_COUNT_RE = re.compile(r"(\d+)\s*条评价")
_POSITIVE_RATE_RE = re.compile(r"好评率\s*(\d+(?:\.\d+)?\s*%)")
_COUPON_MARKER = "券"


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


def _tag_labels(ex_content: Any, group: str) -> list[str]:
    """`fishTags.<group>.tagList[].data.content`。

    实测 r1/r2/r3/r4 四组标签的文案都落在同一个键 `data.content` 上，
    所以这里只按组取文案，不对文案含义做任何推断——推断留给调用方。
    """
    tags = _path(ex_content, "fishTags", group, "tagList")
    if not isinstance(tags, list):
        return []
    return [text for text in (_clean(_path(tag, "data", "content")) for tag in tags) if text]


def _shop_labels(ex_content: Any) -> list[str]:
    """`userFishShopLabel.tagList[].data.content`：实测为「N条评价」「好评率N%」。"""
    tags = _path(ex_content, "userFishShopLabel", "tagList")
    if not isinstance(tags, list):
        return []
    return [text for text in (_clean(_path(tag, "data", "content")) for tag in tags) if text]


def _first_group_int(pattern: re.Pattern[str], texts: Sequence[str]) -> int | None:
    for text in texts:
        matched = pattern.search(text)
        if matched:
            return int(matched.group(1))
    return None


def _first_group_text(pattern: re.Pattern[str], texts: Sequence[str]) -> str | None:
    for text in texts:
        matched = pattern.search(text)
        if matched:
            return matched.group(1).replace(" ", "")
    return None


def _build_raw_payload(
    source_url: str | None,
    item_id: str | None,
    publish_time: str | None,
    parts: Any,
    tag_labels: Sequence[str],
) -> dict[str, Any]:
    """最小必要原始字段。canonical_url 剥掉了跟踪参数，原始形态在此留档以便追溯。

    `tag_labels` 是平台展示过的全部标签文案原文。已建模成独立字段的只是其中最常用的几个，
    留档全量是为了平台新增标签时不会被静默丢掉。

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
    if tag_labels:
        payload["tag_labels"] = [
            _truncate_bytes(label, _SHORT_FIELD_BYTES) for label in tag_labels[:_MAX_TAG_LABELS]
        ]
    if len(json.dumps(payload, ensure_ascii=False).encode()) > RAW_PAYLOAD_MAX_BYTES:
        payload.pop("price_parts", None)
        payload.pop("tag_labels", None)
    return payload


def entry_to_raw_listing(entry: Any) -> RawListing:
    """单个 mtop resultList 条目 → RawListing。结构漂移时降级为坏条目而非抛异常。"""
    main = _path(entry, "data", "item", "main")
    ex_content = _path(main, "exContent")
    click_args = _path(main, "clickParam", "args")
    detail_params = _path(ex_content, "detailParams")

    source_url = _clean(_path(main, "targetUrl"))
    item_id = _clean(_path(ex_content, "itemId")) or _clean(_path(click_args, "item_id"))
    price_parts = ex_content.get("price") if isinstance(ex_content, dict) else None

    # 身份解析同时给出剥除跟踪参数后的 canonical_url
    canonical = resolve_identity(item_id, source_url).canonical_url or ""

    badge_group = _tag_labels(ex_content, "r1")
    published_group = _tag_labels(ex_content, "r2")
    demand_group = _tag_labels(ex_content, "r3")
    credit_group = _tag_labels(ex_content, "r4")
    shop_group = _shop_labels(ex_content)
    all_labels = badge_group + published_group + demand_group + credit_group + shop_group

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
        raw_payload=_build_raw_payload(
            source_url,
            item_id,
            _clean(_path(click_args, "publishTime")),
            price_parts,
            all_labels,
        ),
        description=_clean(_path(detail_params, "title")),
        original_price_text=_clean(_path(ex_content, "oriPrice")),
        seller_avatar_url=_image_url(_path(ex_content, "userAvatarUrl")),
        seller_identity=_clean(_path(ex_content, "userIdentityShow")),
        seller_credit=credit_group[0] if credit_group else None,
        seller_review_count=_first_group_int(_REVIEW_COUNT_RE, shop_group),
        seller_positive_rate=_first_group_text(_POSITIVE_RATE_RE, shop_group),
        published_text=published_group[0] if published_group else None,
        want_count=_first_group_int(_WANT_RE, demand_group),
        coupon_text=next(
            (label for label in demand_group if _COUPON_MARKER in label), None
        ),
        free_shipping=_FREE_SHIPPING_ICON in badge_group,
        labels=tuple(label for label in badge_group if label != _FREE_SHIPPING_ICON),
        has_video=bool(_path(ex_content, "showVideoIcon")),
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
    ) -> None:
        self._upstream_path = Path(upstream_path)
        self._seconds_between_pages = seconds_between_pages
        self._mtop: Any = None
        self._filters_cls: Any = None
        # 上游 client 是模块级 httpx.AsyncClient，连接池绑定创建它的 loop。
        # 因此按 loop 身份判断是否需要重新 init：同一个 loop 内只 init 一次。
        self._init_loop: asyncio.AbstractEventLoop | None = None

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
        loop = asyncio.get_running_loop()
        if self._init_loop is loop:
            return
        try:
            await self._mtop.init()
        except Exception as exc:
            # 带上真实原因：本地生命周期问题（如 Event loop is closed）
            # 不该被笼统报成「平台取不到 token」
            raise UpstreamError(
                "UPSTREAM_UNAVAILABLE",
                f"上游初始化失败：{type(exc).__name__}: {exc}"[:300],
                retryable=True,
            ) from exc
        self._init_loop = loop

    # -- 契约实现 ----------------------------------------------------------

    async def auth_status(self) -> AuthStatus:
        """只读**本地内存**登录态快照，不向平台主动校验。

        为什么不调用上游 `probe_login()`：它在 `fetch_login_user()` 抛**任何**异常时
        都会走 `invalidate_expired_login()`，而后者 `client.cookies.clear()` 并
        `clear_session()` —— 直接删除 `session.json`。也就是一次网络抖动就能毁掉
        用户扫码换来的登录态，逼人重新扫脸。

        因此本项目改为：一次登录后，凭证在本进程生命周期内持续有效，不主动过期。
        真实失效由搜索时平台返回的 `ret` 码反映（映射为 AUTH_EXPIRED / AUTH_REQUIRED），
        那是有证据的被动判定，不会误删凭证。
        """
        try:
            await self._ensure_init()
            snapshot = self._mtop.login_snapshot()
        except UpstreamError as exc:
            return AuthStatus(AUTH_UNKNOWN, hint=exc.message)
        except Exception as exc:
            logger.warning("读取本地登录态失败：%s", type(exc).__name__)
            return AuthStatus(AUTH_UNKNOWN, hint="无法读取本地登录态")

        # 只回传机器可读状态，绝不回传 Cookie / user_id 原值
        if snapshot.get("logged_in"):
            return AuthStatus(
                AUTH_LOGGED_IN,
                hint="本地登录态有效；未向平台主动校验，真实失效会在搜索时以 AUTH_EXPIRED 报出",
            )
        return AuthStatus(
            AUTH_GUEST,
            hint="未登录（guest 可搜索）；如需登录态请在本机运行 scripts/login.sh 后调用 POST /v1/auth/reload",
        )

    async def reload(self) -> AuthStatus:
        """重跑 init() 以重新读取 session.json。

        用户在另一个终端执行 scripts/login.sh 后调用，无需重启服务。
        （上游 login_snapshot() 读的是内存 cookie jar，不会自己感知磁盘变化。）
        """
        self._init_loop = None
        return await self.auth_status()

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

        outcomes: list[PageOutcome] = []
        warnings: list[str] = []
        has_next: bool | None = None

        for page in range(1, max_pages + 1):
            if page > 1:
                # 逐页串行 + 节流，而不是上游那种 Semaphore(3) 并发
                await asyncio.sleep(self._seconds_between_pages)

            if has_next is False:
                outcomes.append(
                    PageOutcome(page, False, (), "NO_MORE_PAGES", "平台报告没有下一页")
                )
                warnings.append(f"page_{page}_not_available:hasNextPage=false")
                continue

            try:
                raw = await self._mtop.search(keyword, page, filters=filters)
            except Exception as exc:
                code, message = _classify_upstream_exception(exc)
                outcomes.append(PageOutcome(page, False, (), code, message))
                warnings.append(f"page_{page}_failed:{code}")
                if code in HALT_CODES:
                    # 风控 / 验证码 / 需登录：立即停止，不继续撞（指南 §9）
                    break
                continue

            info = result_info(raw)
            has_next = info.get("hasNextPage")
            page_listings = tuple(extract_listings(raw))
            outcomes.append(PageOutcome(page, True, page_listings))

            if not page_listings:
                control = info.get("searchResControlFields") or {}
                if control.get("hasItems") is False:
                    # 经核验的真实空结果，区别于上游失败
                    warnings.append(f"page_{page}_verified_empty")
                else:
                    warnings.append(f"page_{page}_empty_but_platform_says_has_items")

        return CrawlResult(
            auth_mode=auth.state,
            pages_requested=max_pages,
            pages=tuple(outcomes),
            warnings=tuple(warnings),
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
    if "SESSION_EXPIRED" in text:
        # 曾登录、凭证已失效 → 让用户重新登录；与「从未登录」的指引不同
        return "AUTH_EXPIRED", "登录会话已过期，请重新运行 scripts/login.sh"
    if "NEED_LOGIN" in text:
        return "AUTH_REQUIRED", "该操作需要登录态"
    if "TOKEN_EMPTY" in text or "TOKEN_EXPIRED" in text or "_EXPIRED" in text:
        return "AUTH_EXPIRED", "上游 token 失效"
    if "FAIL_SYS" in text or "调用失败" in str(exc):
        return "UPSTREAM_CHANGED", f"上游接口返回异常：{str(exc)[:200]}"
    return "UPSTREAM_UNAVAILABLE", f"{type(exc).__name__}: {str(exc)[:200]}"
