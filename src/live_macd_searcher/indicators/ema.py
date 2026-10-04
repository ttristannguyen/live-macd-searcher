"""Exponential moving average.

The standard recurrence, `ema = prev + alpha * (value - prev)` with
`alpha = 2 / (period + 1)`, seeded with the first value. That is what pandas computes
with `ewm(span=period, adjust=False)`, and what `macd_searcher` uses, so the two apps
agree on every number.

Seeding with the first value rather than an SMA of the first `period` values is only
visible during warm-up: the seed's influence decays by a factor of `1 - alpha` per bar,
and nothing is emitted until it is gone (DESIGN §6, `BACKFILL_BARS`).
"""

from collections.abc import Iterable


class EmaState:
    """An EMA updated one value at a time, for the live path. O(1) per value."""

    def __init__(self, period: int) -> None:
        if period < 1:
            raise ValueError(f"period must be at least 1, got {period}")
        self.alpha = 2.0 / (period + 1)
        self.value: float | None = None

    def peek(self, value: float) -> float:
        """The EMA if `value` were the next input, without committing it."""
        if self.value is None:
            return value
        return self.value + self.alpha * (value - self.value)

    def update(self, value: float) -> float:
        """Commit `value` and return the new EMA."""
        self.value = self.peek(value)
        return self.value


def ema(values: Iterable[float], period: int) -> list[float]:
    """The EMA of a whole series at once, for backfill.

    Built on `EmaState` rather than written separately, so batch and live can never
    disagree.
    """
    state = EmaState(period)
    return [state.update(value) for value in values]
