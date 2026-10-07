"""SQLite persistence: the bars the indicators saw, and every window they produced.

Each write commits itself. Persistence is not best-effort here (CLAUDE.md): the stored
windows are the outcome record, so a failed write raises to the caller rather than
being logged and dropped.
"""

import json
import sqlite3
from dataclasses import astuple, fields
from pathlib import Path

from ..detect.config import BAR_RETENTION_DAYS
from ..detect.window import Window, WindowEvent
from ..market import Candle

SCHEMA = Path(__file__).with_name("schema.sql")
DAY_MS = 86_400_000

# The `windows` columns are exactly `Window`'s fields plus `id`; a test holds the
# schema to that, so the column list is written once, here, from the dataclass.
_WINDOW_COLUMNS = [field.name for field in fields(Window)]
_WINDOW_IDENTITY = ("symbol", "started_at")

_UPSERT_WINDOW = f"""
    INSERT INTO windows ({", ".join(_WINDOW_COLUMNS)})
    VALUES ({", ".join(["?"] * len(_WINDOW_COLUMNS))})
    ON CONFLICT (symbol, started_at) DO UPDATE SET
        {", ".join(f"{c} = excluded.{c}" for c in _WINDOW_COLUMNS if c not in _WINDOW_IDENTITY)}
    WHERE excluded.updated_at >= windows.updated_at
"""


# The window's state captured with each event (PLAN D-1): everything that can change
# from bar to bar. Identity and the frozen-at-open fields live on the `windows` row.
EVENT_FIELDS = (
    "state", "bars", "strength", "regime", "band", "band_offset", "hist_pct", "macd_pct",
    "signal_pct", "line_turn", "bars_since_cross", "max_favourable_pct", "max_adverse_pct",
)  # fmt: skip

_INSERT_EVENT = f"""
    INSERT OR IGNORE INTO window_events
        (window_id, run_id, kind, at, close, reconstructed, {", ".join(EVENT_FIELDS)})
    VALUES (?, ?, ?, ?, ?, ?, {", ".join(["?"] * len(EVENT_FIELDS))})
"""

_UPSERT_BAR = """
    INSERT INTO bars (symbol, open_time, open, high, low, close, volume)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (symbol, open_time) DO UPDATE SET
        open = excluded.open, high = excluded.high, low = excluded.low,
        close = excluded.close, volume = excluded.volume
"""


def connect(path: str | Path) -> sqlite3.Connection:
    """Open the database and apply the schema. Safe to call on every boot."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    # WAL lets the web layer read while the detector writes, without either blocking.
    conn.execute("PRAGMA journal_mode=WAL")
    # Commits stay atomic, but stop waiting on the disk: ~28 ms -> 0.04 ms per bar on a
    # spinning disk, the difference between a 31-minute and an instant cold start. The
    # trade is the last few commits on a power loss or OS crash (an app crash loses
    # nothing). Safe here, and exact: the next boot gap-fills from the last bar that
    # survived, and the replay re-derives the very same windows.
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    return conn


def record_bar(
    conn: sqlite3.Connection,
    symbol: str,
    candle: Candle,
    events: list[WindowEvent],
    run_id: int | None,
) -> list[int]:
    """Store a closed bar, every window snapshot it produced, and their event-log rows,
    in one transaction. Returns each event's window id, in order.

    Atomic on purpose: a crash can never leave a bar stored without its windows and
    events. That is what lets a restart rebuild state by replaying stored bars without
    writing anything — whatever they produced is already here.
    """
    with conn:
        conn.execute(_UPSERT_BAR, (symbol, *candle))
        ids = []
        for event in events:
            conn.execute(_UPSERT_WINDOW, astuple(event.window))
            ids.append(window_id(conn, symbol, event.window.started_at))
            record_event(conn, ids[-1], event, candle.close, run_id)
    return ids


def record_event(
    conn: sqlite3.Connection,
    window_id: int,
    event: WindowEvent,
    close: float,
    run_id: int | None,
    *,
    reconstructed: bool = False,
) -> None:
    """One `window_events` row. The caller owns the transaction. A repeat is ignored."""
    window = event.window
    snapshot = [getattr(window, field) for field in EVENT_FIELDS]
    conn.execute(
        _INSERT_EVENT,
        (window_id, run_id, event.kind, window.updated_at, close, int(reconstructed), *snapshot),
    )


def start_run(conn: sqlite3.Connection, started_at: int, code_version: str, config: dict) -> int:
    """Record this boot's provenance; returns the run id every event will carry."""
    with conn:
        cursor = conn.execute(
            "INSERT INTO runs (started_at, code_version, config) VALUES (?, ?, ?)",
            (started_at, code_version, json.dumps(config, sort_keys=True)),
        )
    return cursor.lastrowid


def upsert_bar(conn: sqlite3.Connection, symbol: str, candle: Candle) -> None:
    """Store one closed bar. Writing the same bar again is a no-op (DESIGN §6)."""
    with conn:
        conn.execute(_UPSERT_BAR, (symbol, *candle))


def upsert_window(conn: sqlite3.Connection, window: Window) -> None:
    """Store a window's latest snapshot, keyed by `(symbol, started_at)`.

    A snapshot older than the stored one (smaller `updated_at`) is ignored, so replaying
    any range of bars — after a reconnect, or from scratch after a restart — can never
    roll a window back, and replaying the same range twice changes nothing.
    """
    with conn:
        conn.execute(_UPSERT_WINDOW, astuple(window))


def window_id(conn: sqlite3.Connection, symbol: str, started_at: int) -> int:
    """The stored row id of a window, by its identity — what the stream sends the UI."""
    row = conn.execute(
        "SELECT id FROM windows WHERE symbol = ? AND started_at = ?", (symbol, started_at)
    ).fetchone()
    return row[0]


def load_bars(conn: sqlite3.Connection, symbol: str) -> list[Candle]:
    """Every stored bar for a symbol, oldest first: what a restart replays."""
    rows = conn.execute(
        "SELECT open_time, open, high, low, close, volume FROM bars"
        " WHERE symbol = ? ORDER BY open_time",
        (symbol,),
    )
    return [Candle(*row) for row in rows]


def forget_bars(conn: sqlite3.Connection, symbol: str) -> None:
    """Take a symbol's bars out of the replay set, to re-seed it from scratch. They move
    to `bar_archive`, never deleted; its windows are kept."""
    with conn:
        _archive(conn, "symbol = ?", (symbol,))


def live_symbols(conn: sqlite3.Connection) -> set[str]:
    """Symbols with a window still `active` or `crossed`: they must stay subscribed."""
    rows = conn.execute("SELECT DISTINCT symbol FROM windows WHERE state IN ('active', 'crossed')")
    return {row[0] for row in rows}


def prune_bars(conn: sqlite3.Connection) -> int:
    """Move bars more than `BAR_RETENTION_DAYS` older than the newest stored bar into
    `bar_archive` (PLAN D-3). Nothing is deleted; `bars` just stays the size a restart
    can replay quickly.

    Measured from the newest bar rather than the wall clock: exchange time is the only
    clock (invariant 2). Windows are never pruned. Returns the number of bars moved.
    """
    with conn:
        newest = conn.execute("SELECT MAX(open_time) FROM bars").fetchone()[0]
        if newest is None:
            return 0
        return _archive(conn, "open_time < ?", (newest - BAR_RETENTION_DAYS * DAY_MS,))


def _archive(conn: sqlite3.Connection, where: str, params: tuple) -> int:
    """Move the `bars` rows matching `where` into `bar_archive`. The caller owns the
    transaction, so a bar is always in exactly one of the two tables."""
    conn.execute(f"INSERT OR IGNORE INTO bar_archive SELECT * FROM bars WHERE {where}", params)
    return conn.execute(f"DELETE FROM bars WHERE {where}", params).rowcount
