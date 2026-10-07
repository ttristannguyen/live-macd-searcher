"""Rebuilding the event log of windows recorded before it existed (PLAN D-4)."""

import asyncio

import pytest

from live_macd_searcher.backfill import backfill_events
from live_macd_searcher.ingest.feed import FeedDisconnected
from live_macd_searcher.runtime import Runtime
from live_macd_searcher.store.db import connect
from tests.fakes import FakeFeed, Session, stream_hours, synthetic_history

HISTORY = {symbol: synthetic_history(700, phase) for phase, symbol in enumerate(["BTC", "ETH"])}
COMPARED = "window_id, kind, at, close, state, bars, strength, regime, band, band_offset, hist_pct"


@pytest.fixture
def conn(tmp_path):
    """A database the live runtime filled, event log included."""
    feed = FakeFeed(HISTORY, [Session(now_hour=450, messages=stream_hours(HISTORY, 450, 699))])
    runtime = Runtime(connect(tmp_path / "db.sqlite3"), feed, now_ms=feed.now_ms)

    async def go():
        await runtime.boot()
        with pytest.raises(FeedDisconnected):
            await runtime.ingest.run_session()

    asyncio.run(go())
    return runtime.conn


def log(conn, where="1"):
    rows = conn.execute(f"SELECT {COMPARED} FROM window_events WHERE {where}")
    return sorted(tuple(row) for row in rows)


def test_a_lost_log_is_rebuilt_exactly(conn):
    live = log(conn)
    assert len(live) > 50, "fixture should produce plenty of events"
    conn.execute("DELETE FROM window_events")  # as if recorded before D-1

    report = backfill_events(conn)

    assert report.inserted == len(live)
    assert log(conn) == live
    flags = conn.execute("SELECT DISTINCT reconstructed, run_id FROM window_events").fetchall()
    assert [tuple(row) for row in flags] == [(1, None)]  # marked, never mixed up with live
    assert report.mismatches == []


def test_live_rows_are_checked_and_kept_and_only_gaps_are_filled(conn):
    query = "SELECT at FROM window_events ORDER BY at LIMIT 1 OFFSET 40"
    cutoff = conn.execute(query).fetchone()[0]
    conn.execute("DELETE FROM window_events WHERE at < ?", (cutoff,))  # half the log lost
    kept = log(conn)

    report = backfill_events(conn)

    assert report.already_logged == len(kept)
    assert report.mismatches == []  # the replay agrees with every live row it overlaps
    assert log(conn, "reconstructed = 0") == kept  # live rows untouched


def test_a_disagreement_with_the_live_log_is_reported(conn):
    conn.execute("UPDATE window_events SET strength = strength + 1 WHERE id = 1")
    report = backfill_events(conn)
    assert len(report.mismatches) == 1


def test_every_window_row_is_checked_against_the_replay(conn):
    conn.execute("DELETE FROM window_events")  # nothing live to compare with...
    windows = conn.execute("SELECT COUNT(*) FROM windows").fetchone()[0]
    conn.execute("UPDATE windows SET strength = strength + 1 WHERE id = 1")  # ...but this

    report = backfill_events(conn)

    assert report.windows_checked == windows
    assert len(report.mismatches) == 1 and "window 1:" in report.mismatches[0]


def test_running_it_again_writes_nothing(conn):
    conn.execute("DELETE FROM window_events")
    backfill_events(conn)
    assert backfill_events(conn).inserted == 0
