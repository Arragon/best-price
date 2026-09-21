"""商品身份键。

指南 §7 的优先级：实测确认的平台商品 ID → 已验证官方 URL 的稳定参数 →
剥掉跟踪参数的规范化 URL 的 SHA-256。

明确禁止上游 `link.split("&", 1)[0]` 的做法：本次实测 targetUrl 恰好把 `id`
放在第一个参数，所以那种写法侥幸可用；参数顺序一变就会碰撞或漏匹配。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlparse

PLATFORM_ITEM_ID = "platform_item_id"
URL_FINGERPRINT = "url_fingerprint"
INVALID_IDENTITY = "invalid_identity"

# 仅接受实测确认的闲鱼主机。短链、其他域名、非 http(s) 一律拒绝。
_ALLOWED_HOSTS = frozenset({"goofish.com", "www.goofish.com", "h5.m.goofish.com"})
_CANONICAL_HOST = "www.goofish.com"

_ITEM_ID_RE = re.compile(r"^[1-9][0-9]*$")


@dataclass(frozen=True)
class Identity:
    identity_key: str
    platform_item_id: str | None
    canonical_url: str | None
    id_source: str


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _invalid(raw: str | None) -> Identity:
    # 仍然给出可存的 key（§6 要求 NOT NULL UNIQUE），由调用方打 invalid_identity 标并排除出统计
    return Identity(f"xianyu:invalid:{_sha256(raw or '')}", None, None, INVALID_IDENTITY)


def _parse_trusted(url: str) -> tuple[str, str] | None:
    """→ (规范化 base, item_id 或空串)；主机或协议不可信则 None。"""
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()

    if scheme == "fleamarket":
        # 实测真实形态就是 fleamarket://item?id=...，不能一律当非法 scheme 拒掉
        if parsed.netloc.lower() != "item":
            return None
        base = f"https://{_CANONICAL_HOST}/{parsed.netloc.lower()}"
    elif scheme in ("http", "https"):
        host = (parsed.hostname or "").lower()
        if host not in _ALLOWED_HOSTS:
            return None
        base = f"https://{host}{parsed.path}"
    else:
        return None

    item_id = ""
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key == "id" and _ITEM_ID_RE.match(value.strip()):
            item_id = value.strip()
            break
    return base, item_id


def resolve_identity(item_id: str | None, target_url: str | None) -> Identity:
    if item_id is not None and _ITEM_ID_RE.match(item_id.strip()):
        platform_id = item_id.strip()
        return Identity(
            f"xianyu:item:{platform_id}",
            platform_id,
            f"https://{_CANONICAL_HOST}/item?id={platform_id}",
            PLATFORM_ITEM_ID,
        )

    if not target_url or not target_url.strip():
        return _invalid(target_url)

    parsed = _parse_trusted(target_url.strip())
    if parsed is None:
        return _invalid(target_url)

    base, url_item_id = parsed
    if url_item_id:
        return Identity(
            f"xianyu:item:{url_item_id}",
            url_item_id,
            f"{base}?id={url_item_id}",
            PLATFORM_ITEM_ID,
        )
    return Identity(f"xianyu:url:{_sha256(base)}", None, base, URL_FINGERPRINT)
