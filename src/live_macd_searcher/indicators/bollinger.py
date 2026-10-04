"""Bollinger Bands: a simple moving average of close, plus and minus a multiple of the
standard deviation over the same closes.

    middle = SMA(close, period)
    upper  = middle + width * stdev(close, period)
    lower  = middle - width * stdev(close, period)

The deviation is the *population* standard deviation (divide by `period`, not
`period - 1`). That is what TradingView and most charting packages draw, and the board
should agree with the chart you check it against (DESIGN §2).

`period` and `width` are parameters rather than constants here: their values are
tuning that lives in `detect/config.py`, and this layer may not import upward.
"""

import statistics
from collections import deque
from collections.abc import Iterable
from typing import NamedTuple


class Bands(NamedTuple):
    middle: float
    upper: float
    lower: float


def _bands(closes: Iterable[float], width: float) -> Bands:
    window = list(closes)
    # statistics uses exact rational arithmetic internally, so a flat run of closes
    # gives a deviation of exactly zero rather than float dust.
    middle = statistics.fmean(window)
    deviation = statistics.pstdev(window, mu=middle)
    return Bands(middle, middle + width * deviation, middle - width * deviation)


class BollingerState:
    """Bands updated one close at a time, for the live path.

    Recomputes over the last `period` closes on every update rather than keeping running
    sums. Running sums of squares lose precision over thousands of updates; recomputing
    20 numbers once an hour costs nothing and can never drift.
    """

    def __init__(self, period: int, width: float) -> None:
        if period < 2:
            raise ValueError(f"period must be at least 2, got {period}")
        self.period = period
        self.width = width
        self.closes: deque[float] = deque(maxlen=period)

    def peek(self, close: float) -> Bands | None:
        """The bands if `close` were the next close, without committing it.

        None until there are `period` closes to measure.
        """
        window = [*self.closes, close][-self.period :]
        if len(window) < self.period:
            return None
        return _bands(window, self.width)

    def update(self, close: float) -> Bands | None:
        """Commit a closed bar's close and return the bands. None until full."""
        self.closes.append(close)
        if len(self.closes) < self.period:
            return None
        return _bands(self.closes, self.width)


def bollinger(closes: Iterable[float], period: int, width: float) -> list[Bands | None]:
    """Bands for a whole series at once, for backfill. Built on `BollingerState`."""
    state = BollingerState(period, width)
    return [state.update(close) for close in closes]
