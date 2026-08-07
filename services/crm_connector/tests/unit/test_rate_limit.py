from __future__ import annotations

from app.mcp.rate_limit import RateLimiter


def test_allows_calls_up_to_the_limit() -> None:
    limiter = RateLimiter(limit_per_window=3)
    assert limiter.allow("alice") is True
    assert limiter.allow("alice") is True
    assert limiter.allow("alice") is True


def test_rejects_calls_beyond_the_limit() -> None:
    limiter = RateLimiter(limit_per_window=2)
    assert limiter.allow("alice") is True
    assert limiter.allow("alice") is True
    assert limiter.allow("alice") is False


def test_window_slides_after_elapsed(monkeypatch) -> None:
    now = 1_000_000.0
    monkeypatch.setattr("app.mcp.rate_limit.time.monotonic", lambda: now)

    limiter = RateLimiter(limit_per_window=2)
    assert limiter.allow("alice") is True
    assert limiter.allow("alice") is True
    assert limiter.allow("alice") is False

    # 61 seconds later the sliding window has fully elapsed.
    now += RateLimiter._WINDOW_SECONDS + 1
    assert limiter.allow("alice") is True


def test_keys_are_independent() -> None:
    limiter = RateLimiter(limit_per_window=1)
    assert limiter.allow("alice") is True
    assert limiter.allow("alice") is False
    assert limiter.allow("bob") is True
