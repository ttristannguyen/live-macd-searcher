"""The API (PLAN M6): illegal input can't be expressed, values match the detector exactly,
and health reports a stale or failed feed instead of serving stale windows silently."""

import logging
import sqlite3
import time
from dataclasses import fields

import pytest
from fastapi.testclient import TestClient

from live_macd_searcher.detect.config import BAR_STALE_HOURS, FEED_STALE_SECONDS
from live_macd_searcher.detect.normalise import pct_of_close
from live_macd_searcher.detect.symbol_state import SymbolState
from live_macd_searcher.detect.window import Window
from live_macd_searcher.ingest.feed import HOUR_MS
from live_macd_searcher.runtime import RuntimeStatus
from live_macd_searcher.store.db import connect, record_bar
from live_macd_searcher.web.app import create_app, read_only
from live_macd_searcher.web.health import HealthAlarm, judge
from live_macd_searcher.web.models import WindowOut
from tests.fakes import synthetic_history

SYMBOLS = ["BTC", "xyz:GOLD"]
HOURS = 700

STREAMING = RuntimeStatus(
    booted=True, streamed=True, streaming=True, last_message_at=None, last_refresh_at=None,
    symbols=2, warm=2, rest_429s=0, rest_weight_spent=0,
)  # fmt: skip


@pytest.fixture(scope="module")
def db_path(tmp_path_factory):
    """A database the real pipeline filled: indicators, detector, store."""
    path = tmp_path_factory.mktemp("web") / "db.sqlite3"
    conn = connect(path)
    for phase, symbol in enumerate(SYMBOLS):
        state = SymbolState(symbol)
        for candle in synthetic_history(HOURS, phase):
            record_bar(conn, symbol, candle, state.on_bar(candle), None)
    conn.close()
    return path


@pytest.fixture(scope="module")
def client(db_path):
    with TestClient(create_app(db_path, lambda: STREAMING)) as test_client:
        yield test_client


def all_windows(db_path) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn.execute("SELECT * FROM windows").fetchall()


def test_the_response_model_matches_the_stored_window_exactly():
    assert set(WindowOut.model_fields) == {"id"} | {f.name for f in fields(Window)}


# --- the board ---------------------------------------------------------------------


def test_the_board_defaults_to_live_windows_ranked_by_strength(client, db_path):
    board = client.get("/api/windows").json()
    live = [row for row in all_windows(db_path) if row["state"] in ("active", "crossed")]
    assert live, "fixture should leave some windows live"
    assert {w["id"] for w in board["windows"]} == {row["id"] for row in live}
    strengths = [w["strength"] for w in board["windows"]]
    assert strengths == sorted(strengths, reverse=True)
    assert board["as_of"] == (HOURS - 1) * HOUR_MS


@pytest.mark.parametrize(
    ("query", "check"),
    [
        ("state=hit&state=failed", lambda w: w["state"] in ("hit", "failed")),
        ("state=hit&side=bullish", lambda w: w["state"] == "hit" and w["side"] == "bullish"),
        ("state=failed&asset_class=commodity", lambda w: w["symbol"] == "xyz:GOLD"),
        ("state=hit&min_strength=50", lambda w: w["strength"] >= 50),
        ("state=failed&min_bars=4", lambda w: w["bars"] >= 4),
    ],
)
def test_filters(client, query, check):
    windows = client.get(f"/api/windows?{query}").json()["windows"]
    assert windows, f"fixture should match {query}"
    assert all(check(w) for w in windows)


def test_order_recent_puts_the_latest_news_first(client):
    windows = client.get("/api/windows?state=hit&state=failed&order=recent").json()["windows"]
    updated = [w["updated_at"] for w in windows]
    assert updated == sorted(updated, reverse=True)


def test_an_unknown_order_is_rejected(client):
    assert client.get("/api/windows?order=random").status_code == 422


def test_limit(client):
    assert len(client.get("/api/windows?state=failed&limit=3").json()["windows"]) == 3


class SpyConnection:
    """Stands in for the database and records any SQL that reaches it."""

    executed: list[str] = []

    def execute(self, sql, *args):
        self.executed.append(sql)
        raise AssertionError("SQL ran")


@pytest.mark.parametrize("param", ["state", "asset_class", "side", "regime", "band"])
def test_a_value_outside_the_vocabulary_is_rejected_before_any_sql_runs(db_path, param):
    app = create_app(db_path, lambda: STREAMING)
    spy = SpyConnection()
    app.dependency_overrides[read_only] = lambda: spy
    with TestClient(app) as test_client:
        response = test_client.get(f"/api/windows?{param}=crossed-the-middle")
    assert response.status_code == 422
    assert spy.executed == []


@pytest.mark.parametrize("query", ["min_strength=101", "min_strength=-1", "limit=0", "min_bars=-1"])
def test_out_of_range_numbers_are_rejected(client, query):
    assert client.get(f"/api/windows?{query}").status_code == 422


# --- one window, and series ---------------------------------------------------------


def test_window_detail_traces_the_peak_through_the_latest_bar(client, db_path):
    row = next(r for r in all_windows(db_path) if r["state"] == "hit")
    detail = client.get(f"/api/windows/{row['id']}").json()
    trace = detail["trace"]
    assert detail["trace_complete"]
    assert trace[0]["open_time"] == row["started_at"]
    assert trace[-1]["open_time"] == row["updated_at"]
    assert len(trace) == (row["updated_at"] - row["started_at"]) // HOUR_MS + 1


def test_charted_values_are_exactly_what_the_detector_saw(client, db_path):
    # Recomputed from stored bars, the series must agree with the stored windows to the
    # last bit — or a chart could disagree with the board.
    for row in all_windows(db_path):
        last = client.get(f"/api/windows/{row['id']}").json()["trace"][-1]
        assert pct_of_close(last["hist"], last["close"]) == row["hist_pct"]
        assert pct_of_close(last["macd"], last["close"]) == row["macd_pct"]


def test_an_unknown_window_is_404(client):
    assert client.get("/api/windows/999999").status_code == 404


def test_series_returns_the_last_n_bars_with_indicators(client):
    series = client.get("/api/symbols/BTC/series?bars=50").json()
    assert len(series["bars"]) == 50
    assert series["bars"][-1]["open_time"] == (HOURS - 1) * HOUR_MS
    assert all(bar["upper"] > bar["middle"] > bar["lower"] for bar in series["bars"])


def test_series_shows_no_bands_before_a_full_period(client):
    bars = client.get(f"/api/symbols/BTC/series?bars={HOURS}").json()["bars"]
    assert [bar["middle"] for bar in bars[:19]] == [None] * 19
    assert bars[19]["middle"] is not None


def test_an_xyz_symbol_works_in_the_path(client):
    assert client.get("/api/symbols/xyz:GOLD/series?bars=5").status_code == 200


def test_an_unknown_symbol_is_404(client):
    assert client.get("/api/symbols/NOPE/series").status_code == 404


# --- health ------------------------------------------------------------------------

NOW = 10_000_000.0
FRESH_BAR = int((NOW - 1800) * 1000) - HOUR_MS  # closed half an hour ago


def status(**changes) -> RuntimeStatus:
    base = {**STREAMING.__dict__, "last_message_at": NOW - 5}
    return RuntimeStatus(**(base | changes))


def test_health_is_ok_when_streaming_and_recent():
    health = judge(status(), None, FRESH_BAR, NOW)
    assert (health.status, health.reasons) == ("ok", [])


def test_health_is_starting_until_the_first_gap_fill_finishes():
    assert judge(status(streamed=False, streaming=False), None, None, NOW).status == "starting"


@pytest.mark.parametrize(
    ("changes", "newest_bar", "reason"),
    [
        ({"streaming": False}, FRESH_BAR, "reconnecting"),
        ({"last_message_at": NOW - FEED_STALE_SECONDS - 1}, FRESH_BAR, "no websocket message"),
        ({}, FRESH_BAR - BAR_STALE_HOURS * HOUR_MS, "newest closed bar"),
    ],
)
def test_health_says_stale_and_why(changes, newest_bar, reason):
    health = judge(status(**changes), None, newest_bar, NOW)
    assert health.status == "stale"
    assert any(reason in r for r in health.reasons)


def test_health_reports_a_stopped_detector_as_failed():
    health = judge(status(), "ValueError('bug')", FRESH_BAR, NOW)
    assert health.status == "failed"
    assert "ValueError" in health.reasons[0]


def test_the_board_carries_the_same_status_as_health(client):
    # The fixture's bars are from 1970: the honest answer is stale, on both endpoints.
    assert client.get("/api/health").json()["status"] == "stale"
    assert client.get("/api/windows").json()["status"] == "stale"


# --- guardrails --------------------------------------------------------------------


def test_web_connections_cannot_write(db_path):
    app = create_app(db_path, lambda: STREAMING)

    class FakeRequest:
        pass

    request = FakeRequest()
    request.app = app
    connections = read_only(request)
    conn = next(connections)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        conn.execute("DELETE FROM windows")
    connections.close()


def test_a_crashed_detector_is_reported_and_stops_the_process(db_path):
    stopped: list[BaseException] = []

    async def crashing_detector():
        raise ValueError("a bug in the detector")

    app = create_app(db_path, lambda: STREAMING, run=crashing_detector, on_failure=stopped.append)
    with TestClient(app) as test_client:
        for _ in range(50):  # the task fails on the app's own loop; give it a moment
            if stopped:
                break
            time.sleep(0.01)
        health = test_client.get("/api/health").json()
    assert isinstance(stopped[0], ValueError)
    assert health["status"] == "failed"


# --- the staleness alarm ------------------------------------------------------------


def test_the_alarm_logs_each_change_of_health_once(caplog):
    alarm = HealthAlarm()
    ok = judge(status(), None, FRESH_BAR, NOW)
    stale = judge(status(streaming=False), None, FRESH_BAR, NOW)
    with caplog.at_level(logging.INFO, logger="live_macd_searcher.web.health"):
        for health in (ok, ok, stale, stale, stale, ok):
            alarm.check(health)
    assert [(r.levelname, r.getMessage()) for r in caplog.records] == [
        ("INFO", "health ok"),
        ("WARNING", "health stale: reconnecting to the feed"),
        ("INFO", "health ok"),
    ]
