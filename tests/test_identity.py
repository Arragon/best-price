"""商品身份（identity_key）测试。

真实 targetUrl 形态（2026-09-22 实测抓取）：
    fleamarket://item?id=1083967235157&referPageArgs=%E5%AF%8C%E5%A3%AB+X-T4
                      &gulSource=search&extra=%7B...%7D

指南 §7 明确禁止上游那种 `link.split("&", 1)[0]` 的构造方式：它在参数顺序
变化时会碰撞或漏匹配。下列 test_param_order_does_not_change_key 就是为此存在。
"""

from __future__ import annotations

import hashlib

import pytest

from xps.services.identity import Identity, resolve_identity

# [实测] 真实 targetUrl，带 referPageArgs / gulSource / extra 跟踪参数
REAL_URL = (
    "fleamarket://item?id=1083967235157"
    "&referPageArgs=%E5%AF%8C%E5%A3%AB+X-T4&gulSource=search&extra=%7B%22a%22%3A1%7D"
)


def test_platform_item_id_takes_priority() -> None:
    identity = resolve_identity(item_id="1083967235157", target_url=None)

    assert identity.identity_key == "xianyu:item:1083967235157"
    assert identity.platform_item_id == "1083967235157"
    assert identity.id_source == "platform_item_id"


def test_canonical_url_strips_all_tracking_params() -> None:
    identity = resolve_identity(item_id=None, target_url=REAL_URL)

    assert identity.canonical_url == "https://www.goofish.com/item?id=1083967235157"
    assert "referPageArgs" not in identity.canonical_url
    assert "gulSource" not in identity.canonical_url
    assert "extra" not in identity.canonical_url


def test_fleamarket_scheme_is_the_real_observed_form() -> None:
    """上游拿到的就是 fleamarket://，必须能识别，不能一律当非法 scheme 拒掉。"""
    identity = resolve_identity(item_id=None, target_url=REAL_URL)

    assert identity.id_source == "platform_item_id"
    assert identity.platform_item_id == "1083967235157"


def test_same_item_different_tracking_params_share_one_key() -> None:
    """§11：相同商品不同跟踪参数 → 同一 identity_key。"""
    a = resolve_identity(None, "https://www.goofish.com/item?id=999&spm=a21ybx.search.0.0")
    b = resolve_identity(None, "https://www.goofish.com/item?id=999&gulSource=search")
    c = resolve_identity(None, "https://www.goofish.com/item?id=999")

    assert a.identity_key == b.identity_key == c.identity_key == "xianyu:item:999"


def test_param_order_does_not_change_key() -> None:
    """这条测试专门杀掉 split('&', 1)[0] 那种实现。"""
    first = resolve_identity(None, "https://www.goofish.com/item?id=999&spm=x")
    second = resolve_identity(None, "https://www.goofish.com/item?spm=x&id=999")

    assert first.identity_key == second.identity_key == "xianyu:item:999"


def test_different_items_never_collide() -> None:
    """§11：不同商品即使标题价格相同也不得合并。"""
    a = resolve_identity("1083967235157", None)
    b = resolve_identity("1085916193246", None)

    assert a.identity_key != b.identity_key


def test_identity_ignores_title_and_price_entirely() -> None:
    """身份只由平台 ID / URL 决定，签名里根本没有标题价格参数。"""
    with pytest.raises(TypeError):
        resolve_identity("1", None, title="富士 X-T4")  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.com/item?id=999",          # 不可信主机
        "https://goofish.com.evil.example/item?id=999",  # 后缀伪造
        "javascript:alert(1)",                           # 非 http(s)
        "data:text/html,<script>",                       # 非 http(s)
        "http://m.tb.cn/h.abc123",                       # 伪造短链
        "ftp://www.goofish.com/item?id=999",             # 非 http(s)
        "",                                              # 空
        "not a url at all",                              # 不可解析
    ],
)
def test_untrusted_url_is_flagged_invalid_identity(url: str) -> None:
    identity = resolve_identity(item_id=None, target_url=url)

    assert identity.id_source == "invalid_identity"
    assert identity.platform_item_id is None
    assert identity.canonical_url is None


def test_invalid_identity_is_still_storable_and_unique() -> None:
    """§6 要求 identity_key NOT NULL UNIQUE，无效身份也要可存可追溯，但会被打标排除。"""
    a = resolve_identity(None, "https://evil.example.com/item?id=1")
    b = resolve_identity(None, "https://evil.example.com/item?id=2")

    assert a.identity_key.startswith("xianyu:invalid:")
    assert a.identity_key != b.identity_key


def test_both_missing_is_invalid_identity() -> None:
    identity = resolve_identity(item_id=None, target_url=None)

    assert identity.id_source == "invalid_identity"


@pytest.mark.parametrize("bad_id", ["abc", "-1", "0", "", "12ab", "1.5", "  "])
def test_non_numeric_item_id_is_rejected(bad_id: str) -> None:
    """不能把任意字符串当平台 ID；也不能把数据库自增 ID 伪造成 source_id。"""
    assert resolve_identity(bad_id, None).id_source == "invalid_identity"


def test_url_without_id_param_falls_back_to_sha256_fingerprint() -> None:
    url = "https://www.goofish.com/detail/abc123?spm=a21ybx.search.0.0"
    identity = resolve_identity(None, url)

    expected = hashlib.sha256("https://www.goofish.com/detail/abc123".encode()).hexdigest()
    assert identity.id_source == "url_fingerprint"
    assert identity.identity_key == f"xianyu:url:{expected}"
    assert identity.platform_item_id is None
    assert identity.canonical_url == "https://www.goofish.com/detail/abc123"


def test_fingerprint_ignores_tracking_params() -> None:
    a = resolve_identity(None, "https://www.goofish.com/detail/abc?spm=1&gulSource=search")
    b = resolve_identity(None, "https://www.goofish.com/detail/abc?spm=2")

    assert a.identity_key == b.identity_key


def test_returns_frozen_identity_dataclass() -> None:
    identity = resolve_identity("999", None)

    assert isinstance(identity, Identity)
    with pytest.raises(Exception):
        identity.identity_key = "tampered"  # type: ignore[misc]
