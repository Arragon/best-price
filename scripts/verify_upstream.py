#!/usr/bin/env python3
"""Gate A 验证探针：用上游 xianyu_spider 做一次真实低频搜索，记录原始返回结构。

    .venv/bin/python scripts/verify_upstream.py --keyword "富士 X-T4" --pages 1

安全约束：只输出登录态布尔值和公开商品数据。绝不打印 Cookie、token、user_id 原值。
原始响应落到 data/probe/（已 gitignore），供人工核对，不入库、不分发。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
UPSTREAM = ROOT / "upstream" / "xianyu_spider"
PROBE_DIR = ROOT / "data" / "probe"


def flatten(obj, prefix: str = "", out: dict | None = None, max_list: int = 2) -> dict:
    """把嵌套 JSON 摊平成 点分路径 -> 标量，便于定位真实字段路径。"""
    if out is None:
        out = {}
    if isinstance(obj, dict):
        for key, value in obj.items():
            flatten(value, f"{prefix}.{key}" if prefix else key, out, max_list)
    elif isinstance(obj, list):
        out[f"{prefix}[]"] = f"<list len={len(obj)}>"
        for index, value in enumerate(obj[:max_list]):
            flatten(value, f"{prefix}[{index}]", out, max_list)
    else:
        text = str(obj)
        out[prefix] = text if len(text) <= 140 else text[:137] + "..."
    return out


def item_id_from_url(url: str) -> str | None:
    """闲鱼商品链接里的稳定 id 参数。"""
    try:
        return (parse_qs(urlparse(url).query).get("id") or [None])[0]
    except ValueError:
        return None


async def run(keyword: str, pages: int, sort: str, dump: bool) -> int:
    sys.path.insert(0, str(UPSTREAM))
    from xianyu import mtop
    from xianyu.search_query import SearchFilters

    started = time.monotonic()
    try:
        await mtop.init()
    except Exception as exc:
        print(f"[FAIL] mtop.init() 失败: {type(exc).__name__}: {exc}")
        print("       可能是无外网、被平台拒绝，或上游协议已变。")
        return 2

    snapshot = await mtop.probe_login()
    print(f"[auth] logged_in={bool(snapshot.get('logged_in'))} "
          f"login_expired={bool(snapshot.get('login_expired'))}")
    if not snapshot.get("logged_in"):
        print("       → 本轮按未登录(guest)采集；不打印任何凭据。")

    filters = SearchFilters(sort=sort)
    seen_ids: dict[str, int] = {}

    for page in range(1, pages + 1):
        page_started = time.monotonic()
        try:
            raw = await mtop.search(keyword, page, filters=filters)
        except Exception as exc:
            print(f"[page {page}] FAIL {type(exc).__name__}: {exc}")
            return 3
        elapsed = time.monotonic() - page_started

        result_list = (raw.get("data") or {}).get("resultList") or []
        print(f"[page {page}] ret={raw.get('ret')} items={len(result_list)} "
              f"elapsed={elapsed:.2f}s")

        if dump:
            PROBE_DIR.mkdir(parents=True, exist_ok=True)
            path = PROBE_DIR / f"raw_{keyword.replace(' ', '_')}_{run.tag}_p{page}.json"
            path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"          raw → {path.relative_to(ROOT)}")

        for index, entry in enumerate(result_list):
            main = (((entry.get("data") or {}).get("item") or {}).get("main") or {})
            ex = main.get("exContent") or {}
            target = main.get("targetUrl") or ""
            url = target.replace("fleamarket://", "https://www.goofish.com/")
            item_id = item_id_from_url(url)
            if item_id:
                seen_ids[item_id] = seen_ids.get(item_id, 0) + 1

            if index < 3:
                price = ex.get("price")
                price_text = "".join(
                    str(part.get("text", "")) for part in price if isinstance(part, dict)
                ) if isinstance(price, list) else repr(price)
                print(f"          [{index}] id={item_id} price={price_text!r} "
                      f"title={str(ex.get('title'))[:50]!r}")
                print(f"              area={ex.get('area')!r} nick={ex.get('userNickName')!r}")
                print(f"              url={url[:110]}")

        if page == 1 and dump:
            first = result_list[0] if result_list else {}
            print("\n--- 第一个 resultList 条目的字段路径（摊平）---")
            for path, value in sorted(flatten(first).items()):
                print(f"  {path} = {value}")
            print("--- 摊平结束 ---\n")

        if page < pages:
            await asyncio.sleep(3)

    total = time.monotonic() - started
    duplicates = {k: v for k, v in seen_ids.items() if v > 1}
    print(f"\n[summary] keyword={keyword!r} pages={pages} sort={sort} "
          f"distinct_item_ids={len(seen_ids)} duplicated_ids={len(duplicates)} "
          f"total={total:.2f}s")
    if duplicates:
        print(f"          重复 id 示例: {list(duplicates.items())[:5]}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="上游真实采集 Gate A 探针")
    parser.add_argument("--keyword", default="富士 X-T4")
    parser.add_argument("--pages", type=int, default=1)
    parser.add_argument("--sort", default="newest",
                        choices=["newest", "price_asc", "price_desc", "default"])
    parser.add_argument("--tag", default="a", help="原始响应文件名标记，用于区分多轮")
    parser.add_argument("--no-dump", action="store_true", help="不落盘原始响应")
    args = parser.parse_args()
    run.tag = args.tag
    return asyncio.run(run(args.keyword, args.pages, args.sort, not args.no_dump))


if __name__ == "__main__":
    raise SystemExit(main())
