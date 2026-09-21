"""合成 mtop resultList 条目构造器。

⚠️ SYNTHETIC FIXTURE —— 全部商品 ID、价格、昵称、图片链接均为虚构，
**不是真实市场报价，不得作为价格依据。**

结构严格对齐 2026-09-22 实测抓取「富士 X-T4」时观察到的真实响应路径
（见 docs/superpowers/specs/2026-09-22-xianyu-price-service-design.md §1.6），
只把取值替换为合成数据。上游接口若变更，此 fixture 需同步更新并重跑解析测试。

实测确认的字段位置（60 条真实样本）：
    exContent.title                 单行标题，平台从不在此放换行
    exContent.detailParams.title    同一篇文字但保留换行分段，最长约 1500 字
    exContent.fishTags.r1           徽标：freeShippingIcon / 严选 / 验货宝
    exContent.fishTags.r2           「N小时前发布」
    exContent.fishTags.r3           「N人想要」/「券已抵50元」
    exContent.fishTags.r4           「卖家信用极好」/「卖家信用优秀」
    exContent.userFishShopLabel     「N条评价」/「好评率N%」
    四组标签的文案统一落在 tagList[].data.content 上
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


def _tags(*contents: str) -> dict[str, Any]:
    return {"tagList": [{"data": {"content": text}} for text in contents if text]}


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
    # detailParams.title：默认与 title 同文；传值可模拟带换行的完整描述
    description: str | None = None,
    ori_price: str | None = None,
    avatar_url: str | None = "//img.example.invalid/synthetic-avatar.jpg",
    seller_identity: str | None = None,
    credit: str | None = "卖家信用极好",
    review_count: int | None = 12,
    positive_rate: str | None = "98%",
    published_text: str | None = "8小时前发布",
    want_count: int | None = None,
    coupon_text: str | None = None,
    free_shipping: bool = False,
    badges: Sequence[str] = (),
    has_video: bool = False,
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
        "userAvatarUrl": avatar_url,
        "userIdentityShow": seller_identity,
        "showVideoIcon": has_video,
    }
    if price is not None:
        ex_content["price"] = [{"type": kind, "text": value} for kind, value in price]
    if ori_price is not None:
        ex_content["oriPrice"] = ori_price

    detail_params: dict[str, Any] = {"itemId": item_id}
    description_text = title if description is None else description
    if description_text is not None:
        detail_params["title"] = description_text
    ex_content["detailParams"] = detail_params

    r1 = list(badges)
    if free_shipping:
        r1.insert(0, "freeShippingIcon")
    fish_tags: dict[str, Any] = {}
    if r1:
        fish_tags["r1"] = _tags(*r1)
    if published_text:
        fish_tags["r2"] = _tags(published_text)
    r3 = [
        text
        for text in (
            f"{want_count}人想要" if want_count is not None else None,
            coupon_text,
        )
        if text
    ]
    if r3:
        fish_tags["r3"] = _tags(*r3)
    if credit:
        fish_tags["r4"] = _tags(credit)
    if fish_tags:
        ex_content["fishTags"] = fish_tags

    shop_labels = [
        text
        for text in (
            f"{review_count}条评价" if review_count is not None else None,
            f"好评率{positive_rate}" if positive_rate else None,
        )
        if text
    ]
    if shop_labels:
        ex_content["userFishShopLabel"] = _tags(*shop_labels)

    main: dict[str, Any] = {
        "targetUrl": target_url,
        "clickParam": {"args": {"publishTime": publish_time, "item_id": item_id}},
    }
    if with_ex_content:
        main["exContent"] = ex_content
    return {"data": {"item": {"main": main}}}
