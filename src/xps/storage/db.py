"""SQLite 连接、幂等迁移与在线备份。

备份用 `VACUUM INTO`（SQLite ≥ 3.27），不可用时回落到 backup API。
两者都不会在 WAL 模式下产出「只拷主库文件」那样的不完整快照。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
SCHEMA_VERSION = 1

_BUSY_TIMEOUT_MS = 5000


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


def migrate(conn: sqlite3.Connection) -> int:
    """幂等应用 schema。返回应用后的 schema 版本。"""
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
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
