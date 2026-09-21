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
    eligible_count: int
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
    observation_id: int
    product_id: int
    run_id: str
    observed_at: str
    page_number: int | None
    title: str | None
    title_raw: str | None
    price_raw: str | None
    price_fen: int | None
    currency: str | None
    price_parse_status: str
    area: str | None
    seller_display_name: str | None
    image_url: str | None
    published_at: str | None
    canonical_url: str | None
    flags: tuple[str, ...]
    item_kind: str | None
    excluded: bool
    exclusion_reasons: tuple[str, ...]
    needs_review: bool


@dataclass(frozen=True)
class StoreSummary:
    stored: int = 0
    deduped: int = 0
    skipped_unidentifiable: int = 0


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
        eligible_count=row["eligible_count"],
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
           o.price_raw     AS price_raw,
           o.price_fen     AS price_fen,
           o.currency      AS currency,
           o.price_parse_status AS price_parse_status,
           o.area          AS area,
           o.seller_display_name AS seller_display_name,
           o.image_url     AS image_url,
           o.published_at  AS published_at,
           p.canonical_url AS canonical_url,
           o.flags_json    AS flags_json,
           o.item_kind     AS item_kind,
           o.excluded      AS excluded,
           o.exclusion_reasons_json AS exclusion_reasons_json,
           o.needs_review  AS needs_review
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
        price_raw=row["price_raw"],
        price_fen=row["price_fen"],
        currency=row["currency"],
        price_parse_status=row["price_parse_status"],
        area=row["area"],
        seller_display_name=row["seller_display_name"],
        image_url=row["image_url"],
        published_at=row["published_at"],
        canonical_url=row["canonical_url"],
        flags=_loads_tuple(row["flags_json"]),
        item_kind=row["item_kind"],
        excluded=bool(row["excluded"]),
        exclusion_reasons=_loads_tuple(row["exclusion_reasons_json"]),
        needs_review=bool(row["needs_review"]),
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
        eligible_count: int = 0,
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
                          raw_count=?, stored_count=?, eligible_count=?,
                          error_code=?, error_message=?, warnings_json=?
                    WHERE id=?""",
                (
                    status,
                    ended_at or utcnow_iso(),
                    auth_mode,
                    pages_fetched,
                    raw_count,
                    stored_count,
                    eligible_count,
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
                    entry.title,
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
                    entry.title,
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
        stored = deduped = skipped = 0
        with self.conn:
            for entry in listings:
                if not entry.storable:
                    skipped += 1
                    continue

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

                classification = entry.classification
                cursor = self.conn.execute(
                    """INSERT INTO observations
                           (product_id, run_id, observed_at, page_number, title_raw, price_raw,
                            price_fen, currency, price_parse_status, area, seller_display_name,
                            image_url, published_at, raw_payload_json, flags_json, item_kind,
                            excluded, exclusion_reasons_json, needs_review)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        product_id,
                        run_id,
                        observed_at,
                        page_number,
                        entry.title,
                        entry.price_raw,
                        entry.price_fen,
                        entry.currency,
                        entry.price_status,
                        entry.area,
                        entry.seller_display_name,
                        entry.image_url,
                        entry.published_at,
                        json.dumps(entry.raw_payload, ensure_ascii=False)
                        if entry.raw_payload is not None
                        else None,
                        json.dumps(list(classification.flags), ensure_ascii=False),
                        classification.item_kind,
                        int(classification.excluded),
                        json.dumps(list(classification.exclusion_reasons), ensure_ascii=False),
                        int(classification.needs_review),
                    ),
                )
                self.conn.execute(
                    "UPDATE products SET latest_observation_id=? WHERE id=?",
                    (cursor.lastrowid, product_id),
                )
                stored += 1
        return StoreSummary(stored=stored, deduped=deduped, skipped_unidentifiable=skipped)

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

    _ELIGIBLE_WHERE = (
        "o.excluded = 0 AND o.needs_review = 0 AND o.price_parse_status = 'valid'"
        " AND o.price_fen IS NOT NULL"
    )

    def run_products(
        self,
        run_id: str,
        *,
        eligible_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ObservationRecord], int]:
        where = "o.run_id = ?"
        params: list[Any] = [run_id]
        if eligible_only:
            where += f" AND {self._ELIGIBLE_WHERE}"

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
                flags=_loads_tuple(row["flags_json"]),
                item_kind=row["item_kind"],
                excluded=bool(row["excluded"]),
                exclusion_reasons=_loads_tuple(row["exclusion_reasons_json"]),
                needs_review=bool(row["needs_review"]),
            )
            for row in rows
        ]

    def raw_count_for_run(self, run_id: str) -> int:
        row = self.conn.execute(
            "SELECT raw_count FROM search_runs WHERE id=?", (run_id,)
        ).fetchone()
        return int(row["raw_count"]) if row else 0
