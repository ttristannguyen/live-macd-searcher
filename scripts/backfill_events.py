"""Rebuild the event log for windows recorded before it existed (PLAN D-4).

    uv run python scripts/backfill_events.py [--db PATH]

Safe beside the running app (one short transaction per symbol), and safe to run again:
events already logged — live or reconstructed — are never written twice or overwritten.
It replays stored bars from the earliest, exactly as the runtime does, and checks itself:
every live event it overlaps is compared field by field, and any disagreement is printed.
"""

import argparse
from pathlib import Path

from live_macd_searcher.backfill import backfill_events
from live_macd_searcher.store.db import connect

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=Path("state/live_macd_searcher.sqlite3"))
    report = backfill_events(connect(parser.parse_args().db))
    print(f"{report.symbols} symbols: {report.inserted} events reconstructed, "
          f"{report.already_logged} already logged live and checked, "
          f"{report.windows_checked} windows' stored state checked against the replay")  # fmt: skip
    for line in report.mismatches[:20]:
        print("  MISMATCH", line)
    print("VERDICT:", "replay matches the live log" if not report.mismatches
          else f"{len(report.mismatches)} mismatches: the replay is not exact")  # fmt: skip
