-- Applied on every boot; every statement is idempotent. DESIGN §7.

CREATE TABLE IF NOT EXISTS bars (
  symbol     TEXT    NOT NULL,
  open_time  INTEGER NOT NULL,          -- ms, exchange clock, bar OPEN
  open       REAL    NOT NULL,
  high       REAL    NOT NULL,
  low        REAL    NOT NULL,
  close      REAL    NOT NULL,
  volume     REAL    NOT NULL,
  PRIMARY KEY (symbol, open_time)
);

CREATE TABLE IF NOT EXISTS windows (
  id                 INTEGER PRIMARY KEY,
  symbol             TEXT    NOT NULL,  -- as Hyperliquid names it: 'BTC', 'xyz:TSLA'
  asset_class        TEXT    NOT NULL CHECK (asset_class IN ('crypto','equity','index',
                                                             'commodity','fx')),
  side               TEXT    NOT NULL CHECK (side   IN ('bullish','bearish')),
  regime             TEXT    NOT NULL CHECK (regime IN ('reversal','continuation','transition')),
  band               TEXT    NOT NULL CHECK (band   IN ('far','near','through')),
  state              TEXT    NOT NULL CHECK (state  IN ('active','crossed','failed',
                                                         'hit','reversed','expired')),
  started_at         INTEGER NOT NULL,  -- open_time of the peak bar
  updated_at         INTEGER NOT NULL,  -- open_time of the latest closed bar applied
  crossed_at         INTEGER,           -- open_time of the bar where hist changed sign
  resolved_at        INTEGER,
  bars               INTEGER NOT NULL,  -- shrink steps; stops counting at the cross
  peak_pct           REAL    NOT NULL,
  hist_pct           REAL    NOT NULL,
  macd_pct           REAL    NOT NULL,
  signal_pct         REAL    NOT NULL,
  band_offset        REAL    NOT NULL,  -- latest; band widths from the middle, side-agnostic
  band_at_cross      TEXT    CHECK (band_at_cross IN ('far','near','through')),
  band_through_at    INTEGER,           -- first close through the middle band, if any
  line_turn          INTEGER NOT NULL,
  strength           REAL    NOT NULL,  -- frozen at the cross
  price_at_open      REAL    NOT NULL,
  price_at_cross     REAL,
  price_at_resolve   REAL,              -- close of the resolving bar
  bars_since_cross   INTEGER NOT NULL DEFAULT 0,  -- drives `expired`; needed to resume after restart
  max_favourable_pct REAL,              -- after the cross, % of price_at_cross
  max_adverse_pct    REAL,
  -- A window's identity. Two runs on one symbol can never share a peak bar: a new run
  -- only starts after the last one ended. Upserting on this makes replay a no-op.
  UNIQUE (symbol, started_at)
);

CREATE INDEX IF NOT EXISTS windows_live ON windows(state, strength DESC);

-- Provenance (PLAN D-2): one row per boot — the code and every tunable that produced
-- what this run writes. Without it, a stored strength means nothing once weights change.
CREATE TABLE IF NOT EXISTS runs (
  id           INTEGER PRIMARY KEY,
  started_at   INTEGER NOT NULL,        -- ms, wall clock: provenance only, never bar logic
  code_version TEXT    NOT NULL,        -- git commit, or 'unknown'
  config       TEXT    NOT NULL         -- JSON: every constant in detect/config.py
);

-- Each window's journey (PLAN D-1): append-only, one row per event, holding the state
-- *at that bar*. `windows` keeps only the latest; this keeps what it was when it opened.
CREATE TABLE IF NOT EXISTS window_events (
  id                 INTEGER PRIMARY KEY,
  window_id          INTEGER NOT NULL REFERENCES windows(id),
  run_id             INTEGER REFERENCES runs(id),  -- null only on reconstructed rows
  kind               TEXT    NOT NULL CHECK (kind IN ('opened','updated','crossed','resolved')),
  at                 INTEGER NOT NULL,  -- open_time of the bar that produced it
  close              REAL    NOT NULL,  -- that bar's close
  state              TEXT    NOT NULL,
  bars               INTEGER NOT NULL,
  strength           REAL    NOT NULL,
  regime             TEXT    NOT NULL,
  band               TEXT    NOT NULL,
  band_offset        REAL    NOT NULL,
  hist_pct           REAL    NOT NULL,
  macd_pct           REAL    NOT NULL,
  signal_pct         REAL    NOT NULL,
  line_turn          INTEGER NOT NULL,
  bars_since_cross   INTEGER NOT NULL,
  max_favourable_pct REAL,
  max_adverse_pct    REAL,
  reconstructed      INTEGER NOT NULL DEFAULT 0,  -- 1: rebuilt by replay (PLAN D-4)
  -- An event's identity: replaying a bar can never write it twice.
  UNIQUE (window_id, kind, at)
);
