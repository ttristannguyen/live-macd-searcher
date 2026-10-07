"""The store: schema, idempotent writes, retention, and replay as a no-op (PLAN M3)."""

import json
import math
import sqlite3
from dataclasses import fields, replace

import pytest

from live_macd_searcher.detect.config import BACKFILL_BARS, BAR_RETENTION_DAYS
from live_macd_searcher.detect.symbol_state import SymbolState
from live_macd_searcher.detect.window import Window, WindowEvent
from live_macd_searcher.market import Candle
from live_macd_searcher.store.db import (
    DAY_MS,
    connect,
    forget_bars,
    live_symbols,
    load_bars,
    prune_bars,
    record_bar,
    start_run,
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
    assert again.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
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


def test_record_bar_writes_the_bar_and_its_windows_together(conn):
    candle = Candle(12 * HOUR, 1.0, 1.0, 1.0, 1.0, 1.0)
    record_bar(conn, "BTC", candle, [WindowEvent("opened", WINDOW)], None)
    assert load_bars(conn, "BTC") == [candle]
    assert conn.execute("SELECT COUNT(*) FROM windows").fetchone()[0] == 1


def test_record_bar_is_atomic_a_bad_window_rolls_back_its_bar(conn):
    # A crash or a failed write must never leave a bar stored without its windows: a
    # restart replays stored bars without writing, trusting their windows are stored.
    with pytest.raises(sqlite3.IntegrityError):
        record_bar(conn, "BTC", Candle(12 * HOUR, 1.0, 1.0, 1.0, 1.0, 1.0),
                   [WindowEvent("opened", replace(WINDOW, state="forming"))], None)  # fmt: skip
    assert load_bars(conn, "BTC") == []


def test_load_bars_returns_one_symbol_oldest_first(conn):
    for hour in (3, 1, 2):
        upsert_bar(conn, "BTC", Candle(hour * HOUR, 1.0, 1.0, 1.0, float(hour), 1.0))
    upsert_bar(conn, "ETH", Candle(HOUR, 1.0, 1.0, 1.0, 1.0, 1.0))
    assert [bar.close for bar in load_bars(conn, "BTC")] == [1.0, 2.0, 3.0]


def test_live_symbols_are_those_with_active_or_crossed_windows(conn):
    upsert_window(conn, WINDOW)  # BTC, active
    upsert_window(conn, replace(WINDOW, symbol="ETH", state="crossed"))
    upsert_window(conn, replace(WINDOW, symbol="SOL", state="hit"))
    assert live_symbols(conn) == {"BTC", "ETH"}


def test_forget_bars_keeps_the_windows(conn):
    candle = Candle(12 * HOUR, 1.0, 1.0, 1.0, 1.0, 1.0)
    record_bar(conn, "BTC", candle, [WindowEvent("opened", WINDOW)], None)
    forget_bars(conn, "BTC")
    assert load_bars(conn, "BTC") == []
    assert conn.execute("SELECT COUNT(*) FROM windows").fetchone()[0] == 1


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
    """What the runtime does (M5): a symbol's state, then the store — from a cold start."""
    state = SymbolState(symbol)
    for candle in candles:
        record_bar(conn, symbol, candle, state.on_bar(candle), None)


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


# --- the event log and provenance (PLAN D-1, D-2) -------------------------------------


def events(conn):
    return [dict(row) for row in conn.execute("SELECT * FROM window_events ORDER BY at, kind")]


def test_each_event_keeps_the_state_at_its_own_bar(conn):
    opened = WINDOW  # updated_at 12h, strength 55
    later = replace(WINDOW, updated_at=13 * HOUR, bars=3, strength=71.0, hist_pct=-0.2)
    for hour, close, event in [(12, 100.0, WindowEvent("opened", opened)),
                               (13, 101.0, WindowEvent("updated", later))]:
        record_bar(conn, "BTC", Candle(hour * HOUR, 1, 1, 1, close, 1), [event], 7)

    logged = events(conn)
    assert [(e["kind"], e["at"], e["strength"], e["bars"], e["close"]) for e in logged] == [
        ("opened", 12 * HOUR, 55.0, 2, 100.0),  # the state when it opened survives...
        ("updated", 13 * HOUR, 71.0, 3, 101.0),
    ]
    # ...though the window row itself moved on.
    assert conn.execute("SELECT strength FROM windows").fetchone()[0] == 71.0
    assert {e["run_id"] for e in logged} == {7}
    window_id = conn.execute("SELECT id FROM windows").fetchone()[0]
    assert {e["window_id"] for e in logged} == {window_id}


def test_replaying_an_event_never_writes_it_twice(conn):
    candle = Candle(12 * HOUR, 1, 1, 1, 100.0, 1)
    for _ in range(2):
        record_bar(conn, "BTC", candle, [WindowEvent("opened", WINDOW)], 1)
    assert len(events(conn)) == 1


def test_a_run_records_the_code_and_every_constant(conn):
    run_id = start_run(conn, 1_000, "abc123", {"MIN_PEAK_PCT": 0.05, "WEIGHTS": {"decay": 0.4}})
    row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    assert (row["started_at"], row["code_version"]) == (1_000, "abc123")
    assert json.loads(row["config"]) == {"MIN_PEAK_PCT": 0.05, "WEIGHTS": {"decay": 0.4}}
