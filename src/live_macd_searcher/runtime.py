"""The running app: one asyncio loop and one dict of `SymbolState` (DESIGN §5).

Boot rebuilds every symbol by replaying its stored bars from the earliest. That is the
bar the previous process seeded its EMAs from, so the rebuilt state is bitwise what it
was — half-formed runs included — and a restart changes nothing about the windows it
goes on to write (PLAN M5). The live feed then resumes after the last stored bar.
"""

import asyncio
import inspect
import logging
import sqlite3
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .detect.config import BAR_RETENTION_DAYS, REFRESH_INTERVAL_MIN
from .detect.symbol_state import Provisional, SymbolState
from .detect.window import WindowEvent
from .ingest.feed import FeedError, MarketFeed, Universe
from .ingest.runner import Ingest
from .market import Candle
from .store.db import (
    DAY_MS,
    forget_bars,
    live_symbols,
    load_bars,
    prune_bars,
    record_bar,
    window_id,
)

log = logging.getLogger(__name__)

DAY_SECONDS = 86_400


@dataclass(frozen=True)
class RuntimeStatus:
    """What /api/health needs to judge the feed. Times are wall clock (`time.time()`)."""

    booted: bool
    streamed: bool  # a gap-fill has completed at least once: past starting up
    streaming: bool  # right now
    last_message_at: float | None
    last_refresh_at: float | None
    symbols: int
    warm: int
    rest_429s: int
    rest_weight_spent: int


class Runtime:
    def __init__(
        self,
        conn: sqlite3.Connection,
        feed: MarketFeed,
        *,
        now_ms: Callable[[], int] = lambda: int(time.time() * 1000),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        on_window: Callable[[WindowEvent, int], None] | None = None,
        on_provisional: Callable[[list[Provisional]], None] | None = None,
    ) -> None:
        """`on_window` hears every window event with its stored row id, and
        `on_provisional` every refresh's readings — the live stream (M7)."""
        self.conn = conn
        self.on_window = on_window
        self.on_provisional = on_provisional
        self.feed = feed
        self.now_ms = now_ms
        self._sleep = sleep
        self.states: dict[str, SymbolState] = {}  # the only shared state
        self.provisional: dict[str, Provisional] = {}  # latest forming-bar readings
        self.ingest: Ingest | None = None  # created by boot()
        self.last_refresh_at: float | None = None

    def status(self) -> RuntimeStatus:
        ingest = self.ingest
        return RuntimeStatus(
            booted=ingest is not None,
            streamed=ingest is not None and ingest.has_streamed,
            streaming=ingest is not None and ingest.streaming,
            last_message_at=ingest.last_message_at if ingest is not None else None,
            last_refresh_at=self.last_refresh_at,
            symbols=len(ingest.symbols) if ingest is not None else 0,
            warm=sum(not state.warming for state in self.states.values()),
            rest_429s=self.feed.rate_limited,
            rest_weight_spent=self.feed.weight_spent,
        )

    async def run(self) -> None:
        await self.boot()
        assert self.ingest is not None
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(self.ingest.run())
            tasks.create_task(self._every(REFRESH_INTERVAL_MIN * 60, self.refresh))
            tasks.create_task(self._every(DAY_SECONDS, self.daily))

    async def boot(self) -> None:
        """Rebuild every symbol from its stored bars, then set up the live feed."""
        symbols = self._subscription(await self.feed.universe())
        for symbol in symbols:
            self._restore(symbol)
        warm = sum(not state.warming for state in self.states.values())
        log.info("booted: %d symbols, %d warm from stored bars", len(symbols), warm)
        self.ingest = Ingest(
            self.feed, symbols, self._on_closed,
            last_stored=self._last_stored, now_ms=self.now_ms, sleep=self._sleep,
        )  # fmt: skip

    def refresh(self) -> None:
        """Recompute provisional readings from each held forming candle. Never persisted,
        never moves a window (invariant 1)."""
        assert self.ingest is not None
        readings = []
        for symbol, forming in self.ingest.closer.forming.items():
            state = self.states.get(symbol)
            reading = state.peek(forming) if state is not None else None
            if reading is not None:
                self.provisional[symbol] = reading
                readings.append(reading)
        self.last_refresh_at = time.time()
        if self.on_provisional is not None:
            self.on_provisional(readings)

    async def daily(self) -> None:
        """Prune old bars, and follow the universe as it changes."""
        log.info("pruned %d bars", prune_bars(self.conn))
        try:
            universe = await self.feed.universe()
        except FeedError as exc:
            # Keep today's subscriptions; tomorrow tries again. Nothing is lost meanwhile.
            log.warning("universe refresh failed, keeping current symbols: %s", exc)
            return
        symbols = self._subscription(universe)
        assert self.ingest is not None
        if symbols != self.ingest.symbols:
            for symbol in set(symbols) - set(self.states):
                self._restore(symbol)
            log.info("universe changed: %d symbols, resubscribing", len(symbols))
            self.ingest.resubscribe(symbols)

    # --- internals -------------------------------------------------------------------

    def _subscription(self, universe: Universe) -> list[str]:
        """Today's tradeable symbols, plus any with a live window — while still listed.

        Subscribing to a name the exchange doesn't list drops the whole connection
        (DESIGN §6), so a live window on a delisted symbol is left as it stands: there
        are no more bars to resolve it, and resolving it by guesswork would be dishonest.
        """
        live = live_symbols(self.conn)
        if unlisted := live - universe.listed:
            log.warning("live windows on symbols no longer listed, left as they stand: %s",
                        sorted(unlisted))  # fmt: skip
        return sorted(set(universe.tradeable) | (live & universe.listed))

    def _restore(self, symbol: str) -> None:
        state = SymbolState(symbol)
        bars = load_bars(self.conn, symbol)
        if bars and bars[-1].open_time < self.now_ms() - BAR_RETENTION_DAYS * DAY_MS:
            # Down longer than the history we keep. Gap-filling would flat-fill months, and
            # splicing a fresh backfill onto these bars would seed the next boot's replay
            # differently from this process. Start the symbol over instead.
            log.warning("%s: last stored bar is older than %d days, re-seeding",
                        symbol, BAR_RETENTION_DAYS)  # fmt: skip
            forget_bars(self.conn, symbol)
            bars = []
        for bar in bars:
            # Whatever these produced is already stored: record_bar is atomic.
            state.on_bar(bar)
        self.states[symbol] = state

    def _last_stored(self, symbol: str) -> Candle | None:
        state = self.states.get(symbol)
        return state.last_bar if state is not None else None

    def _on_closed(self, symbol: str, candle: Candle) -> None:
        state = self.states.get(symbol)
        if state is None:
            state = self.states[symbol] = SymbolState(symbol)
        events = state.on_bar(candle)
        # Not best-effort: a failed write raises and stops the app (CLAUDE.md guardrails).
        record_bar(self.conn, symbol, candle, [event.window for event in events])
        for event in events:
            # Only after the write: the stream never shows what the database doesn't hold.
            if self.on_window is not None:
                self.on_window(event, window_id(self.conn, symbol, event.window.started_at))
            window = event.window
            log.info("window %s: %s %s %s strength=%.0f", event.kind, symbol, window.side,
                     window.state, window.strength)  # fmt: skip

    async def _every(self, seconds: float, action: Callable[[], object]) -> None:
        while True:
            await self._sleep(seconds)
            result = action()
            if inspect.isawaitable(result):
                await result
