"""The runtime: boot, the closed-bar path, refresh, the daily job — and the M5 gate:
killing and restarting the process changes nothing about the stored windows."""

import asyncio
import sqlite3

import pytest

from live_macd_searcher.detect.window import Window
from live_macd_searcher.ingest.feed import HOUR_MS, FeedDisconnected, FeedError, Universe
from live_macd_searcher.market import Candle
from live_macd_searcher.runtime import Runtime
from live_macd_searcher.store.db import connect, load_bars, upsert_bar, upsert_window
from tests.fakes import FakeFeed, Session, stream_hours, synthetic_history

SYMBOLS = ["BTC", "xyz:GOLD"]
HISTORY = {symbol: synthetic_history(700, phase) for phase, symbol in enumerate(SYMBOLS)}


def boot_and_stream(runtime: Runtime, sessions: int) -> None:
    async def go():
        if runtime.ingest is None:
            await runtime.boot()
        for _ in range(sessions):
            with pytest.raises(FeedDisconnected):
                await runtime.ingest.run_session()

    asyncio.run(go())


def rows(conn: sqlite3.Connection, table: str) -> list[tuple]:
    """A table's contents, minus `id`: ids follow insertion order, which a gap-fill
    (symbol by symbol) and live streaming (hour by hour) legitimately differ on."""
    if table == "windows":
        query = f"SELECT {', '.join(Window.__dataclass_fields__)} FROM windows"
        return sorted(tuple(row) for row in conn.execute(query))
    return sorted(tuple(row) for row in conn.execute(f"SELECT * FROM {table}"))


# --- the M5 gate -------------------------------------------------------------------


def test_a_restart_changes_nothing_about_the_stored_windows(tmp_path):
    feed = FakeFeed(HISTORY, [Session(now_hour=450, messages=stream_hours(HISTORY, 450, 699))])
    never_stopped = Runtime(connect(tmp_path / "c.sqlite3"), feed, now_ms=feed.now_ms)
    boot_and_stream(never_stopped, sessions=1)

    # Killed mid-hour 583 — a forming candle in memory, an `active` BTC window (peak at
    # 580) in its detector — and started again as a fresh process while 587 is forming.
    # The window crosses at 585, while the process is down, and resolves after it.
    feed = FakeFeed(HISTORY, [
        Session(now_hour=450, messages=stream_hours(HISTORY, 450, 583, cut_after_partial=True)),
        Session(now_hour=587, messages=stream_hours(HISTORY, 587, 699)),
    ])  # fmt: skip
    first = Runtime(connect(tmp_path / "ab.sqlite3"), feed, now_ms=feed.now_ms)
    boot_and_stream(first, sessions=1)
    first.conn.close()
    second = Runtime(connect(tmp_path / "ab.sqlite3"), feed, now_ms=feed.now_ms)
    boot_and_stream(second, sessions=1)

    assert rows(second.conn, "bars") == rows(never_stopped.conn, "bars")
    assert rows(second.conn, "windows") == rows(never_stopped.conn, "windows")

    # Not vacuous: windows were open across the restart and resolved after it.
    kill, restart = 583 * HOUR_MS, 587 * HOUR_MS
    spanning = never_stopped.conn.execute(
        "SELECT COUNT(*) FROM windows WHERE started_at < ? AND resolved_at >= ?", (kill, restart)
    ).fetchone()[0]
    assert spanning > 0


# --- boot --------------------------------------------------------------------------


class FixedUniverseFeed(FakeFeed):
    def __init__(self, universe: Universe) -> None:
        super().__init__(HISTORY, [Session(now_hour=450, messages=[])])
        self.fixed = universe

    async def universe(self) -> Universe:
        return self.fixed


def window_on(symbol: str) -> Window:
    return Window(
        symbol=symbol, asset_class="crypto", side="bullish", state="active",
        started_at=HOUR_MS, peak_pct=-0.5, price_at_open=100.0, bars=2, regime="reversal",
        line_turn=True, strength=50.0, updated_at=2 * HOUR_MS, hist_pct=-0.3,
        macd_pct=-1.0, signal_pct=-0.7, band="near", band_offset=0.0,
    )  # fmt: skip


def test_a_symbol_with_a_live_window_stays_subscribed_only_while_still_listed(tmp_path):
    conn = connect(tmp_path / "db.sqlite3")
    upsert_window(conn, window_on("ETH"))  # below the floors now, but still listed
    upsert_window(conn, window_on("DEAD"))  # delisted: subscribing would drop everything
    feed = FixedUniverseFeed(Universe(tradeable=["BTC"], listed=frozenset({"BTC", "ETH"})))
    runtime = Runtime(conn, feed, now_ms=feed.now_ms)
    asyncio.run(runtime.boot())
    assert runtime.ingest.symbols == ["BTC", "ETH"]


def test_a_symbol_down_longer_than_retention_is_re_seeded(tmp_path):
    conn = connect(tmp_path / "db.sqlite3")
    for candle in HISTORY["BTC"][:10]:
        upsert_bar(conn, "BTC", candle)
    feed = FixedUniverseFeed(Universe(["BTC"], frozenset({"BTC"})))
    far_later = lambda: (10 + 91 * 24) * HOUR_MS  # noqa: E731 — 91 days after the last bar
    runtime = Runtime(conn, feed, now_ms=far_later)
    asyncio.run(runtime.boot())
    assert load_bars(conn, "BTC") == []
    assert runtime.states["BTC"].last_bar is None  # so the gap-fill starts cold


def test_boot_rebuilds_state_from_stored_bars(tmp_path):
    feed = FakeFeed(HISTORY, [Session(now_hour=450, messages=[])])
    runtime = Runtime(connect(tmp_path / "db.sqlite3"), feed, now_ms=feed.now_ms)
    boot_and_stream(runtime, sessions=1)

    reborn = Runtime(runtime.conn, FakeFeed(HISTORY, [Session(450, [])]), now_ms=feed.now_ms)
    asyncio.run(reborn.boot())
    for symbol in SYMBOLS:
        assert not reborn.states[symbol].warming
        assert reborn.states[symbol].last_bar == HISTORY[symbol][449]


# --- refresh and the daily job -----------------------------------------------------


def test_refresh_reads_the_forming_bars_and_writes_nothing(tmp_path):
    feed = FakeFeed(HISTORY, [Session(now_hour=450, messages=stream_hours(HISTORY, 450, 455))])
    runtime = Runtime(connect(tmp_path / "db.sqlite3"), feed, now_ms=feed.now_ms)
    boot_and_stream(runtime, sessions=1)
    bars_before = rows(runtime.conn, "bars")

    runtime.refresh()

    assert set(runtime.provisional) == set(SYMBOLS)
    assert runtime.provisional["BTC"].open_time == 455 * HOUR_MS  # the held, forming hour
    assert rows(runtime.conn, "bars") == bars_before


class ChangingUniverseFeed(FixedUniverseFeed):
    def __init__(self) -> None:
        super().__init__(Universe(["BTC"], frozenset({"BTC", "ETH"})))
        self.fail = False

    async def universe(self) -> Universe:
        if self.fail:
            raise FeedError("exchange down")
        return self.fixed


def test_daily_follows_the_universe_and_survives_a_failed_refresh(tmp_path):
    feed = ChangingUniverseFeed()
    runtime = Runtime(connect(tmp_path / "db.sqlite3"), feed, now_ms=feed.now_ms)
    asyncio.run(runtime.boot())
    assert runtime.ingest.symbols == ["BTC"]

    feed.fixed = Universe(["BTC", "ETH"], frozenset({"BTC", "ETH"}))
    asyncio.run(runtime.daily())
    assert runtime.ingest.symbols == ["BTC", "ETH"]
    assert "ETH" in runtime.states

    feed.fail = True
    asyncio.run(runtime.daily())  # logs and keeps going
    assert runtime.ingest.symbols == ["BTC", "ETH"]


def test_a_failed_write_is_not_swallowed(tmp_path):
    # Persistence is not best-effort (CLAUDE.md): the stored windows are the outcome record.
    feed = FakeFeed(HISTORY, [Session(now_hour=450, messages=[])])
    runtime = Runtime(connect(tmp_path / "db.sqlite3"), feed, now_ms=feed.now_ms)
    asyncio.run(runtime.boot())
    runtime.conn.close()
    with pytest.raises(sqlite3.ProgrammingError):
        runtime._on_closed("BTC", Candle(0, 1.0, 1.0, 1.0, 1.0, 1.0))
