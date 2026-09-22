"""Offline regression tests for Phase 1 search scheduling semantics."""

from __future__ import annotations

from tests.fake_adapter import FakeAdapter, body_listings
from tests.helpers import build_client, submit, wait_for_run


def test_normal_end_of_pagination_is_succeeded_not_partial(tmp_path) -> None:
    adapter = FakeAdapter(
        pages=[body_listings(7001, 2)],
        exhaust_after_page=1,
    )
    with build_client(tmp_path, adapter) as client:
        response = submit(client, {"keyword": "富士 X-T4", "max_pages": 2})
        run = wait_for_run(client, response.json()["run_id"])

    assert run["status"] == "succeeded"
    assert run["pages_fetched"] == 1
    assert run["exhausted"] is True
    assert run["error"] is None


def test_identical_in_flight_searches_share_one_run(tmp_path) -> None:
    adapter = FakeAdapter(pages=[body_listings(7001, 2)], delay=0.1)
    with build_client(tmp_path, adapter) as client:
        first = submit(client, {"keyword": "富士 X-T4"})
        second = submit(client, {"keyword": "  富士   x-t4  "})
        wait_for_run(client, first.json()["run_id"])

    assert first.json()["run_id"] == second.json()["run_id"]
    assert second.json()["reused"] is True
    assert second.json()["cache_hit"] is False
    assert len(adapter.calls) == 1


def test_recent_success_is_reused_but_force_refresh_is_not(tmp_path) -> None:
    adapter = FakeAdapter(pages=[body_listings(7001, 2)])
    with build_client(tmp_path, adapter, search_cache_ttl_seconds=600) as client:
        first = submit(client, {"keyword": "富士 X-T4"})
        wait_for_run(client, first.json()["run_id"])

        cached = submit(client, {"keyword": "富士 X-T4"})
        refreshed = submit(
            client,
            {"keyword": "富士 X-T4", "cache_policy": "force_refresh"},
        )
        wait_for_run(client, refreshed.json()["run_id"])

    assert cached.json()["run_id"] == first.json()["run_id"]
    assert cached.json()["cache_hit"] is True
    assert refreshed.json()["run_id"] != first.json()["run_id"]
    assert len(adapter.calls) == 2


def test_queue_limit_rejects_without_calling_platform(tmp_path) -> None:
    adapter = FakeAdapter(pages=[body_listings(7001, 2)], delay=0.2)
    with build_client(tmp_path, adapter, max_pending_jobs=1) as client:
        first = submit(client, {"keyword": "富士 X-T4"})
        rejected = submit(client, {"keyword": "9950X3D"})
        wait_for_run(client, first.json()["run_id"])

    assert rejected.status_code == 429
    assert rejected.json()["code"] == "QUEUE_FULL"
    assert len(adapter.calls) == 1


def test_fast_pace_requires_explicit_compatibility_opt_in(tmp_path) -> None:
    adapter = FakeAdapter(pages=[body_listings(7001, 1)])
    with build_client(
        tmp_path,
        adapter,
        allow_faster_pace=False,
        min_seconds_between_searches=30,
        platform_floor_seconds=10,
        balanced_gap_seconds=30,
        fast_gap_seconds=12,
    ) as client:
        scheduler = client.app.state.service.scheduler
        assert scheduler.effective_gap("fast") == 30

    with build_client(
        tmp_path / "opted-in",
        adapter,
        allow_faster_pace=True,
        min_seconds_between_searches=30,
        platform_floor_seconds=10,
        balanced_gap_seconds=30,
        fast_gap_seconds=12,
    ) as client:
        scheduler = client.app.state.service.scheduler
        assert scheduler.effective_gap("fast") == 12
        assert scheduler.effective_gap("balanced") == 30
