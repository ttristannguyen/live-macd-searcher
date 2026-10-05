"""The REST pacer never lets spending exceed its budget in any one-minute window."""

import asyncio

import pytest

from live_macd_searcher.ingest.pacer import WINDOW_SECONDS, RestPacer


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def spend_all(weights: list[int], budget: int) -> tuple[FakeTime, list[tuple[float, int]]]:
    time = FakeTime()
    pacer = RestPacer(budget, clock=time.clock, sleep=time.sleep)
    spent = []

    async def go():
        for weight in weights:
            await pacer.spend(weight)
            spent.append((time.now, weight))

    asyncio.run(go())
    return time, spent


def test_spending_within_budget_never_waits():
    time, _ = spend_all([20] * 30, budget=600)
    assert time.sleeps == []


def test_the_request_over_budget_waits_for_the_oldest_to_leave_the_window():
    time, spent = spend_all([20] * 31, budget=600)
    assert time.sleeps == [WINDOW_SECONDS]
    assert spent[-1] == (WINDOW_SECONDS, 20)


def test_no_one_minute_window_ever_exceeds_the_budget():
    # A cold start's shape: candle requests of mixed weight, far more than one minute's worth.
    weights = [27, 21, 20, 34, 21] * 60
    _, spent = spend_all(weights, budget=600)
    for when, _ in spent:
        in_window = sum(w for t, w in spent if when - WINDOW_SECONDS < t <= when)
        assert in_window <= 600


def test_a_single_request_heavier_than_the_whole_budget_is_refused():
    with pytest.raises(ValueError):
        spend_all([601], budget=600)
