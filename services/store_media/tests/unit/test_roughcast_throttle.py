"""节流器与熔断（`docs/roughcast-quality-ranking.md` §三 / §八 G）。"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.infrastructure.roughcast_repository import RoughcastRepository
from app.infrastructure.roughcast_throttle import (
    BreakerOpen,
    BudgetExhausted,
    RoughcastThrottle,
    plan_budget,
)
from tests.roughcast_helpers import FakeClock, FakeMonotonic, RecordingSleeper, make_db

QUEUE = "A"
CHINA = timedelta(hours=8)


def build(tmp_path, *, daily_budget: int = 100, min_interval: float = 20.0,
          clock: FakeClock | None = None):
    resolved_clock = clock or FakeClock()
    database = make_db(tmp_path)
    repository = RoughcastRepository(database, clock=resolved_clock)
    monotonic = FakeMonotonic()
    sleeper = RecordingSleeper(monotonic)
    throttle = RoughcastThrottle(
        repository,
        daily_budget=daily_budget,
        min_interval_seconds=min_interval,
        timezone=CHINA,
        clock=resolved_clock,
        monotonic=monotonic,
        sleeper=sleeper,
    )
    return throttle, repository, database, sleeper, resolved_clock


# ------------------------------------------------------------------ 预算


def test_plan_budget_never_exceeds_hard_cap() -> None:
    plan = plan_budget(queue_a_pages=66, queue_b_pages=0, retry_reserve=10,
                       safety_factor=1.15, hard_cap=260)
    # 66 + 10 = 76;76 × 1.15 = 87.4 -> 88。浮点 ceil 抖动会算成 87,所以走整数空间。
    assert plan.daily_budget == 88

    capped = plan_budget(queue_a_pages=5000, queue_b_pages=0, retry_reserve=10,
                         safety_factor=1.15, hard_cap=260)
    assert capped.daily_budget == 260


def test_each_real_request_decrements_once_including_retries(tmp_path) -> None:
    """§八 G:一个小区 2 页 + 1 次重试,预算必须正好扣 3。"""
    throttle, repository, _db, _sleeper, _clock = build(tmp_path, daily_budget=10)

    first = throttle.acquire(queue=QUEUE, target="page=1")
    throttle.succeeded(first)
    retry = throttle.acquire(queue=QUEUE, target="page=2")
    throttle.failed(retry, http_status=500, note="upstream 500")
    second = throttle.acquire(queue=QUEUE, target="page=2")
    throttle.succeeded(second)

    assert throttle.spent_today() == 3
    assert throttle.remaining_today() == 7
    assert repository.count_requests_since(
        throttle._local_day_start_utc()  # noqa: SLF001 —— 就是要断言这条会计口径
    ) == 3


def test_budget_exhausted_blocks_further_requests(tmp_path) -> None:
    throttle, _repo, _db, _sleeper, _clock = build(tmp_path, daily_budget=2)

    throttle.succeeded(throttle.acquire(queue=QUEUE, target="page=1"))
    throttle.succeeded(throttle.acquire(queue=QUEUE, target="page=2"))

    with pytest.raises(BudgetExhausted) as excinfo:
        throttle.acquire(queue=QUEUE, target="page=3")
    assert excinfo.value.reason == "budget_exhausted"
    # 关键:被拒的那一次不留 crawl_log 行,否则预算会被自己的拒绝记录吃掉。
    assert throttle.spent_today() == 2


def test_budget_survives_process_restart(tmp_path) -> None:
    """当天已花从 crawl_log 数出,不是内存计数器——重启不能忘记今天的账。"""
    clock = FakeClock()
    throttle, repository, database, _sleeper, _clock = build(
        tmp_path, daily_budget=3, clock=clock
    )
    for page in (1, 2, 3):
        throttle.succeeded(throttle.acquire(queue=QUEUE, target=f"page={page}"))

    reborn = RoughcastThrottle(
        RoughcastRepository(database, clock=clock),
        daily_budget=3,
        min_interval_seconds=20.0,
        timezone=CHINA,
        clock=clock,
        monotonic=FakeMonotonic(),
        sleeper=RecordingSleeper(),
    )
    assert reborn.spent_today() == 3
    with pytest.raises(BudgetExhausted):
        reborn.acquire(queue=QUEUE, target="page=4")


def test_budget_resets_on_the_next_local_day(tmp_path) -> None:
    clock = FakeClock()
    throttle, _repo, _db, _sleeper, _clock = build(tmp_path, daily_budget=2, clock=clock)
    throttle.succeeded(throttle.acquire(queue=QUEUE, target="page=1"))
    throttle.succeeded(throttle.acquire(queue=QUEUE, target="page=2"))
    assert throttle.remaining_today() == 0

    clock.advance(days=1)
    assert throttle.spent_today() == 0
    assert throttle.remaining_today() == 2


# ------------------------------------------------------------------ 间隔


def test_minimum_interval_is_enforced_between_requests(tmp_path) -> None:
    throttle, _repo, _db, sleeper, _clock = build(tmp_path, min_interval=20.0)

    throttle.succeeded(throttle.acquire(queue=QUEUE, target="page=1"))
    assert sleeper.calls == []          # 第一次不等
    throttle.succeeded(throttle.acquire(queue=QUEUE, target="page=2"))

    assert sleeper.calls == [pytest.approx(20.0)]


def test_interval_credits_time_already_elapsed(tmp_path) -> None:
    throttle, _repo, _db, sleeper, _clock = build(tmp_path, min_interval=20.0)
    throttle.succeeded(throttle.acquire(queue=QUEUE, target="page=1"))
    throttle._monotonic.value += 30.0   # noqa: SLF001 —— 模拟请求本身耗时超过间隔

    throttle.succeeded(throttle.acquire(queue=QUEUE, target="page=2"))
    assert sleeper.calls == []          # 已经等够了,不再叠加


# ------------------------------------------------------------------ 熔断


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        ({"http_status": 429}, "http_429"),
        ({"http_status": 401, "error_code": "CRM_AUTH_REQUIRED"}, "http_401"),
        ({"http_status": 502, "error_code": "CRM_UPSTREAM_CHANGED"}, "http_502"),
        ({"error_code": "CRM_AUTH_REQUIRED"}, "code_CRM_AUTH_REQUIRED"),
        ({"error_code": "CRM_UPSTREAM_CHANGED"}, "code_CRM_UPSTREAM_CHANGED"),
    ],
)
def test_each_breaker_signal_trips_and_stops_all_requests(tmp_path, kwargs, expected) -> None:
    throttle, _repo, _db, _sleeper, _clock = build(tmp_path, daily_budget=50)
    log_id = throttle.acquire(queue=QUEUE, target="page=1")

    throttle.failed(log_id, **kwargs)

    assert throttle.breaker_reason == expected
    with pytest.raises(BreakerOpen) as excinfo:
        throttle.acquire(queue=QUEUE, target="page=2")
    assert excinfo.value.reason == "breaker_open"


def test_three_consecutive_failures_trip_the_breaker(tmp_path) -> None:
    throttle, _repo, _db, _sleeper, _clock = build(tmp_path, daily_budget=50)

    for _ in range(2):
        throttle.failed(throttle.acquire(queue=QUEUE, target="page=1"), http_status=500)
    assert throttle.breaker_reason is None       # 两次还不熔断

    throttle.failed(throttle.acquire(queue=QUEUE, target="page=1"), http_status=500)
    assert throttle.breaker_reason == "consecutive_failures_3"
    with pytest.raises(BreakerOpen):
        throttle.acquire(queue=QUEUE, target="page=2")


def test_success_resets_the_consecutive_failure_counter(tmp_path) -> None:
    throttle, _repo, _db, _sleeper, _clock = build(tmp_path, daily_budget=50)

    for _ in range(2):
        throttle.failed(throttle.acquire(queue=QUEUE, target="page=1"), http_status=500)
    throttle.succeeded(throttle.acquire(queue=QUEUE, target="page=1"))
    for _ in range(2):
        throttle.failed(throttle.acquire(queue=QUEUE, target="page=2"), http_status=500)

    assert throttle.breaker_reason is None


def test_tripping_writes_an_auditable_breaker_row(tmp_path) -> None:
    throttle, _repo, database, _sleeper, _clock = build(tmp_path, daily_budget=50)
    throttle.failed(throttle.acquire(queue=QUEUE, target="page=1"), http_status=429)

    with database.connect() as db:
        rows = db.execute(
            "SELECT target, status, note FROM roughcast_crawl_log ORDER BY id"
        ).fetchall()
    assert [row["status"] for row in rows] == ["failed", "breaker"]
    assert rows[1]["target"] == "breaker"
    assert rows[1]["note"] == "http_429"
