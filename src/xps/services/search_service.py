"""搜索编排：任务生命周期、串行锁、节流、标准化入库。

指南 §8.1：MVP 用应用内异步任务 + 单实例串行锁，但**所有任务状态必须落库**，
进程崩溃后恢复为 failed + RUN_INTERRUPTED，不可永远停在 running。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from xps.adapters.base import CrawlResult
from xps.adapters.xianyu import ADAPTER_VERSION
from xps.errors import RUN_INTERRUPTED, ServiceError
from xps.services.normalize import normalize_listing
from xps.services.statistics import compute_stats
from xps.settings import Settings
from xps.storage.repository import Repository

logger = logging.getLogger(__name__)

SORT_OPTIONS = ("newest", "price_asc", "price_desc", "default")


@dataclass(frozen=True)
class SearchRequest:
    keyword: str
    max_pages: int = 1
    sort: str = "newest"
    min_price: Decimal | None = None
    max_price: Decimal | None = None
    city: str | None = None

    def filters_dict(self) -> dict[str, Any]:
        return {
            "sort": self.sort,
            "min_price_yuan": str(self.min_price) if self.min_price is not None else None,
            "max_price_yuan": str(self.max_price) if self.max_price is not None else None,
            "city": self.city,
        }


class SearchService:
    def __init__(self, repo: Repository, adapter: Any, settings: Settings) -> None:
        self.repo = repo
        self.adapter = adapter
        self.settings = settings
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self._last_crawl_started: datetime | None = None

    # -- 提交 --------------------------------------------------------------

    def submit(self, request: SearchRequest) -> str:
        run_id = str(uuid.uuid4())
        self.repo.create_run(
            run_id=run_id,
            keyword=request.keyword,
            filters=request.filters_dict(),
            pages_requested=request.max_pages,
            adapter_version=ADAPTER_VERSION,
            source_commit=self.settings.resolved_source_commit(),
        )
        task = asyncio.create_task(self._execute(run_id, request), name=f"search-{run_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return run_id

    def seconds_until_next_allowed(self) -> float:
        """距下一次允许发起采集还需等待的秒数（§9 低频节流）。"""
        interval = self.settings.min_seconds_between_searches
        if interval <= 0 or self._last_crawl_started is None:
            return 0.0
        elapsed = (datetime.now(timezone.utc) - self._last_crawl_started).total_seconds()
        return max(0.0, interval - elapsed)

    async def shutdown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    # -- 执行 --------------------------------------------------------------

    async def _execute(self, run_id: str, request: SearchRequest) -> None:
        async with self._lock:
            wait = self.seconds_until_next_allowed()
            if wait > 0:
                logger.info("run %s 节流等待 %.1fs", run_id, wait)
                await asyncio.sleep(wait)
            self._last_crawl_started = datetime.now(timezone.utc)
            self.repo.mark_running(run_id)

            try:
                result = await self.adapter.search(
                    request.keyword,
                    request.max_pages,
                    request.sort,
                    request.min_price,
                    request.max_price,
                    request.city,
                )
            except ServiceError as exc:
                self._fail(run_id, exc.code, exc.message, None, 0, 0)
                return
            except Exception as exc:  # noqa: BLE001  任何未预期异常都要落库，不能让任务悬在 running
                logger.exception("run %s 采集异常", run_id)
                self._fail(
                    run_id,
                    "UPSTREAM_UNAVAILABLE",
                    f"{type(exc).__name__}: {exc}"[:500],
                    None,
                    request.max_pages,
                    0,
                )
                return

            self._persist(run_id, result)

    def _fail(
        self,
        run_id: str,
        code: str,
        message: str,
        auth_mode: str | None,
        pages_requested: int,
        pages_fetched: int,
    ) -> None:
        self.repo.finish_run(
            run_id,
            status="failed",
            pages_fetched=pages_fetched,
            auth_mode=auth_mode,
            error_code=code,
            error_message=message,
        )

    def _persist(self, run_id: str, result: CrawlResult) -> None:
        warnings = list(result.warnings)
        raw_count = 0
        stored_count = 0
        skipped = 0
        untrusted = 0

        for outcome in result.pages:
            if not outcome.fetched:
                if outcome.error_code:
                    warnings.append(f"page_{outcome.page}:{outcome.error_code}")
                continue
            observed_at = datetime.now(timezone.utc)
            raw_count += len(outcome.listings)
            normalized = [
                normalize_listing(raw, observed_at=observed_at) for raw in outcome.listings
            ]
            summary = self.repo.store_listings(
                run_id, normalized, page_number=outcome.page
            )
            stored_count += summary.stored
            skipped += summary.skipped_unidentifiable
            untrusted += summary.untrusted_identity

        if skipped:
            warnings.append(f"skipped_unidentifiable:{skipped}")
        if untrusted:
            # 这些条目已入库，但主机不在实测确认的白名单里，canonical_url 为 None
            warnings.append(f"untrusted_identity:{untrusted}")

        failed_pages = [page for page in result.pages if not page.fetched]
        pages_fetched = result.pages_fetched

        if pages_fetched == 0:
            first = failed_pages[0] if failed_pages else None
            self.repo.finish_run(
                run_id,
                status="failed",
                pages_fetched=0,
                raw_count=0,
                stored_count=0,
                priced_count=0,
                auth_mode=result.auth_mode,
                error_code=(first.error_code if first else None) or "UPSTREAM_UNAVAILABLE",
                error_message=(first.error_message if first else None)
                or "没有任何一页抓取成功",
                warnings=warnings,
            )
            return

        status = "partial" if failed_pages else "succeeded"
        first_error = failed_pages[0] if failed_pages else None

        stats = compute_stats(
            run_id=run_id,
            items=self.repo.stats_items(run_id),
            raw_count=raw_count,
            min_sample=self.settings.min_sample_threshold,
            pages_requested=result.pages_requested,
            pages_fetched=pages_fetched,
            auth_mode=result.auth_mode,
            status=status,
        )

        self.repo.finish_run(
            run_id,
            status=status,
            pages_fetched=pages_fetched,
            raw_count=raw_count,
            stored_count=stored_count,
            priced_count=stats.priced_count,
            auth_mode=result.auth_mode,
            error_code=first_error.error_code if first_error else None,
            error_message=first_error.error_message if first_error else None,
            warnings=warnings,
        )

    # -- 启动恢复 ----------------------------------------------------------

    def recover_interrupted_runs(self) -> int:
        """进程重启后，把上一轮遗留的 pending/running 判定为中断。"""
        recovered = self.repo.recover_interrupted_runs()
        if recovered:
            logger.warning("已将 %d 个遗留任务判定为 %s", recovered, RUN_INTERRUPTED)
        return recovered
