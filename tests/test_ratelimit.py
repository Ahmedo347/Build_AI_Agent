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


def test_first_request_does_not_wait():
    clock = FakeClock()
    limiter = RateLimiter(4.0, clock=clock.clock, sleep=clock.sleep)

    assert limiter.wait() == 0.0
    assert clock.slept == []


def test_consecutive_requests_are_spaced():
    clock = FakeClock()
    limiter = RateLimiter(4.0, clock=clock.clock, sleep=clock.sleep)

    limiter.wait()
    waited = limiter.wait()

    assert waited == 4.0
    assert limiter.waits == 1
    assert limiter.total_wait == 4.0


def test_elapsed_time_counts_toward_the_interval():
    clock = FakeClock()
    limiter = RateLimiter(4.0, clock=clock.clock, sleep=clock.sleep)

    limiter.wait()
    clock.now += 3.0
    waited = limiter.wait()

    assert waited == 1.0


def test_zero_interval_never_sleeps():
    clock = FakeClock()
    limiter = RateLimiter(0, clock=clock.clock, sleep=clock.sleep)

    assert limiter.wait() == 0.0
    assert limiter.wait() == 0.0
    assert clock.slept == []


def test_negative_interval_is_treated_as_zero():
    clock = FakeClock()
    limiter = RateLimiter(-5, clock=clock.clock, sleep=clock.sleep)

    limiter.wait()
    assert limiter.wait() == 0.0
    assert clock.slept == []
