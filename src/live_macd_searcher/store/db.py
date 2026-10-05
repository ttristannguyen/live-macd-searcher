"""SQLite persistence: the bars the indicators saw, and every window they produced.

Each write commits itself. Persistence is not best-effort here (CLAUDE.md): the stored
windows are the outcome record, so a failed write raises to the caller rather than
being logged and dropped.
"""

import sqlite3
from dataclasses import astuple, fields
from pathlib import Path

from ..detect.config import BAR_RETENTION_DAYS
from ..detect.window import Window
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


def connect(path: str | Path) -> sqlite3.Connection:
    """Open the database and apply the schema. Safe to call on every boot."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    # WAL lets the web layer read while the detector writes, without either blocking.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    return conn


def upsert_bar(conn: sqlite3.Connection, symbol: str, candle: Candle) -> None:
    """Store one closed bar. Writing the same bar again is a no-op (DESIGN §6)."""
    with conn:
        conn.execute(
            """
            INSERT INTO bars (symbol, open_time, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (symbol, open_time) DO UPDATE SET
                open = excluded.open, high = excluded.high, low = excluded.low,
                close = excluded.close, volume = excluded.volume
            """,
            (symbol, *candle),
        )


def upsert_window(conn: sqlite3.Connection, window: Window) -> None:
    """Store a window's latest snapshot, keyed by `(symbol, started_at)`.

    A snapshot older than the stored one (smaller `updated_at`) is ignored, so replaying
    any range of bars — after a reconnect, or from scratch after a restart — can never
    roll a window back, and replaying the same range twice changes nothing.
    """
    with conn:
        conn.execute(_UPSERT_WINDOW, astuple(window))


def prune_bars(conn: sqlite3.Connection) -> int:
    """Delete bars more than `BAR_RETENTION_DAYS` older than the newest stored bar.

    Measured from the newest bar rather than the wall clock: exchange time is the only
    clock (invariant 2). Windows are never pruned. Returns the number of bars deleted.
    """
    with conn:
        cursor = conn.execute(
            "DELETE FROM bars WHERE open_time < (SELECT MAX(open_time) FROM bars) - ?",
            (BAR_RETENTION_DAYS * DAY_MS,),
        )
    return cursor.rowcount
