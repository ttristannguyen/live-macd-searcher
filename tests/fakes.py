"""A scripted market for tests: one true price history, served the way Hyperliquid does.

REST answers as of the current session's `now_hour`: every earlier hour closed, that hour
still forming. Each websocket session replays a scripted list of messages, then drops.
"""

import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from live_macd_searcher.ingest.feed import HOUR_MS, CandleMessage, FeedDisconnected, Universe
from live_macd_searcher.market import Candle


def synthetic_history(hours: int, phase: float) -> list[Candle]:
    """A deterministic hourly market: two sines, cents, and growing volume. Long enough
    histories produce windows of every kind after warm-up."""
    candles, previous = [], 100.0
    for hour in range(hours):
        close = round(100 + 6 * math.sin(hour / 9 + phase) + 2.5 * math.sin(hour / 2.7), 2)
        high, low = max(previous, close) + 0.3, min(previous, close) - 0.3
        candles.append(Candle(hour * HOUR_MS, previous, high, low, close, 100.0 + hour))
        previous = close
    return candles


def partial(candle: Candle) -> Candle:
    """A mid-hour snapshot of `candle`: half its volume, and a close it hasn't reached yet."""
    return candle._replace(close=candle.open, volume=candle.volume / 2)


def stream_hours(
    history: dict[str, list[Candle]], first: int, last: int, *, cut_after_partial: bool = False
) -> list[CandleMessage]:
    """Websocket traffic for hours `first..last`: each symbol's partial then final snapshot.

    With `cut_after_partial`, hour `last` gets only its partial — a drop mid-hour.
    """
    messages: list[CandleMessage] = []
    for hour in range(first, last + 1):
        for symbol, candles in history.items():
            messages.append((symbol, partial(candles[hour])))
            if not (cut_after_partial and hour == last):
                messages.append((symbol, candles[hour]))
    return messages


@dataclass
class Session:
    now_hour: int  # what REST sees during this session
    messages: list[CandleMessage]


class FakeFeed:
    def __init__(self, history: dict[str, list[Candle]], sessions: list[Session]) -> None:
        self.history = history
        self.sessions = list(sessions)
        self.now_hour = sessions[0].now_hour
        self.requests: list[tuple[str, int, int]] = []  # every candles() call
        self.rate_limited = 0
        self.weight_spent = 0

    def now_ms(self) -> int:
        return self.now_hour * HOUR_MS + HOUR_MS // 2  # mid-hour

    async def universe(self) -> Universe:
        return Universe(list(self.history), frozenset(self.history))

    async def candles(self, symbol: str, start_ms: int, end_ms: int) -> list[Candle]:
        """Every candle *overlapping* the range, as Hyperliquid returns them (observed
        2026-10-05) — including one that opened before `start_ms`."""
        self.requests.append((symbol, start_ms, end_ms))
        candles = self.history[symbol]
        visible = candles[: self.now_hour] + [partial(candles[self.now_hour])]
        return [c for c in visible if c.open_time + HOUR_MS > start_ms and c.open_time <= end_ms]

    @asynccontextmanager
    async def connect(self, symbols: list[str]):
        if not self.sessions:
            raise NoMoreSessions
        session = self.sessions.pop(0)
        self.now_hour = session.now_hour
        yield self._replay(session.messages)

    async def _replay(self, messages: list[CandleMessage]) -> AsyncIterator[CandleMessage]:
        for message in messages:
            yield message
        raise FeedDisconnected("scripted end of session")


class NoMoreSessions(Exception):
    """Not a FeedError, so it stops `Ingest.run()` instead of being retried."""
