"""Rebuild the event log of windows recorded before it existed (PLAN D-4).

Replays every stored bar of each symbol through a fresh `SymbolState`, from the earliest
— the same bar the runtime seeds from, under the same rules — so the events it produces
are the ones the live detector produced. They are written flagged `reconstructed`, with
no run id, and never over a row that was logged live.

Exactness is checked, not assumed, two ways: wherever a reconstructed event and a live
one share a key, every field is compared; and every window's stored row — its latest
state — must equal the last event the replay produced for it. Each disagreement is
reported. The second check is what vouches for windows that never had a live event.
"""

import sqlite3
from dataclasses import dataclass, field

from .detect.symbol_state import SymbolState
from .store.db import (
    EVENT_FIELDS,
    latest_state,
    load_bars,
    logged_event,
    record_event,
    symbols_with_windows,
    window_ids,
)


@dataclass
class BackfillReport:
    symbols: int = 0
    inserted: int = 0  # reconstructed rows written
    already_logged: int = 0  # events that had a live row: compared, left alone
    windows_checked: int = 0  # stored rows compared with the replay's last event
    mismatches: list[str] = field(default_factory=list)


def backfill_events(conn: sqlite3.Connection) -> BackfillReport:
    report = BackfillReport()
    for symbol in symbols_with_windows(conn):
        report.symbols += 1
        ids = window_ids(conn, symbol)
        state = SymbolState(symbol)
        last_seen: dict[int, tuple] = {}  # window id -> its latest replayed state
        with conn:  # one transaction per symbol
            for bar in load_bars(conn, symbol):
                for event in state.on_bar(bar):
                    window_id = ids.get(event.window.started_at)
                    if window_id is None:
                        continue  # a window the live run never stored: not ours to invent
                    w = event.window
                    last_seen[window_id] = (w.updated_at, *(getattr(w, f) for f in EVENT_FIELDS))
                    live = logged_event(conn, window_id, event.kind, event.window.updated_at)
                    if live is None:
                        record_event(conn, window_id, event, bar.close, None, reconstructed=True)
                        report.inserted += 1
                        continue
                    report.already_logged += 1
                    rebuilt = (bar.close, *(getattr(event.window, f) for f in EVENT_FIELDS))
                    if live != rebuilt:
                        report.mismatches.append(
                            f"{symbol} window {window_id} {event.kind} @ {event.window.updated_at}:"
                            f" live {tuple(live)} vs replay {rebuilt}"
                        )
        for window_id, replayed in last_seen.items():
            report.windows_checked += 1
            if latest_state(conn, window_id) != replayed:
                report.mismatches.append(
                    f"{symbol} window {window_id}: stored row {latest_state(conn, window_id)}"
                    f" vs replay {replayed}"
                )
    return report
