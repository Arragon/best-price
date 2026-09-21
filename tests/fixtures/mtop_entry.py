"""合成 mtop resultList 条目构造器。

⚠️ SYNTHETIC FIXTURE —— 全部商品 ID、价格、昵称、图片链接均为虚构，
**不是真实市场报价，不得作为价格依据。**

结构严格对齐 2026-09-22 实测抓取「富士 X-T4」时观察到的真实响应路径
（见 docs/superpowers/specs/2026-09-22-xianyu-price-service-design.md §1.6），
只把取值替换为合成数据。上游接口若变更，此 fixture 需同步更新并重跑解析测试。
"""

from __future__ import annotations

from typing import Any, Sequence

DEFAULT_ITEM_ID = "9000000000001"
DEFAULT_TARGET_URL = (
    f"fleamarket://item?id={DEFAULT_ITEM_ID}"
    "&referPageArgs=%E5%AF%8C%E5%A3%AB+X-T4&gulSource=search&extra=%7B%22a%22%3A1%7D"
)

# 实测价格由 sign / integer / decimal 三段拼成，例如 ¥5642.50
PRICE_YEN_5499: Sequence[tuple[str, str]] = (("sign", "¥"), ("integer", "5499"))
PRICE_YEN_5642_50: Sequence[tuple[str, str]] = (
    ("sign", "¥"),
    ("integer", "5642"),
    ("decimal", ".50"),
)


def make_entry(
    *,
    item_id: str | None = DEFAULT_ITEM_ID,
    title: str | None = "合成商品 单机身",
    price: Sequence[tuple[str, str]] | None = PRICE_YEN_5499,
    area: str | None = "上海",
    nick: str | None = "合成卖家",
    pic_url: str | None = "//img.example.invalid/synthetic.jpg",
    publish_time: str | None = "1789989232000",
    is_auction: bool = False,
    is_ad: bool = False,
    target_url: str | None = DEFAULT_TARGET_URL,
    with_ex_content: bool = True,
) -> dict[str, Any]:
    """price 传 (type, text) 序列；传 None 表示平台没给该字段。"""
    ex_content: dict[str, Any] = {
        "itemId": item_id,
        "title": title,
        "area": area,
        "userNickName": nick,
        "picUrl": pic_url,
        "isAuction": is_auction,
        "isAliMaMaAD": is_ad,
    }
    if price is not None:
        ex_content["price"] = [{"type": kind, "text": value} for kind, value in price]

    main: dict[str, Any] = {
        "targetUrl": target_url,
        "clickParam": {"args": {"publishTime": publish_time, "item_id": item_id}},
    }
    if with_ex_content:
        main["exContent"] = ex_content
    return {"data": {"item": {"main": main}}}
