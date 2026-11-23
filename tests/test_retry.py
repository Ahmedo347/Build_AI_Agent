"""Retry and rate-limit behaviour of ``request_completion``.

Sleeping is always monkeypatched, so the suite stays fast and offline.
"""

from __future__ import annotations

import pytest
from fakes import (
    FakeClient,
    completion,
    connection_error,
    message,
    rate_limit_error,
    status_error,
)
from openai import APIConnectionError, APIStatusError, RateLimitError

import main
from ratelimit import RateLimiter


class FakeClock:
    def __init__(self, start: float = 100.0) -> None:
        self.now = start
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture
def sleeps(monkeypatch):
    recorded: list[float] = []
    monkeypatch.setattr(main.time, "sleep", recorded.append)
    return recorded


def call(client, limiter=None):
    return main.request_completion(
        client,
        limiter or RateLimiter(0),
        model="m",
        messages=[],
        tools=[],
    )


def test_rate_limit_is_retried_and_honours_retry_after(sleeps):
    client = FakeClient([rate_limit_error(retry_after=2), completion(message("done"))])

    response = call(client)

    assert response.choices[0].message.content == "done"
    assert len(client.calls) == 2
    assert 2.0 <= sleeps[0] <= 2.5


def test_connection_error_backs_off_exponentially(sleeps):
    client = FakeClient(
        [connection_error(), connection_error(), completion(message("ok"))]
    )

    call(client)

    # Base backoff is 4s, plus up to 0.5s of jitter.
    assert 4.0 <= sleeps[0] <= 4.5
    assert 8.0 <= sleeps[1] <= 8.5


def test_server_errors_are_retried(sleeps):
    client = FakeClient([status_error(503), completion(message("ok"))])

    call(client)

    assert len(client.calls) == 2
    assert len(sleeps) == 1


def test_client_errors_are_not_retried(sleeps):
    client = FakeClient([status_error(400)])

    with pytest.raises(APIStatusError):
        call(client)

    assert len(client.calls) == 1
    assert sleeps == []


def test_gives_up_after_max_retries(sleeps):
    client = FakeClient([rate_limit_error() for _ in range(main.MAX_RETRIES)])

    with pytest.raises(RateLimitError):
        call(client)

    assert len(client.calls) == main.MAX_RETRIES
    assert len(sleeps) == main.MAX_RETRIES - 1


def test_gives_up_after_max_retries_on_connection_errors(sleeps):
    client = FakeClient([connection_error() for _ in range(main.MAX_RETRIES)])

    with pytest.raises(APIConnectionError):
        call(client)

    assert len(client.calls) == main.MAX_RETRIES


def test_backoff_is_capped(sleeps, monkeypatch):
    monkeypatch.setattr(main, "BACKOFF_BASE_SECONDS", 100.0)
    client = FakeClient([connection_error(), completion(message("ok"))])

    call(client)

    assert sleeps[0] <= main.MAX_BACKOFF_SECONDS + 0.5


def test_limiter_spaces_out_requests():
    clock = FakeClock()
    limiter = RateLimiter(4.0, clock=clock.clock, sleep=clock.sleep)
    client = FakeClient([completion(message("one")), completion(message("two"))])

    call(client, limiter)
    call(client, limiter)

    assert clock.slept == [4.0]
    assert limiter.total_wait == 4.0


def test_limiter_waits_before_every_attempt(sleeps):
    class RecordingLimiter:
        def __init__(self):
            self.calls = 0
            self.total_wait = 0.0

        def wait(self):
            self.calls += 1
            return 0.0

    limiter = RecordingLimiter()
    client = FakeClient([connection_error(), completion(message("ok"))])

    call(client, limiter)

    assert limiter.calls == 2
