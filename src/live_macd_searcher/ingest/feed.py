"""The one abstraction (DESIGN §5): a market data source.

It has a single real implementation (`HyperliquidFeed`). The seam exists so tests can
drive everything from a scripted fake with no network — not for a second exchange.
"""

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from typing import Protocol

from ..market import Candle

HOUR_MS = 3_600_000

# One websocket update: the symbol and its whole current candle.
CandleMessage = tuple[str, Candle]


class FeedError(ConnectionError):
    """The market data source failed in a way reconnecting may fix: the caller backs off
    and tries again. Anything else is a bug and is left to propagate."""


class FeedDisconnected(FeedError):
    """The live stream ended or could not be opened. The caller reconnects and gap-fills."""


class MarketFeed(Protocol):
    async def universe(self) -> list[str]:
        """Symbols worth watching today: listed, enabled, and above the liquidity floors."""
        ...

    async def candles(self, symbol: str, start_ms: int, end_ms: int) -> list[Candle]:
        """1h candles with `open_time` in `[start_ms, end_ms]`, oldest first.

        The last one may still be forming; `CandleCloser` decides, not the caller.
        """
        ...

    def connect(
        self, symbols: list[str]
    ) -> AbstractAsyncContextManager[AsyncIterator[CandleMessage]]:
        """Open the live stream. Subscriptions are active by the time this has entered,
        so a REST gap-fill made afterwards cannot leave a hole. Iterating raises
        `FeedDisconnected` when the stream drops."""
        ...
