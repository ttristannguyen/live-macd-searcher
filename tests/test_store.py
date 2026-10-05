"""The store: schema, idempotent writes, retention, and replay as a no-op (PLAN M3)."""

import math
import sqlite3
from dataclasses import fields, replace

import pytest

from live_macd_searcher.detect.config import (
    BACKFILL_BARS,
    BAR_RETENTION_DAYS,
    BOLLINGER_PERIOD,
    BOLLINGER_WIDTH,
)
from live_macd_searcher.detect.detector import BarReading, WindowDetector
from live_macd_searcher.detect.window import Window
from live_macd_searcher.indicators.bollinger import BollingerState
from live_macd_searcher.indicators.macd import MacdState
from live_macd_searcher.market import Candle
from live_macd_searcher.store.db import (
    DAY_MS,
    connect,
    prune_bars,
    upsert_bar,
    upsert_window,
)

HOUR = 3_600_000


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path / "test.sqlite3")
    yield connection
    connection.close()


def table(conn, name):
    return [tuple(row) for row in conn.execute(f"SELECT * FROM {name} ORDER BY 1, 2")]


WINDOW = Window(
    symbol="BTC",
    asset_class="crypto",
    side="bullish",
    state="active",
    started_at=10 * HOUR,
    peak_pct=-0.5,
    price_at_open=100.0,
    bars=2,
    regime="reversal",
    line_turn=True,
    strength=55.0,
    updated_at=12 * HOUR,
    hist_pct=-0.3,
    macd_pct=-1.0,
    signal_pct=-0.7,
    band="near",
    band_offset=-0.05,
)


# --- schema ------------------------------------------------------------------------


def test_connect_uses_wal_and_is_safe_to_repeat_on_every_boot(tmp_path):
    path = tmp_path / "boot.sqlite3"
    connect(path).close()
    again = connect(path)
    assert again.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    again.close()


def test_windows_columns_are_exactly_window_fields_plus_id(conn):
    # The upsert is generated from Window's fields, so schema.sql must match them. Order
    # doesn't matter: the upsert names every column.
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(windows)")}
    assert columns == {"id"} | {field.name for field in fields(Window)}


def test_schema_rejects_a_state_outside_the_vocabulary(conn):
    # `forming` exists only inside the detector and must never reach the outcome record.
    with pytest.raises(sqlite3.IntegrityError):
        upsert_window(conn, replace(WINDOW, state="forming"))


# --- bars --------------------------------------------------------------------------


def test_upserting_the_same_bar_twice_keeps_one_row(conn):
    candle = Candle(open_time=HOUR, open=1.0, high=2.0, low=0.5, close=1.5, volume=10.0)
    upsert_bar(conn, "BTC", candle)
    upsert_bar(conn, "BTC", candle)
    assert table(conn, "bars") == [("BTC", HOUR, 1.0, 2.0, 0.5, 1.5, 10.0)]


def test_a_corrected_bar_replaces_the_stored_one(conn):
    upsert_bar(conn, "BTC", Candle(HOUR, 1.0, 2.0, 0.5, 1.5, 10.0))
    upsert_bar(conn, "BTC", Candle(HOUR, 1.0, 2.1, 0.5, 1.6, 12.0))
    assert table(conn, "bars") == [("BTC", HOUR, 1.0, 2.1, 0.5, 1.6, 12.0)]


# --- windows -----------------------------------------------------------------------


def test_upserting_the_same_window_twice_keeps_one_row(conn):
    upsert_window(conn, WINDOW)
    upsert_window(conn, WINDOW)
    assert conn.execute("SELECT COUNT(*) FROM windows").fetchone()[0] == 1


def test_a_newer_snapshot_updates_the_window(conn):
    upsert_window(conn, WINDOW)
    crossed = replace(WINDOW, state="crossed", updated_at=13 * HOUR, crossed_at=13 * HOUR,
                      price_at_cross=101.0, band_at_cross="through")  # fmt: skip
    upsert_window(conn, crossed)
    row = conn.execute("SELECT state, crossed_at, band_at_cross FROM windows").fetchone()
    assert tuple(row) == ("crossed", 13 * HOUR, "through")


def test_an_older_snapshot_never_rolls_a_window_back(conn):
    resolved = replace(WINDOW, state="hit", updated_at=20 * HOUR, resolved_at=20 * HOUR)
    upsert_window(conn, resolved)
    upsert_window(conn, WINDOW)  # an earlier snapshot, as a replay would send it
    assert conn.execute("SELECT state FROM windows").fetchone()[0] == "hit"


def test_windows_with_different_peaks_are_different_rows(conn):
    upsert_window(conn, WINDOW)
    upsert_window(conn, replace(WINDOW, started_at=30 * HOUR, updated_at=32 * HOUR))
    upsert_window(conn, replace(WINDOW, symbol="ETH"))
    assert conn.execute("SELECT COUNT(*) FROM windows").fetchone()[0] == 3


# --- retention ---------------------------------------------------------------------


def test_prune_keeps_bars_within_retention_of_the_newest_and_never_touches_windows(conn):
    newest = 200 * DAY_MS
    keep_from = newest - BAR_RETENTION_DAYS * DAY_MS
    for open_time in (keep_from - HOUR, keep_from, newest):
        upsert_bar(conn, "BTC", Candle(open_time, 1.0, 1.0, 1.0, 1.0, 1.0))
    upsert_window(conn, WINDOW)  # its bars are long gone after this prune

    assert prune_bars(conn) == 1
    assert [row[1] for row in table(conn, "bars")] == [keep_from, newest]
    assert conn.execute("SELECT COUNT(*) FROM windows").fetchone()[0] == 1


# --- replay is a no-op: the M3 gate ------------------------------------------------

# Long enough to warm up and then produce windows of every kind. Deterministic: two
# sines and a drift, rounded to cents.
CANDLES = []
for i in range(BACKFILL_BARS + 400):
    close = round(100 + 6 * math.sin(i / 9) + 2.5 * math.sin(i / 2.7) + 0.01 * i, 2)
    CANDLES.append(Candle(i * HOUR, close, close + 0.4, close - 0.4, close, 1.0))


def run(conn, candles, symbol="BTC"):
    """What the runtime will do (M5): indicators, detector, store — from a cold start."""
    macd, bands = MacdState(), BollingerState(BOLLINGER_PERIOD, BOLLINGER_WIDTH)
    detector = WindowDetector(symbol)
    for candle in candles:
        point = macd.update(candle.close)
        reading = BarReading(
            candle.open_time, candle.high, candle.low, candle.close,
            point.macd, point.signal, point.hist, bands.update(candle.close),
        )  # fmt: skip
        upsert_bar(conn, symbol, candle)
        for event in detector.step(reading):
            upsert_window(conn, event.window)


def test_the_replay_fixture_is_not_vacuous(conn):
    run(conn, CANDLES)
    states = {row[0] for row in conn.execute("SELECT state FROM windows")}
    assert conn.execute("SELECT COUNT(*) FROM windows").fetchone()[0] >= 10
    assert {"failed", "hit"} <= states  # windows of more than one outcome were written


def test_replaying_the_same_range_twice_changes_nothing(conn):
    run(conn, CANDLES)
    bars_before, windows_before = table(conn, "bars"), table(conn, "windows")

    run(conn, CANDLES)  # a restart that replays everything from scratch

    assert table(conn, "bars") == bars_before
    assert table(conn, "windows") == windows_before


def test_replaying_an_earlier_part_of_the_range_rolls_nothing_back(conn):
    run(conn, CANDLES)
    windows_before = table(conn, "windows")

    run(conn, CANDLES[: BACKFILL_BARS + 200])  # replay stops partway, as a crash might

    assert table(conn, "windows") == windows_before
