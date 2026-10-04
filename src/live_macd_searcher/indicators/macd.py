"""MACD: two EMAs of close, their difference, and an EMA of that difference.

    macd   = EMA(close, 12) - EMA(close, 26)
    signal = EMA(macd, 9)
    hist   = macd - signal

The periods are the definition of the indicator (DESIGN §2), not tuning, which is why
they live here rather than in `detect/config.py`. All values are in price units;
normalising them to percent of close is the detector's job, not this module's.
"""

from collections.abc import Iterable
from typing import NamedTuple

from .ema import EmaState

FAST_PERIOD = 12
SLOW_PERIOD = 26
SIGNAL_PERIOD = 9


class MacdPoint(NamedTuple):
    macd: float
    signal: float
    hist: float


class MacdState:
    """MACD updated one close at a time, for the live path. O(1) per close."""

    def __init__(self) -> None:
        self.fast = EmaState(FAST_PERIOD)
        self.slow = EmaState(SLOW_PERIOD)
        self.signal = EmaState(SIGNAL_PERIOD)

    def peek(self, close: float) -> MacdPoint:
        """The reading if `close` were the next bar's close, without committing it.

        This is how the forming bar becomes a `provisional` reading (invariant 1).
        """
        macd = self.fast.peek(close) - self.slow.peek(close)
        signal = self.signal.peek(macd)
        return MacdPoint(macd, signal, macd - signal)

    def update(self, close: float) -> MacdPoint:
        """Commit a closed bar's close and return its reading."""
        macd = self.fast.update(close) - self.slow.update(close)
        signal = self.signal.update(macd)
        return MacdPoint(macd, signal, macd - signal)


def macd(closes: Iterable[float]) -> list[MacdPoint]:
    """MACD for a whole series at once, for backfill. Built on `MacdState`."""
    state = MacdState()
    return [state.update(close) for close in closes]
