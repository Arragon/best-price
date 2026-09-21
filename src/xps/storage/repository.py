"""SQLite 持久化：搜索任务、商品实体、观察快照。

写入语义（指南 §6 / §11）：
- 同一 identity_key 跨轮只对应一个 products 实体，first_seen_at 不变，last_seen_at 前移
- 同一轮内重复分页只保留首次观察（写前查重，不依赖 INSERT OR IGNORE 的 rowcount 行为）
- 无法识别身份的条目不入库，只计数，避免 identity_key 碰撞污染 products
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

from xps.services.identity import INVALID_IDENTITY as INVALID_ID_SOURCE
from xps.services.normalize import NormalizedListing
from xps.services.statistics import StatsItem

RUN_INTERRUPTED = "RUN_INTERRUPTED"
_INTERRUPT_MESSAGE = "进程重启时任务仍在进行，已判定为中断"
_ACTIVE_STATUSES = ("pending", "running")
_PLATFORM = "xianyu"


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def _loads_tuple(text: str | None) -> tuple[str, ...]:
    if not text:
        return ()
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        return ()
    return tuple(str(item) for item in loaded) if isinstance(loaded, list) else ()


def _loads_dict(text: str | None) -> dict[str, Any]:
    if not text:
        return {}
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    platform: str
    keyword: str
    filters: dict[str, Any]
    status: str
    started_at: str
    ended_at: str | None
    auth_mode: str | None
    pages_requested: int
    pages_fetched: int
    raw_count: int
    stored_count: int
    priced_count: int
    error_code: str | None
    error_message: str | None
    warnings: tuple[str, ...]
    adapter_version: str | None
    source_commit: str | None


@dataclass(frozen=True)
class ProductRecord:
    product_id: int
    platform: str
    platform_item_id: str | None
    id_source: str
    identity_key: str
    canonical_url: str | None
    title_latest: str | None
    first_seen_at: str
    last_seen_at: str


@dataclass(frozen=True)
class ObservationRecord:
    """一行 observation = 平台当时展示的一件商品的清洗后快照。

    全部字段都是平台真实给出的值，缺失即 None；本服务不附加任何相关性判断。
    """

    observation_id: int
    product_id: int
    run_id: str
    observed_at: str
    page_number: int | None
    title: str | None
    title_raw: str | None
    description: str | None
    price_raw: str | None
    price_fen: int | None
    currency: str | None
    price_parse_status: str
    original_price_text: str | None
    coupon_text: str | None
    area: str | None
    seller_display_name: str | None
    seller_credit: str | None
    seller_review_count: int | None
    seller_positive_rate: str | None
    seller_identity: str | None
    seller_avatar_url: str | None
    image_url: str | None
    has_video: bool
    published_at: str | None
    published_text: str | None
    want_count: int | None
    free_shipping: bool
    labels: tuple[str, ...]
    is_auction: bool
    is_ad: bool
    canonical_url: str | None


@dataclass(frozen=True)
class StoreSummary:
    stored: int = 0
    deduped: int = 0
    skipped_unidentifiable: int = 0
    # 有 URL 可去重、但主机不在实测确认的白名单里 → canonical_url 为 None。
    # 仍然入库（它是平台真实返回的条目），但要在 run warnings 里露出来，
    # 否则调用方会以为每条都有可点开的追溯链接。
    untrusted_identity: int = 0


def _run_record(row: sqlite3.Row) -> RunRecord:
    return RunRecord(
        run_id=row["id"],
        platform=row["platform"],
        keyword=row["keyword"],
        filters=_loads_dict(row["filters_json"]),
        status=row["status"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        auth_mode=row["auth_mode"],
        pages_requested=row["pages_requested"],
        pages_fetched=row["pages_fetched"],
        raw_count=row["raw_count"],
        stored_count=row["stored_count"],
        priced_count=row["priced_count"],
        error_code=row["error_code"],
        error_message=row["error_message"],
        warnings=_loads_tuple(row["warnings_json"]),
        adapter_version=row["adapter_version"],
        source_commit=row["source_commit"],
    )


def _product_record(row: sqlite3.Row) -> ProductRecord:
    return ProductRecord(
        product_id=row["id"],
        platform=row["platform"],
        platform_item_id=row["platform_item_id"],
        id_source=row["id_source"],
        identity_key=row["identity_key"],
        canonical_url=row["canonical_url"],
        title_latest=row["title_latest"],
        first_seen_at=row["first_seen_at"],
        last_seen_at=row["last_seen_at"],
    )


_OBSERVATION_SELECT = """
    SELECT o.id            AS observation_id,
           o.product_id    AS product_id,
           o.run_id        AS run_id,
           o.observed_at   AS observed_at,
           o.page_number   AS page_number,
           COALESCE(o.title_raw, p.title_latest) AS title,
           o.title_raw     AS title_raw,
           o.description   AS description,
           o.price_raw     AS price_raw,
           o.price_fen     AS price_fen,
           o.currency      AS currency,
           o.price_parse_status AS price_parse_status,
           o.original_price_text AS original_price_text,
           o.coupon_text   AS coupon_text,
           o.area          AS area,
           o.seller_display_name AS seller_display_name,
           o.seller_credit AS seller_credit,
           o.seller_review_count AS seller_review_count,
           o.seller_positive_rate AS seller_positive_rate,
           o.seller_identity AS seller_identity,
           o.seller_avatar_url AS seller_avatar_url,
           o.image_url     AS image_url,
           o.has_video     AS has_video,
           o.published_at  AS published_at,
           o.published_text AS published_text,
           o.want_count    AS want_count,
           o.free_shipping AS free_shipping,
           o.labels_json   AS labels_json,
           o.is_auction    AS is_auction,
           o.is_ad         AS is_ad,
           p.canonical_url AS canonical_url
      FROM observations o
      JOIN products p ON p.id = o.product_id
"""


def _observation_record(row: sqlite3.Row) -> ObservationRecord:
    return ObservationRecord(
        observation_id=row["observation_id"],
        product_id=row["product_id"],
        run_id=row["run_id"],
        observed_at=row["observed_at"],
        page_number=row["page_number"],
        title=row["title"],
        title_raw=row["title_raw"],
        description=row["description"],
        price_raw=row["price_raw"],
        price_fen=row["price_fen"],
        currency=row["currency"],
        price_parse_status=row["price_parse_status"],
        original_price_text=row["original_price_text"],
        coupon_text=row["coupon_text"],
        area=row["area"],
        seller_display_name=row["seller_display_name"],
        seller_credit=row["seller_credit"],
        seller_review_count=row["seller_review_count"],
        seller_positive_rate=row["seller_positive_rate"],
        seller_identity=row["seller_identity"],
        seller_avatar_url=row["seller_avatar_url"],
        image_url=row["image_url"],
        has_video=bool(row["has_video"]),
        published_at=row["published_at"],
        published_text=row["published_text"],
        want_count=row["want_count"],
        free_shipping=bool(row["free_shipping"]),
        labels=_loads_tuple(row["labels_json"]),
        is_auction=bool(row["is_auction"]),
        is_ad=bool(row["is_ad"]),
        canonical_url=row["canonical_url"],
    )


class Repository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # -- search_runs -------------------------------------------------------

    def create_run(
        self,
        *,
        run_id: str,
        keyword: str,
        filters: dict[str, Any] | None = None,
        pages_requested: int = 1,
        platform: str = _PLATFORM,
        adapter_version: str | None = None,
        source_commit: str | None = None,
        started_at: str | None = None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO search_runs
                       (id, platform, keyword, filters_json, status, started_at,
                        pages_requested, adapter_version, source_commit)
                   VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?)""",
                (
                    run_id,
                    platform,
                    keyword,
                    json.dumps(filters or {}, ensure_ascii=False),
                    started_at or utcnow_iso(),
                    pages_requested,
                    adapter_version,
                    source_commit,
                ),
            )

    def mark_running(self, run_id: str) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE search_runs SET status='running' WHERE id=?", (run_id,)
            )

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        pages_fetched: int = 0,
        raw_count: int = 0,
        stored_count: int = 0,
        priced_count: int = 0,
        auth_mode: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        warnings: Sequence[str] = (),
        ended_at: str | None = None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """UPDATE search_runs
                      SET status=?, ended_at=?, auth_mode=?, pages_fetched=?,
                          raw_count=?, stored_count=?, priced_count=?,
                          error_code=?, error_message=?, warnings_json=?
                    WHERE id=?""",
                (
                    status,
                    ended_at or utcnow_iso(),
                    auth_mode,
                    pages_fetched,
                    raw_count,
                    stored_count,
                    priced_count,
                    error_code,
                    error_message,
                    json.dumps(list(warnings), ensure_ascii=False),
                    run_id,
                ),
            )

    def get_run(self, run_id: str) -> RunRecord | None:
        row = self.conn.execute("SELECT * FROM search_runs WHERE id=?", (run_id,)).fetchone()
        return _run_record(row) if row else None

    def recover_interrupted_runs(self) -> int:
        """启动时调用：进程崩溃遗留的 pending/running 一律改判 failed + RUN_INTERRUPTED。"""
        placeholders = ",".join("?" for _ in _ACTIVE_STATUSES)
        with self.conn:
            cursor = self.conn.execute(
                f"""UPDATE search_runs
                       SET status='failed', error_code=?, error_message=?, ended_at=?
                     WHERE status IN ({placeholders})""",
                (RUN_INTERRUPTED, _INTERRUPT_MESSAGE, utcnow_iso(), *_ACTIVE_STATUSES),
            )
        return cursor.rowcount

    # -- products / observations -------------------------------------------

    def _upsert_product(self, entry: NormalizedListing, observed_at: str) -> int:
        identity = entry.identity
        row = self.conn.execute(
            "SELECT * FROM products WHERE identity_key=?", (identity.identity_key,)
        ).fetchone()

        if row is None:
            cursor = self.conn.execute(
                """INSERT INTO products
                       (platform, platform_item_id, id_source, identity_key, canonical_url,
                        title_latest, first_seen_at, last_seen_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    _PLATFORM,
                    identity.platform_item_id,
                    identity.id_source,
                    identity.identity_key,
                    identity.canonical_url,
                    entry.raw.title,
                    observed_at,
                    observed_at,
                ),
            )
            return int(cursor.lastrowid)

        product_id = int(row["id"])
        # 只有更新的观察才覆盖 title_latest；first_seen_at 永不变动
        if observed_at >= row["last_seen_at"]:
            self.conn.execute(
                """UPDATE products
                      SET last_seen_at=?,
                          title_latest=COALESCE(?, title_latest),
                          canonical_url=COALESCE(?, canonical_url),
                          platform_item_id=COALESCE(?, platform_item_id)
                    WHERE id=?""",
                (
                    observed_at,
                    entry.raw.title,
                    identity.canonical_url,
                    identity.platform_item_id,
                    product_id,
                ),
            )
        return product_id

    def store_listings(
        self,
        run_id: str,
        listings: Sequence[NormalizedListing],
        *,
        page_number: int | None = None,
    ) -> StoreSummary:
        stored = deduped = skipped = untrusted = 0
        with self.conn:
            for entry in listings:
                if not entry.storable:
                    skipped += 1
                    continue
                if entry.identity.id_source == INVALID_ID_SOURCE:
                    untrusted += 1

                observed_at = _iso(entry.observed_at)
                product_id = self._upsert_product(entry, observed_at)

                existing = self.conn.execute(
                    "SELECT id FROM observations WHERE run_id=? AND product_id=?",
                    (run_id, product_id),
                ).fetchone()
                if existing is not None:
                    # 同轮重复分页：首次观察为准，保留首见页码
                    deduped += 1
                    continue

                raw = entry.raw
                price = entry.price
                cursor = self.conn.execute(
                    """INSERT INTO observations
                           (product_id, run_id, observed_at, page_number, title_raw, description,
                            price_raw, price_fen, currency, price_parse_status,
                            original_price_text, coupon_text,
                            area, seller_display_name, seller_credit, seller_review_count,
                            seller_positive_rate, seller_identity, seller_avatar_url,
                            image_url, has_video, published_at, published_text, want_count,
                            free_shipping, labels_json, is_auction, is_ad, raw_payload_json)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        product_id,
                        run_id,
                        observed_at,
                        page_number,
                        raw.title,
                        raw.description,
                        price.price_raw,
                        price.price_fen,
                        price.currency,
                        price.status,
                        raw.original_price_text,
                        raw.coupon_text,
                        raw.area,
                        raw.seller_name,
                        raw.seller_credit,
                        raw.seller_review_count,
                        raw.seller_positive_rate,
                        raw.seller_identity,
                        raw.seller_avatar_url,
                        raw.image_url,
                        int(raw.has_video),
                        raw.published_at,
                        raw.published_text,
                        raw.want_count,
                        int(raw.free_shipping),
                        json.dumps(list(raw.labels), ensure_ascii=False),
                        int(raw.is_auction),
                        int(raw.is_ad),
                        json.dumps(raw.raw_payload, ensure_ascii=False)
                        if raw.raw_payload is not None
                        else None,
                    ),
                )
                self.conn.execute(
                    "UPDATE products SET latest_observation_id=? WHERE id=?",
                    (cursor.lastrowid, product_id),
                )
                stored += 1
        return StoreSummary(
            stored=stored,
            deduped=deduped,
            skipped_unidentifiable=skipped,
            untrusted_identity=untrusted,
        )

    # -- 读取 --------------------------------------------------------------

    def get_product_by_identity(self, identity_key: str) -> ProductRecord | None:
        row = self.conn.execute(
            "SELECT * FROM products WHERE identity_key=?", (identity_key,)
        ).fetchone()
        return _product_record(row) if row else None

    def count_products(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM products").fetchone()[0])

    def count_observations_for_run(self, run_id: str) -> int:
        return int(
            self.conn.execute(
                "SELECT COUNT(*) FROM observations WHERE run_id=?", (run_id,)
            ).fetchone()[0]
        )

    def count_observations_for_product(self, identity_key: str) -> int:
        return int(
            self.conn.execute(
                """SELECT COUNT(*) FROM observations o
                     JOIN products p ON p.id = o.product_id
                    WHERE p.identity_key=?""",
                (identity_key,),
            ).fetchone()[0]
        )

    def observations_of(self, identity_key: str) -> list[ObservationRecord]:
        rows = self.conn.execute(
            f"{_OBSERVATION_SELECT} WHERE p.identity_key=? ORDER BY o.observed_at, o.id",
            (identity_key,),
        ).fetchall()
        return [_observation_record(row) for row in rows]

    # 「有价格数字」而已，不是相关性筛选：解析不出价格的条目照样能通过
    # /v1/products 拿到原文，只是无法参与算术。
    _PRICED_WHERE = "o.price_parse_status = 'valid' AND o.price_fen IS NOT NULL"

    def run_products(
        self,
        run_id: str,
        *,
        priced_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ObservationRecord], int]:
        where = "o.run_id = ?"
        params: list[Any] = [run_id]
        if priced_only:
            where += f" AND {self._PRICED_WHERE}"

        total = int(
            self.conn.execute(
                f"""SELECT COUNT(*) FROM observations o
                      JOIN products p ON p.id = o.product_id
                     WHERE {where}""",
                params,
            ).fetchone()[0]
        )
        rows = self.conn.execute(
            f"""{_OBSERVATION_SELECT}
                 WHERE {where}
              ORDER BY (o.price_fen IS NULL), o.price_fen, o.id
                 LIMIT ? OFFSET ?""",
            (*params, limit, offset),
        ).fetchall()
        return [_observation_record(row) for row in rows], total

    def stats_items(self, run_id: str) -> list[StatsItem]:
        """本轮去重后的统计输入。只取当前 run_id，绝不混入历史数据。"""
        rows = self.conn.execute(
            f"""{_OBSERVATION_SELECT}
                 WHERE o.run_id = ?
              ORDER BY (o.price_fen IS NULL), o.price_fen, o.id""",
            (run_id,),
        ).fetchall()
        return [
            StatsItem(
                product_id=row["product_id"],
                title=row["title"] or "",
                canonical_url=row["canonical_url"],
                price_fen=row["price_fen"],
                price_status=row["price_parse_status"],
                is_auction=bool(row["is_auction"]),
                is_ad=bool(row["is_ad"]),
            )
            for row in rows
        ]

    def raw_count_for_run(self, run_id: str) -> int:
        row = self.conn.execute(
            "SELECT raw_count FROM search_runs WHERE id=?", (run_id,)
        ).fetchone()
        return int(row["raw_count"]) if row else 0
