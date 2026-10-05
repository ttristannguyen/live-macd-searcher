"""Turning candle snapshots into closed bars (invariant 1, DESIGN §6).

Hyperliquid's websocket sends the *whole current candle* on every update, and a REST
response ends with the still-forming candle. Both go through `CandleCloser.offer()`,
which holds the latest snapshot per symbol and releases it as closed only when a
candle with a later `open_time` arrives. Our clock is never asked whether the hour is
over.
"""

from ..market import Candle


class CandleCloser:
    def __init__(self) -> None:
        self.forming: dict[str, Candle] = {}  # the held, still-forming candle per symbol

    def offer(self, symbol: str, candle: Candle) -> Candle | None:
        """Take one snapshot. Returns the candle it closed, if it closed one."""
        held = self.forming.get(symbol)
        if held is None:
            self.forming[symbol] = candle
            return None
        if candle.open_time < held.open_time:
            return None  # an hour that has already closed: stale, ignore
        if candle.open_time == held.open_time:
            # Two snapshots of the same hour. Volume only grows within an hour, so the
            # larger one is the more complete — whichever order they arrived in. This is
            # what makes a REST gap-fill and buffered websocket messages safe to mix.
            if candle.volume >= held.volume:
                self.forming[symbol] = candle
            return None
        self.forming[symbol] = candle
        return held
