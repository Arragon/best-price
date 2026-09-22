"""搜索编排：任务生命周期、串行锁、节流、标准化入库。

指南 §8.1：MVP 用应用内异步任务 + 单实例串行锁，但**所有任务状态必须落库**，
进程崩溃后恢复为 failed + RUN_INTERRUPTED，不可永远停在 running。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal

from xps.adapters.base import AUTH_UNKNOWN, CrawlResult
from xps.adapters.xianyu import ADAPTER_VERSION
from xps.errors import QUEUE_FULL, RUN_INTERRUPTED, ServiceError
from xps.services.normalize import normalize_listing
from xps.services.search_scheduler import Pace, SearchScheduler
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
    pace: Pace = "balanced"
    cache_policy: Literal["prefer_fresh", "force_refresh"] = "prefer_fresh"
    idempotency_key: str | None = None

    def filters_dict(self) -> dict[str, Any]:
        return {
            "sort": self.sort,
            "min_price_yuan": str(self.min_price) if self.min_price is not None else None,
            "max_price_yuan": str(self.max_price) if self.max_price is not None else None,
            "city": self.city,
            "pace": self.pace,
        }

    def fingerprint(self, auth_mode: str) -> str:
        # Pace/cache/idempotency affect execution, not the resulting dataset.
        payload = {
            "platform": "xianyu",
            "keyword": " ".join(self.keyword.casefold().split()),
            "sort": self.sort,
            "min_price_yuan": str(self.min_price) if self.min_price is not None else None,
            "max_price_yuan": str(self.max_price) if self.max_price is not None else None,
            "city": self.city.strip().casefold() if self.city else None,
            "max_pages": self.max_pages,
            "adapter_version": ADAPTER_VERSION,
            "auth_mode": auth_mode,
            "schema_version": 4,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class SearchSubmission:
    run_id: str
    status: str
    reused: bool = False
    cache_hit: bool = False


class SearchService:
    def __init__(self, repo: Repository, adapter: Any, settings: Settings) -> None:
        self.repo = repo
        self.adapter = adapter
        self.settings = settings
        self._lock = asyncio.Lock()
        self._submission_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self.scheduler = SearchScheduler(repo, settings)

    # -- 提交 --------------------------------------------------------------

    async def submit(self, request: SearchRequest) -> SearchSubmission:
        async with self._submission_lock:
            try:
                auth_mode = (await self.adapter.auth_status()).state
            except Exception:  # local snapshot failure must not pretend another auth mode
                auth_mode = AUTH_UNKNOWN
            fingerprint = request.fingerprint(auth_mode)

            active = self.repo.find_active_run(fingerprint)
            if active is not None:
                return SearchSubmission(active.run_id, active.status, reused=True)

            if request.cache_policy == "prefer_fresh" and self.settings.search_cache_ttl_seconds:
                not_before = datetime.now(timezone.utc) - timedelta(
                    seconds=self.settings.search_cache_ttl_seconds
                )
                cached = self.repo.find_reusable_run(
                    fingerprint, not_before.isoformat().replace("+00:00", "Z")
                )
                if cached is not None:
                    return SearchSubmission(
                        cached.run_id, cached.status, reused=True, cache_hit=True
                    )

            if max(len(self._tasks), self.repo.count_active_runs()) >= self.settings.max_pending_jobs:
                raise ServiceError(
                    QUEUE_FULL,
                    f"本地搜索队列已满（上限 {self.settings.max_pending_jobs}）；未向平台发请求",
                )

            run_id = str(uuid.uuid4())
            self.repo.create_run(
                run_id=run_id,
                keyword=request.keyword,
                filters=request.filters_dict(),
                pages_requested=request.max_pages,
                adapter_version=ADAPTER_VERSION,
                source_commit=self.settings.resolved_source_commit(),
                request_fingerprint=fingerprint,
            )
            task = asyncio.create_task(self._execute(run_id, request), name=f"search-{run_id}")
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            return SearchSubmission(run_id, "pending")

    def seconds_until_next_allowed(self) -> float:
        """距下一次允许发起采集还需等待的秒数（§9 低频节流）。"""
        return self.scheduler.seconds_until_allowed("balanced")

    async def shutdown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    # -- 执行 --------------------------------------------------------------

    async def _execute(self, run_id: str, request: SearchRequest) -> None:
        async with self._lock:
            try:
                wait = self.scheduler.seconds_until_allowed(request.pace)
                if wait > 0:
                    logger.info("run %s 节流等待 %.1fs", run_id, wait)
                await self.scheduler.wait_and_record_attempt(request.pace)
                self.repo.mark_running(run_id)
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

            self.scheduler.record_result(result)
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
                if outcome.is_error and outcome.error_code:
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

        failed_pages = [page for page in result.pages if page.is_error]
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
                exhausted=result.exhausted,
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
            exhausted=result.exhausted,
        )

    # -- 启动恢复 ----------------------------------------------------------

    def recover_interrupted_runs(self) -> int:
        """进程重启后，把上一轮遗留的 pending/running 判定为中断。"""
        recovered = self.repo.recover_interrupted_runs()
        if recovered:
            logger.warning("已将 %d 个遗留任务判定为 %s", recovered, RUN_INTERRUPTED)
        return recovered
