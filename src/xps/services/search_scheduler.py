"""Persistent single-worker pacing and restriction state.

Pace is a caller preference, never permission to cross the configured platform
floor or a persisted cooldown/human-action stop.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Literal

from xps.adapters.base import CrawlResult
from xps.errors import HALT_CODES, RATE_LIMITED, ServiceError
from xps.settings import Settings
from xps.storage.repository import Repository, utcnow_iso

Pace = Literal["economy", "balanced", "fast"]

_LAST_ATTEMPT = "last_platform_attempt_at"
_RESTRICTION_CODE = "restriction_code"
_RESTRICTION_MESSAGE = "restriction_message"
_COOLDOWN_UNTIL = "cooldown_until"


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.astimezone(UTC)


class SearchScheduler:
    def __init__(self, repo: Repository, settings: Settings) -> None:
        self.repo = repo
        self.settings = settings

    def effective_gap(self, pace: Pace) -> float:
        requested = {
            "economy": self.settings.economy_gap_seconds,
            "balanced": self.settings.balanced_gap_seconds,
            "fast": self.settings.fast_gap_seconds,
        }[pace]
        floor = self.settings.platform_floor_seconds
        if not self.settings.allow_faster_pace:
            floor = max(floor, self.settings.min_seconds_between_searches)
        return max(floor, requested)

    def _check_restriction(self) -> None:
        code = self.repo.get_scheduler_state(_RESTRICTION_CODE)
        if not code:
            return
        message = self.repo.get_scheduler_state(_RESTRICTION_MESSAGE) or "平台访问已暂停"
        if code == RATE_LIMITED:
            cooldown = _parse_utc(self.repo.get_scheduler_state(_COOLDOWN_UNTIL))
            if cooldown is not None and cooldown <= datetime.now(UTC):
                self.clear_restriction()
                return
        raise ServiceError(code, message)

    def seconds_until_allowed(self, pace: Pace) -> float:
        self._check_restriction()
        last_attempt = _parse_utc(self.repo.get_scheduler_state(_LAST_ATTEMPT))
        if last_attempt is None:
            return 0.0
        elapsed = (datetime.now(UTC) - last_attempt).total_seconds()
        return max(0.0, self.effective_gap(pace) - elapsed)

    async def wait_and_record_attempt(self, pace: Pace) -> None:
        wait = self.seconds_until_allowed(pace)
        if wait > 0:
            await asyncio.sleep(wait)
        # Persist before the real call so a crash/restart cannot erase pacing.
        self.repo.set_scheduler_state(_LAST_ATTEMPT, utcnow_iso())

    def record_result(self, result: CrawlResult) -> None:
        errors = [page for page in result.pages if page.is_error and page.error_code]
        halt = next((page for page in errors if page.error_code in HALT_CODES), None)
        if halt is None:
            return
        assert halt.error_code is not None
        self.repo.set_scheduler_state(_RESTRICTION_CODE, halt.error_code)
        self.repo.set_scheduler_state(
            _RESTRICTION_MESSAGE, halt.error_message or halt.error_code
        )
        if halt.error_code == RATE_LIMITED:
            until = datetime.now(UTC) + timedelta(
                seconds=self.settings.rate_limit_cooldown_seconds
            )
            self.repo.set_scheduler_state(
                _COOLDOWN_UNTIL, until.isoformat().replace("+00:00", "Z")
            )

    def clear_restriction(self) -> None:
        self.repo.delete_scheduler_state(
            _RESTRICTION_CODE, _RESTRICTION_MESSAGE, _COOLDOWN_UNTIL
        )

    def acknowledge_human_action(self) -> None:
        """Clear auth/challenge stops after an explicit user-driven reload.

        Rate-limit cooldown deliberately survives reload; changing credentials or
        reloading state must not be a way to bypass a platform restriction.
        """
        code = self.repo.get_scheduler_state(_RESTRICTION_CODE)
        if code and code != RATE_LIMITED:
            self.clear_restriction()
