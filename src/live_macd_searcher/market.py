"""Market data shapes shared by the layers that move candles around: ingest, store, runtime."""

from typing import NamedTuple


class Candle(NamedTuple):
    """One 1h candle. `open_time` is the exchange's, in ms: the only clock (invariant 2)."""

    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
