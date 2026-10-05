"""The web layer's SQL. Each query restates its own SELECT (CLAUDE.md, DRY with judgment).

Every filter value arrives already validated against the vocabulary by FastAPI, and is
passed as a parameter — never formatted into the SQL text.
"""

import sqlite3
from collections.abc import Sequence

from ..market import Candle


def board(
    conn: sqlite3.Connection,
    *,
    states: Sequence[str],
    asset_classes: Sequence[str] | None,
    sides: Sequence[str] | None,
    regimes: Sequence[str] | None,
    bands: Sequence[str] | None,
    min_strength: float,
    min_bars: int,
    limit: int,
) -> list[sqlite3.Row]:
    clauses, params = ["strength >= ?", "bars >= ?"], [min_strength, min_bars]
    for column, values in (
        ("state", states),
        ("asset_class", asset_classes),
        ("side", sides),
        ("regime", regimes),
        ("band", bands),
    ):
        if values:
            clauses.append(f"{column} IN ({', '.join('?' * len(values))})")
            params.extend(values)
    sql = f"""
        SELECT * FROM windows
        WHERE {" AND ".join(clauses)}
        ORDER BY strength DESC, updated_at DESC
        LIMIT ?
    """
    return conn.execute(sql, [*params, limit]).fetchall()


def window(conn: sqlite3.Connection, window_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM windows WHERE id = ?", (window_id,)).fetchone()


def bars(conn: sqlite3.Connection, symbol: str) -> list[Candle]:
    """Every stored bar for a symbol, oldest first — the indicators need all of them to
    reproduce the runtime's values exactly (DESIGN §6, "Restart")."""
    rows = conn.execute(
        """
        SELECT open_time, open, high, low, close, volume FROM bars
        WHERE symbol = ? ORDER BY open_time
        """,
        (symbol,),
    )
    return [Candle(*row) for row in rows]


def newest_bar_time(conn: sqlite3.Connection) -> int | None:
    return conn.execute("SELECT MAX(open_time) FROM bars").fetchone()[0]
