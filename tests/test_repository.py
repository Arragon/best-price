"""存储层测试。

对应指南 §Phase2 验收与 §11 测试矩阵：
- 相同商品被两轮搜索发现时 products 只有一个实体、observations 能记录两次
- 同轮重复不重复计数
- 价格原文始终可还原
- 错误价格不进入数字统计
- 备份后恢复至临时数据库 integrity_check 通过、业务行数匹配
- 进程重启后遗留 running 任务可识别为中断

所有商品数据均由 tests/fixtures/mtop_entry.py 合成，非真实报价。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from xps.adapters.xianyu import entry_to_raw_listing
from xps.services.classify import build_model_spec
from xps.services.normalize import normalize_listing
from xps.storage.db import backup, connect, integrity_check, migrate
from xps.storage.repository import Repository
from tests.fixtures.mtop_entry import make_entry

XT4 = build_model_spec("富士 X-T4")
T1 = datetime(2026, 9, 22, 3, 0, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 9, 22, 4, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path / "price.sqlite3")
    migrate(connection)
    yield connection
    connection.close()


@pytest.fixture
def repo(conn) -> Repository:
    return Repository(conn)


def listing(
    item_id: str,
    *,
    title: str = "富士 X-T4 单机身",
    price: tuple[str, ...] = ("5499",),
    observed_at: datetime = T1,
):
    parts = [("sign", "¥")] + [("integer", text) for text in price]
    raw = entry_to_raw_listing(
        make_entry(
            item_id=item_id,
            title=title,
            price=parts,
            target_url=f"fleamarket://item?id={item_id}",
        )
    )
    return normalize_listing(raw, spec=XT4, observed_at=observed_at)


def new_run(repo: Repository, run_id: str, **kwargs) -> None:
    kwargs.setdefault("keyword", "富士 X-T4")
    kwargs.setdefault("filters", {"sort": "newest"})
    kwargs.setdefault("pages_requested", 1)
    repo.create_run(run_id=run_id, **kwargs)


# ---------------------------------------------------------------- schema 与迁移


def test_migrate_creates_the_three_tables_and_view(conn) -> None:
    names = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
        )
    }

    assert {"products", "observations", "search_runs", "run_items"} <= names


def test_migrate_is_idempotent(conn) -> None:
    migrate(conn)
    migrate(conn)

    assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0


def test_migration_preserves_existing_rows(conn, repo) -> None:
    """§6：迁移不得依靠删除 SQLite 文件解决结构升级。"""
    new_run(repo, "r1")
    repo.store_listings("r1", [listing("7001")], page_number=1)

    migrate(conn)

    assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 1


def test_wal_journal_mode_is_enabled(conn) -> None:
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_foreign_keys_are_enforced(conn) -> None:
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO observations (product_id, run_id, observed_at, price_parse_status)"
            " VALUES (9999, 'nope', '2026-09-22T00:00:00Z', 'valid')"
        )


def test_money_column_is_integer_not_real(conn) -> None:
    """§6：不使用 SQLite REAL 做交易金额基础类型。"""
    columns = {row[1]: row[2].upper() for row in conn.execute("PRAGMA table_info(observations)")}

    assert columns["price_fen"] == "INTEGER"


# ---------------------------------------------------------------- 写入与去重


def test_store_creates_product_and_observation(repo) -> None:
    new_run(repo, "r1")

    summary = repo.store_listings("r1", [listing("7001")], page_number=1)

    assert summary.stored == 1
    row = repo.get_product_by_identity("xianyu:item:7001")
    assert row is not None
    assert row.platform_item_id == "7001"
    assert row.canonical_url == "https://www.goofish.com/item?id=7001"


def test_same_product_in_two_runs_is_one_entity_two_observations(repo) -> None:
    """§11：两次搜索返回同一商品 → 一个 products，两个不同 run 的观察记录。"""
    new_run(repo, "r1")
    new_run(repo, "r2")
    repo.store_listings("r1", [listing("7001", observed_at=T1)], page_number=1)
    repo.store_listings("r2", [listing("7001", price=("5200",), observed_at=T2)], page_number=1)

    assert repo.count_products() == 1
    assert repo.count_observations_for_product("xianyu:item:7001") == 2
    assert {row.run_id for row in repo.observations_of("xianyu:item:7001")} == {"r1", "r2"}


def test_duplicate_within_one_run_is_deduped_first_wins(repo) -> None:
    """§6：同一轮重复分页去重，以首次观察为准。"""
    new_run(repo, "r1")
    repo.store_listings("r1", [listing("7001", price=("5499",))], page_number=1)

    summary = repo.store_listings("r1", [listing("7001", price=("6000",))], page_number=2)

    assert summary.deduped == 1
    assert repo.count_observations_for_run("r1") == 1
    kept = repo.observations_of("xianyu:item:7001")[0]
    assert kept.price_fen == 549_900
    assert kept.page_number == 1


def test_distinct_products_in_one_run_are_all_kept(repo) -> None:
    new_run(repo, "r1")

    repo.store_listings("r1", [listing("7001"), listing("7002"), listing("7003")], page_number=1)

    assert repo.count_products() == 3
    assert repo.count_observations_for_run("r1") == 3


def test_different_items_with_identical_title_and_price_stay_separate(repo) -> None:
    """§11：不同商品有相同标题和价格 → 不得合并成同一 ID。"""
    new_run(repo, "r1")

    repo.store_listings(
        "r1",
        [listing("7001", title="富士 X-T4 单机身"), listing("7002", title="富士 X-T4 单机身")],
        page_number=1,
    )

    assert repo.count_products() == 2


def test_last_seen_at_advances_on_second_run(repo) -> None:
    new_run(repo, "r1")
    new_run(repo, "r2")
    repo.store_listings("r1", [listing("7001", observed_at=T1)], page_number=1)
    first = repo.get_product_by_identity("xianyu:item:7001")

    repo.store_listings("r2", [listing("7001", observed_at=T2)], page_number=1)
    second = repo.get_product_by_identity("xianyu:item:7001")

    assert first.first_seen_at == second.first_seen_at
    assert second.last_seen_at > first.last_seen_at


def test_title_latest_follows_the_newest_observation(repo) -> None:
    new_run(repo, "r1")
    new_run(repo, "r2")
    repo.store_listings("r1", [listing("7001", title="旧标题 单机身", observed_at=T1)], page_number=1)
    repo.store_listings("r2", [listing("7001", title="新标题 单机身", observed_at=T2)], page_number=1)

    assert repo.get_product_by_identity("xianyu:item:7001").title_latest == "新标题 单机身"


# ---------------------------------------------------------------- 价格可还原性


def test_price_raw_is_preserved_verbatim(repo) -> None:
    new_run(repo, "r1")
    repo.store_listings("r1", [listing("7001", price=("5499",))], page_number=1)

    assert repo.observations_of("xianyu:item:7001")[0].price_raw == "¥5499"


def test_ambiguous_price_stores_null_not_zero(repo) -> None:
    """§7：错误价格不进入数字统计，且不得转成 0。"""
    new_run(repo, "r1")
    raw = entry_to_raw_listing(
        make_entry(
            item_id="7002",
            title="富士 X-T4 定金链接",
            price=(("integer", "定金200"),),
            target_url="fleamarket://item?id=7002",
        )
    )
    repo.store_listings(
        "r1", [normalize_listing(raw, spec=XT4, observed_at=T1)], page_number=1
    )

    stored = repo.observations_of("xianyu:item:7002")[0]
    assert stored.price_fen is None
    assert stored.price_parse_status == "ambiguous"
    assert stored.price_raw == "定金200"


def test_missing_title_is_stored_as_null_not_placeholder(repo) -> None:
    """§0.4：绝不插入「暂无」之类的占位伪数据。"""
    new_run(repo, "r1")
    raw = entry_to_raw_listing(
        make_entry(item_id="7003", title=None, target_url="fleamarket://item?id=7003")
    )
    repo.store_listings("r1", [normalize_listing(raw, spec=XT4, observed_at=T1)], page_number=1)

    stored = repo.observations_of("xianyu:item:7003")[0]
    assert stored.title_raw is None
    assert stored.area == "上海", "fixture 里 area 有值，不应被误清"


def test_unstorable_listing_is_skipped_and_counted(repo) -> None:
    new_run(repo, "r1")
    broken = entry_to_raw_listing(make_entry(item_id=None, target_url=None, title=None))
    normalized = normalize_listing(broken, spec=XT4, observed_at=T1)

    summary = repo.store_listings("r1", [normalized], page_number=1)

    assert normalized.storable is False
    assert summary.skipped_unidentifiable == 1
    assert summary.stored == 0
    assert repo.count_products() == 0


def test_untrusted_host_listing_is_stored_but_excluded(repo) -> None:
    """身份不可信时保留记录以便追溯，但打标排除，不进可信统计。"""
    from xps.adapters.base import RawListing

    new_run(repo, "r1")
    raw = RawListing(
        source_id=None,
        url="https://evil.example.com/item?id=1",
        title="富士 X-T4 单机身",
        price_text="¥5499",
    )
    repo.store_listings("r1", [normalize_listing(raw, spec=XT4, observed_at=T1)], page_number=1)

    items = repo.stats_items("r1")
    assert len(items) == 1
    assert items[0].excluded is True


# ---------------------------------------------------------------- run 生命周期


def test_recover_interrupted_runs_marks_stale_running_as_failed(tmp_path) -> None:
    """§8.1 + §11：进程崩溃后不得永远停在 running。"""
    path = tmp_path / "price.sqlite3"
    first = connect(path)
    migrate(first)
    repo = Repository(first)
    new_run(repo, "r-crash")
    repo.mark_running("r-crash")
    new_run(repo, "r-pending")
    first.close()

    # 模拟进程重启：全新连接
    second = connect(path)
    recovered = Repository(second).recover_interrupted_runs()

    assert recovered == 2
    crashed = Repository(second).get_run("r-crash")
    assert crashed.status == "failed"
    assert crashed.error_code == "RUN_INTERRUPTED"
    assert Repository(second).get_run("r-pending").status == "failed"
    second.close()


def test_recover_leaves_finished_runs_alone(repo) -> None:
    new_run(repo, "r1")
    repo.finish_run("r1", status="succeeded", pages_fetched=1, auth_mode="guest")

    assert repo.recover_interrupted_runs() == 0
    assert repo.get_run("r1").status == "succeeded"


def test_finish_run_records_layered_counts(repo) -> None:
    new_run(repo, "r1", pages_requested=2)
    repo.mark_running("r1")

    repo.finish_run(
        "r1",
        status="partial",
        pages_fetched=1,
        raw_count=30,
        stored_count=28,
        eligible_count=9,
        auth_mode="guest",
        warnings=("page_2_failed:UPSTREAM_TIMEOUT",),
        error_code="UPSTREAM_TIMEOUT",
        error_message="第 2 页超时",
    )

    run = repo.get_run("r1")
    assert run.status == "partial"
    assert run.pages_requested == 2
    assert run.pages_fetched == 1
    assert (run.raw_count, run.stored_count, run.eligible_count) == (30, 28, 9)
    assert run.warnings == ("page_2_failed:UPSTREAM_TIMEOUT",)
    assert run.error_code == "UPSTREAM_TIMEOUT"
    assert run.ended_at is not None


def test_get_run_returns_none_for_unknown_id(repo) -> None:
    assert repo.get_run("nope") is None


# ---------------------------------------------------------------- 读取


def test_run_items_view_covers_every_product_seen_this_round(repo) -> None:
    """§6：老商品重新出现时仍属于本轮结果，run_items 必须包含它。"""
    new_run(repo, "r1")
    new_run(repo, "r2")
    repo.store_listings("r1", [listing("7001"), listing("7002")], page_number=1)
    # 第二轮全是「老商品」，上游此时会报 new_records=0
    repo.store_listings("r2", [listing("7001"), listing("7002")], page_number=1)

    rows = conn_rows(repo, "SELECT product_id FROM run_items WHERE run_id = 'r2'")

    assert len(rows) == 2


def conn_rows(repo: Repository, sql: str) -> list:
    return repo.conn.execute(sql).fetchall()


def test_run_products_paginates_and_reports_total(repo) -> None:
    new_run(repo, "r1")
    repo.store_listings(
        "r1", [listing(f"700{i}") for i in range(1, 6)], page_number=1
    )

    page, total = repo.run_products("r1", limit=2, offset=0)

    assert total == 5
    assert len(page) == 2
    second_page, _ = repo.run_products("r1", limit=2, offset=2)
    assert {row.product_id for row in page} & {row.product_id for row in second_page} == set()


def test_run_products_eligible_only_filters_excluded(repo) -> None:
    new_run(repo, "r1")
    repo.store_listings(
        "r1",
        [listing("7001"), listing("7002", title="X-T4 电池", price=("50",))],
        page_number=1,
    )

    page, total = repo.run_products("r1", eligible_only=True)

    assert total == 1
    assert page[0].title == "富士 X-T4 单机身"


def test_stats_items_expose_what_statistics_needs(repo) -> None:
    new_run(repo, "r1")
    repo.store_listings(
        "r1",
        [listing("7001", price=("4800",)), listing("7002", price=("5200",))],
        page_number=1,
    )

    items = repo.stats_items("r1")

    assert sorted(entry.price_fen for entry in items) == [480_000, 520_000]
    assert all(entry.canonical_url for entry in items)
    assert all(entry.product_id for entry in items)


def test_stats_items_do_not_leak_across_runs(repo) -> None:
    """§8.4：默认绝不跨日期/跨关键词混合历史数据。"""
    new_run(repo, "r1")
    new_run(repo, "r2")
    repo.store_listings("r1", [listing("7001")], page_number=1)
    repo.store_listings("r2", [listing("8001")], page_number=1)

    assert [entry.product_id for entry in repo.stats_items("r2")] != [
        entry.product_id for entry in repo.stats_items("r1")
    ]
    assert len(repo.stats_items("r2")) == 1


# ---------------------------------------------------------------- 备份


def test_backup_restores_to_a_working_database(tmp_path, repo) -> None:
    """§11：备份后恢复至临时数据库，integrity_check 通过、业务行数匹配。"""
    new_run(repo, "r1")
    repo.store_listings("r1", [listing("7001"), listing("7002")], page_number=1)
    repo.finish_run("r1", status="succeeded", pages_fetched=1, auth_mode="guest")

    dest = tmp_path / "backups" / "snapshot.sqlite3"
    backup(repo.conn, dest)

    assert dest.exists()
    restored = connect(dest)
    assert integrity_check(restored) == "ok"
    assert restored.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 2
    assert restored.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 2
    assert restored.execute("SELECT COUNT(*) FROM search_runs").fetchone()[0] == 1
    restored.close()


def test_backup_creates_parent_directory(tmp_path, repo) -> None:
    dest = tmp_path / "nested" / "deep" / "snapshot.sqlite3"

    backup(repo.conn, dest)

    assert dest.exists()


def test_backup_refuses_to_overwrite_an_existing_file(tmp_path, repo) -> None:
    dest = tmp_path / "snapshot.sqlite3"
    dest.write_text("existing")

    with pytest.raises(FileExistsError):
        backup(repo.conn, dest)
