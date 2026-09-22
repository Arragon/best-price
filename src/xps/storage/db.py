"""SQLite 连接、幂等迁移与在线备份。

迁移是**加法**的：schema.sql 用 CREATE TABLE IF NOT EXISTS，对已存在的库不会改表结构，
所以升级靠 `ALTER TABLE ADD COLUMN` 补列。老库里被废弃的筛选列不删——
SQLite 删列要重写整表，为几列死数据冒这个险不值当，代码不读写它们即可。

备份用 `VACUUM INTO`（SQLite ≥ 3.27），不可用时回落到 backup API。
两者都不会在 WAL 模式下产出「只拷主库文件」那样的不完整快照。
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
SCHEMA_VERSION = 4

_BUSY_TIMEOUT_MS = 5000

# (列名, 列定义)。仅列 v2 新增的字段；已存在则跳过。
_OBSERVATION_COLUMNS: tuple[tuple[str, str], ...] = (
    ("description", "TEXT"),
    ("original_price_text", "TEXT"),
    ("coupon_text", "TEXT"),
    ("seller_credit", "TEXT"),
    ("seller_review_count", "INTEGER"),
    ("seller_positive_rate", "TEXT"),
    ("seller_identity", "TEXT"),
    ("seller_avatar_url", "TEXT"),
    ("has_video", "INTEGER NOT NULL DEFAULT 0"),
    ("published_text", "TEXT"),
    ("want_count", "INTEGER"),
    ("free_shipping", "INTEGER NOT NULL DEFAULT 0"),
    ("labels_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("is_auction", "INTEGER NOT NULL DEFAULT 0"),
    ("is_ad", "INTEGER NOT NULL DEFAULT 0"),
)


def _sqlite_version() -> tuple[int, ...]:
    return tuple(int(part) for part in sqlite3.sqlite_version.split("."))


def connect(path: str | Path) -> sqlite3.Connection:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    return conn


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _migrate_additive(conn: sqlite3.Connection) -> list[str]:
    applied: list[str] = []

    present = _column_names(conn, "observations")
    for name, definition in _OBSERVATION_COLUMNS:
        if name in present:
            continue
        conn.execute(f"ALTER TABLE observations ADD COLUMN {name} {definition}")
        applied.append(f"observations.{name}")

    run_columns = _column_names(conn, "search_runs")
    if "priced_count" not in run_columns:
        # 语义没变（能参与算术的条目数），只是不再用「合格」这种带筛选意味的词
        if "eligible_count" in run_columns:
            conn.execute(
                "ALTER TABLE search_runs RENAME COLUMN eligible_count TO priced_count"
            )
            applied.append("search_runs.eligible_count→priced_count")
        else:
            conn.execute(
                "ALTER TABLE search_runs"
                " ADD COLUMN priced_count INTEGER NOT NULL DEFAULT 0"
            )
            applied.append("search_runs.priced_count")

    run_columns = _column_names(conn, "search_runs")
    if "request_fingerprint" not in run_columns:
        conn.execute("ALTER TABLE search_runs ADD COLUMN request_fingerprint TEXT")
        applied.append("search_runs.request_fingerprint")
    if "exhausted" not in run_columns:
        conn.execute(
            "ALTER TABLE search_runs ADD COLUMN exhausted INTEGER NOT NULL DEFAULT 0"
        )
        applied.append("search_runs.exhausted")

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_runs_fingerprint "
        "ON search_runs(request_fingerprint, started_at DESC)"
    )

    return applied


def migrate(conn: sqlite3.Connection) -> int:
    """幂等应用 schema。返回应用后的 schema 版本。"""
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    applied = _migrate_additive(conn)
    if applied:
        logger.info("schema 迁移补了 %d 项：%s", len(applied), ", ".join(applied))
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
    return SCHEMA_VERSION


def integrity_check(conn: sqlite3.Connection) -> str:
    return str(conn.execute("PRAGMA integrity_check").fetchone()[0])


def backup(conn: sqlite3.Connection, dest: str | Path) -> Path:
    """在线备份到 dest。拒绝覆盖已存在文件，避免悄悄毁掉上一份可用快照。"""
    target = Path(dest)
    if target.exists():
        raise FileExistsError(f"备份目标已存在，拒绝覆盖：{target}")
    target.parent.mkdir(parents=True, exist_ok=True)

    conn.commit()
    if _sqlite_version() >= (3, 27):
        conn.execute("VACUUM INTO ?", (str(target),))
        return target

    mirror = sqlite3.connect(target)
    try:
        with mirror:
            conn.backup(mirror)
    finally:
        mirror.close()
    return target
