"""Turning candle snapshots into closed bars (invariant 1, DESIGN §6).

Hyperliquid's websocket sends the *whole current candle* on every update, and a REST
response ends with the still-forming candle. Both go through `CandleCloser.offer()`,
which holds the latest snapshot per symbol and releases it as closed only when a
candle with a later `open_time` arrives. Our clock is never asked whether the hour is
over.

Hyperliquid sends no candle at all for an hour with no trades. Every such hour is
released as a flat bar at the previous close with zero volume (PLAN D13): the hour
happened and price didn't move. Skipping it instead would make the EMAs treat the next
bar as consecutive and compress `bars` and `POST_CROSS_BARS` for thin markets.
"""

from ..market import Candle
from .feed import HOUR_MS


class CandleCloser:
    def __init__(self) -> None:
        self.forming: dict[str, Candle] = {}  # the held, still-forming candle per symbol
        self.last_closed: dict[str, Candle] = {}  # the last bar released per symbol

    def offer(self, symbol: str, candle: Candle) -> list[Candle]:
        """Take one snapshot. Returns the bars it closed, oldest first — usually none or
        one, more when hours with no trades are filled."""
        held = self.forming.get(symbol)
        if held is None:
            self.forming[symbol] = candle
            return []
        if candle.open_time < held.open_time:
            return []  # an hour that has already closed: stale, ignore
        if candle.open_time == held.open_time:
            # Two snapshots of the same hour. Volume only grows within an hour, so the
            # larger one is the more complete — whichever order they arrived in. This is
            # what makes a REST gap-fill and buffered websocket messages safe to mix.
            if candle.volume >= held.volume:
                self.forming[symbol] = candle
            return []

        self.forming[symbol] = candle
        closed = []
        last = self.last_closed.get(symbol)
        if last is not None:
            closed += _flat_hours(last, until=held.open_time)  # e.g. a gap after a restart
        closed.append(held)
        closed += _flat_hours(held, until=candle.open_time)  # quiet hours before this one
        self.last_closed[symbol] = closed[-1]
        return closed


def _flat_hours(previous: Candle, *, until: int) -> list[Candle]:
    """A flat, zero-volume bar at `previous.close` for each hour after `previous` and
    before `until`."""
    price = previous.close
    return [
        Candle(open_time, price, price, price, price, 0.0)
        for open_time in range(previous.open_time + HOUR_MS, until, HOUR_MS)
    ]
