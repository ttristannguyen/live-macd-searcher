# Build Plan

Milestones for [DESIGN.md](DESIGN.md), in dependency order. Tick boxes as they land.

**Now:** M3 — Store

Each milestone has a **Done when** line. That line is the gate: if it isn't true, the
milestone isn't finished, regardless of how much code exists. Don't start a milestone
before its blocker is ticked.

---

## M0 — Skeleton ✅ (2026-10-05)

- [x] `pyproject.toml` — project metadata, deps (`fastapi`, `uvicorn`, `httpx`, `websockets`), dev deps (`pytest`, `ruff`). `uv` + `hatchling` + `src/live_macd_searcher/`, as in `macd_searcher`
- [x] Package layout: `indicators/`, `detect/`, `store/`, `ingest/`, `web/`, `tests/`
- [x] `ruff` config matching the house style (100 cols, `E W F I B UP`); one `pytest` invocation runs everything
- [x] `README.md` — what it is, how to run it, pointer to `docs/`
- [x] `tests/test_layering.py` — enforces invariant 6 by reading `indicators/` and `detect/` with `ast`; also stops the suite from being empty (pytest exits 5 on zero tests, which isn't "clean")

**Done when:** `pytest` and `ruff check` both run clean on an empty suite. — met: 9 passed, ruff clean; the layering test fails on a planted `import sqlite3` in `detect/`.

---

## M1 — Indicators (pure) ✅ (2026-10-05)

Blocked by: M0

- [x] `ema(values, period)` — batch, for backfill seeding. Seeded with the first value, same recurrence as pandas `ewm(adjust=False)` and `macd_searcher`
- [x] `EmaState` — incremental `update(value) -> float`, O(1), for the live path
- [x] `macd(closes)` → `MacdPoint(macd, signal, hist)` and `MacdState`; 12/26/9 are module constants (the definition, not tuning)
- [x] `bollinger(closes, period, width)` → `Bands(middle, upper, lower)`, population stdev; `BollingerState` recomputes over the last `period` closes (no running sums to drift). `period`/`width` are parameters because their values live in `detect/config.py`
- [x] `peek(value)` on every incremental indicator — the forming-bar reading without committing state
- [x] Golden-vector tests against a fixed input series, asserted to 1e-9 — expected values from pandas 3.0.5, an independent implementation
- [x] Test: incremental output equals batch output over the same series
- [x] Test: `peek()` leaves state unchanged, and equals the next `update()` at the same price

**Done when:** the golden vectors pass and incremental matches batch exactly. Everything
above this layer is only as correct as this is — build it first and pin it hard. — met:
28 passed; planting a sample stdev or a 25-bar slow EMA turns the golden tests red.

---

## M2 — Detector (pure) ✅ (2026-10-05)

Blocked by: M1

- [x] `detect/config.py` — every constant in DESIGN §9, each with a *why* comment. Includes the strength weights and multipliers, so there is one tuning surface (DESIGN §3 updated to match)
- [x] Normalisation helpers — `hist_pct`, `macd_pct`, `signal_pct` (percent of close), `band_offset` (band widths; zero-width bands → `0`)
- [x] `classify_regime(side, macd, signal)` → `reversal` | `continuation` | `transition`
- [x] `classify_band(side, band_offset)` → `far` | `near` | `through`
- [x] `classify_asset(symbol)` → `crypto` | `equity` | `index` | `commodity` | `fx`, ported from `macd_searcher/classify.py` (unknown `xyz:` → `equity`)
- [x] Test: asset class mapping, including an unknown `xyz:` symbol
- [x] `detect/strength.py` — one pure `score()` function over the `config.py` weights; tested against hand-calculated values
- [x] `WindowDetector.step(BarReading)` → `list[WindowEvent]`, implementing the state machine in DESIGN §4 with two slots: contracting and following. A list, because one bar can cross one window and reverse another
- [x] Following after the cross: `hit` / `reversed` / `expired` checked in that order from the bar after the cross; `max_favourable_pct`, `max_adverse_pct`, `band_through_at` maintained; `strength` frozen
- [x] Noise gate: `peak_pct < MIN_PEAK_PCT` never publishes
- [x] Warm-up guard: emits nothing until the symbol has `BACKFILL_BARS` of history
- [x] Table-driven tests: hand-written histogram, price, and band sequences asserting open / extend / fail / cross / hit / reversed / expired
- [x] Test: re-expansion resolves `failed`; sign flip moves to `crossed`; `hist == 0` counts as a cross
- [x] Test: band classification at each threshold, both sides; close on the middle band is `near`
- [x] Test: target touched and histogram flipped on the same bar resolves `hit`; `hist == 0` after a cross resolves `reversed`
- [x] Test: an opposite-side window crossing resolves the followed window `reversed` on the same bar

**Done when:** the state machine is driven entirely by hand-written number sequences —
no fixtures, no DB, no clock — and every transition in DESIGN §4 has a test. — met:
108 passed. Seven planted bugs (hit/reversed order, zero not a cross back, no noise
gate, slot order, warm-up off by one, flat step as shrink, strength not frozen) each
turned a test red. Also run end to end over 5,000 real 1h bars for six symbols: see D12.

---

## M3 — Store

Blocked by: M2

- [ ] `schema.sql` — `bars`, `windows` (with `asset_class`), indexes, `CHECK` constraints from DESIGN §7
- [ ] Applied on boot; SQLite in WAL mode
- [ ] `upsert_bar()` — idempotent on `(symbol, open_time)`
- [ ] `open_window()` / `update_window()` / `cross_window()` / `resolve_window()` — `cross_window()` writes `crossed_at`, `price_at_cross`, `band_at_cross`; `resolve_window()` writes `resolved_at`, `price_at_resolve`, excursions
- [ ] Nightly prune of `bars` older than 90 days; `windows` never pruned
- [ ] Test: replaying the same bar range twice yields identical windows and no duplicate rows

**Done when:** replay is provably a no-op. This is the property that makes reconnects and
restarts safe, so it gets a test before anything depends on it.

---

## M4 — Ingest (Hyperliquid)

Blocked by: M3. D1 is decided (Hyperliquid), so nothing here waits on a decision.
`MarketFeed` + `FakeFeed` come first; the live client follows. Reuse what already works
in `macd_searcher/hyperliquid.py`: the request shapes, the `growthMode` / delisted
filters, and retry-with-backoff on 429 and 5xx.

- [ ] `MarketFeed` Protocol — `universe()`, `candles(symbol, start, end)` (REST), `stream(symbols)` (websocket candle messages)
- [ ] `FakeFeed` — scripted websocket messages and REST responses, no network
- [ ] REST pacer: every REST call goes through it; never more than `REST_WEIGHT_PER_MIN` (weight 20 per call plus `candleSnapshot`'s per-60-items surcharge)
- [ ] Universe: `metaAndAssetCtxs` for core and `xyz`, both liquidity floors, refreshed daily; a symbol leaving the universe stays subscribed until its live windows resolve
- [ ] Backfill: `BACKFILL_BARS` per symbol
- [ ] Websocket: one connection, `candle` / `1h` subscription per symbol, ping every `WS_PING_SECONDS`
- [ ] Closedness: a held candle closes when a later `open_time` arrives for that symbol; **closed bars only** reach the detector
- [ ] Refresh every `REFRESH_INTERVAL_MIN`: `peek()` the held forming candle, tag `provisional`, never persist
- [ ] Reconnect: subscribe and buffer → paced REST gap-fill since last processed `open_time` → drain buffer; re-seed to `warming` if the gap exceeds the ring buffer; backoff between attempts
- [ ] Out-of-order guard: ignore any bar whose `open_time` is not strictly greater than the last processed
- [ ] `scripts/smoke_hyperliquid.py` — subscribe to one core and one `xyz:` symbol and print a rollover, so a change in the live API shows up before a deploy, not after
- [ ] Test (`FakeFeed`): a disconnect mid-hour, with overlapping gap-fill and buffered messages, gives the same windows as an uninterrupted run
- [ ] Test: the pacer never exceeds its budget in any minute (fake clock)

**Done when:** `FakeFeed` drives the whole pipeline offline and deterministically, and the
live feed warms the full universe without a single window emitted during warm-up and
without a single REST 429.

---

## M5 — Runtime wiring

Blocked by: M4

- [ ] `SymbolState` — ring buffer + `EmaState` + Bollinger + `WindowDetector`, one per symbol; `on_bar()` commits, `peek()` does not
- [ ] Boot sequence: load `bars` → rebuild state → backfill the gap → reconcile `active` and `crossed` windows → stream goes live
- [ ] Single asyncio loop; `dict[str, SymbolState]` as the only shared state
- [ ] Structured logging: websocket connect/disconnect, gap-fill (symbols, bars, weight spent), window opened/crossed/resolved
- [ ] Test: restart mid-run reproduces identical window rows

**Done when:** killing and restarting the process changes nothing about the stored windows.

---

## M6 — API

Blocked by: M5

- [ ] `web/models.py` — response shapes; `asset_class`, `side`, `regime`, `band`, `state` as `Literal` types
- [ ] `web/queries.py` — all SQL, each query restating its own `SELECT`
- [ ] `GET /api/windows` with `state` / `asset_class` / `side` / `regime` / `band` / `min_strength` / `min_bars` / `limit`; defaults to `active` + `crossed`
- [ ] `GET /api/windows/{id}` — window plus bar-by-bar trace, through the cross to resolution
- [ ] `GET /api/symbols/{symbol}/series` — OHLC + Bollinger middle/upper/lower + macd/signal/hist
- [ ] `GET /api/health` — websocket connected and last message time, last refresh, warm vs warming counts, REST 429 count
- [ ] Test: bad `state`, `asset_class`, `side`, `regime`, or `band` returns 422 before any SQL runs

**Done when:** illegal input is impossible to express and health honestly reports a stale
feed rather than serving stale windows silently.

---

## M7 — Stream

Blocked by: M6

- [ ] `GET /api/stream` — SSE
- [ ] Events: `window.opened`, `window.updated`, `window.crossed`, `window.resolved`, `tick.provisional`
- [ ] A dropped or slow client is disconnected, never allowed to block the detector — with a comment at the `except` recording that intent
- [ ] SSE comment heartbeat every 30 s, so idle connections survive the Tailscale proxy (DESIGN §13)
- [ ] Test: killing a subscriber mid-stream leaves the detector running

**Done when:** the detector's throughput is measurably unaffected by client count.

---

## M8 — UI

Blocked by: M7

- [ ] `ui/api/` — typed client for the endpoints and the SSE stream
- [ ] `ui/components/` — window row, strength badge, regime chip, band chip, price panel with Bollinger Bands, MACD panel; window shaded and cross marked on both
- [ ] `ui/pages/` — the board: a contracting group (`active`, by strength) and a following group (`crossed`, with bars since cross, excursions, distance to target); filters for asset class, side, regime, and band
- [ ] Rows update in place from the stream; **nothing polls**
- [ ] On stream reconnect, refetch `/api/windows` — events missed while disconnected are gone
- [ ] Vite dev server proxies `/api` to `127.0.0.1:8001`; production build served same-origin by FastAPI from `ui/dist`
- [ ] Provisional readings shown dimmed and visibly distinct from confirmed ones

**Done when:** the board runs for an hour without a manual refresh and without a single
row repainting its history.

---

## M9 — Run it permanently (DigitalOcean droplet, beside `macd_searcher`)

Blocked by: M8. Follow DESIGN §13; every choice below copies `macd_searcher` on purpose.

- [ ] `live-macd-searcher` console script in `pyproject.toml` — one command runs detector + web, takes `--host` / `--port`
- [ ] `deploy/live-macd-searcher.service` — modelled on `macd_searcher/deploy/macd-searcher-web.service`: `User=tristan`, `WorkingDirectory=/home/tristan/live-macd-searcher`, `--host 127.0.0.1 --port 8001`, `Restart=on-failure`, install steps in the header comment
- [x] On the droplet: confirm port 8001 is free (`ss -ltnp`) and note what `tailscale serve status` shows *before* changing anything — 2026-10-04: 8001 free; serve has only 443 `/` → `127.0.0.1:8000`
- [ ] Check `free -h` and swap before the first `ui` build (1 GB droplet)
- [ ] Clone to `~/live-macd-searcher`, `uv sync`, build `ui/dist`, enable the unit
- [ ] `sudo tailscale serve --bg --https=8443 http://127.0.0.1:8001`; `tailscale serve status` shows the new handler and the existing one unchanged
- [ ] README deployment section mirroring `macd_searcher`'s: install, update one-liner, restart, `journalctl -u live-macd-searcher -f`
- [ ] Feed-staleness alarm (log-level is fine to start)
- [ ] Backup: `scripts/sync_prod_db.ps1` adapted from `macd_searcher` — SQLite online backup on the droplet, pulled to the desktop
- [ ] Check: the board loads at `https://<droplet>.<tailnet>.ts.net:8443/` and a window appears without a refresh
- [ ] Check: nothing answers on the droplet's public IP at 8001
- [ ] Check: `macd_searcher`'s page and cron still work, unaffected
- [ ] Check: `macd_searcher`'s `logs/scan.log` shows no more `attempt N failed` retry warnings in the week after deploy than the week before
- [ ] Check (one-off): a day of stored websocket bars matches `candleSnapshot` for every symbol (DESIGN §11 — if not, closed bars move to REST confirmation)
- [ ] 7-day soak: no memory growth, no missed bars, no unexplained `warming` symbols, zero REST 429s

**Done when:** it has survived a week unattended on the droplet, including at least one
restart of the service, is reachable over the tailnet and nowhere else, and
`macd_searcher` never noticed it arrived.

---

## M10 — Outcomes (gated: do not start early)

Blocked by: M9 **and at least three weeks of stored windows.**

- [ ] `GET /api/stats/outcomes` — cross rate by `asset_class` x `side` x `regime` x `band` x `bars`; hit rate by `band_at_cross` and by whether `band_through_at` preceded the cross
- [ ] Compare realised cross and hit rates against the `strength` score
- [ ] Retune `WEIGHTS`, `REGIME_MULTIPLIER`, and `BAND_MULTIPLIER` from the data; record what changed and why
- [ ] Revisit D3, D6, D7, and D12 with evidence
- [ ] Baseline for `hit`: how often does price touch the outer band within `POST_CROSS_BARS` from *any* bar? Without it, a hit rate can't be read as an edge (the M2 sample showed ~65% hit given a cross — meaningless until compared)
- [ ] Split `xyz` outcomes by whether the window opened while the venue was shut (from `started_at`); revisit the dropped market-hours guard (DESIGN §11) if those resolve noticeably worse

**Done when:** the section-3 weights are measurements rather than guesses. Starting this
before the data exists just produces a confident-looking chart of noise.

---

## Decisions log

Open questions from DESIGN §12. Tick when settled, and record the answer inline.

- [x] **D1 — Which market?** → _answer (2026-10-04):_ Hyperliquid — it's where the windows would be traded. Websocket for live bars so the per-IP REST budget stays `macd_searcher`'s; paced REST for universe, backfill, gap-fill.
- [x] **D2 — Universe?** → _answer (2026-10-04):_ core perps + `xyz`, `macd_searcher`'s liquidity floors ($300k day volume, $1M OI), refreshed daily — about 140 symbols. `xyz` treated exactly like crypto, around the clock — revised 2026-10-05 from an off-hours guard (DESIGN §11 records it and when to revisit).
- [ ] **D3 — Should `continuation` outrank `reversal`?** Currently no. Awaiting M10. → _answer:_
- [ ] **D4 — Alerting?** Currently out of scope; raises the bar on `MIN_PEAK_PCT` if yes. → _answer:_
- [ ] **D5 — `MIN_RUN_BARS` = 2 or 3?** 2 is earlier and noisier. Revisit after a week of live data. → _answer:_
- [ ] **D6 — Should `near` outrank `through`?** Currently `through` slightly. Awaiting M10. → _answer:_
- [ ] **D7 — Is `POST_CROSS_BARS = 24` the right horizon?** Awaiting M10. → _answer:_
- [ ] **D12 — Is `MIN_PEAK_PCT = 0.15` gating out evidence?** Evidence (2026-10-05, M1+M2 run over 5,000 real 1h bars of BTC, ETH, SOL, HYPE, xyz:TSLA, xyz:GOLD with the gate off, ~2,850 resolved windows): the gate drops about half of all windows (median abs(peak) 0.14%), yet hit and cross rates are flat across peak size — <0.05%: 30% hit; 0.15–0.25%: 26%; ≥0.5%: 32%. One sample, six symbols, no baseline yet. Options: keep 0.15, lower to ~0.05 (drops only the smallest ~15%), or remove. → _answer:_
- [x] **D8 — Timeframe and cadence.** → _answer (2026-10-04):_ 1-hour bars, provisional refresh every 10 minutes. Only closed hourly bars move windows. Replaces the original 5-minute design. (First written as REST polling; moved to a websocket by D1.)
- [x] **D9 — What happens after a cross?** → _answer (2026-10-04):_ follow the window until it reaches the target band (`hit`), the histogram flips back (`reversed`), or `POST_CROSS_BARS` pass (`expired`).
- [x] **D10 — Bollinger Bands' role.** → _answer (2026-10-04):_ a first-class `band` field (`far` / `near` / `through` the middle band), filterable, and a score multiplier like regime. Not a gate.
- [x] **D11 — Where does it run?** → _answer (2026-10-04):_ the existing DigitalOcean droplet, beside `macd_searcher`, as a systemd unit on `127.0.0.1:8001`, reached over Tailscale at `:8443`. See DESIGN §13. The droplet is 1 vCPU / 1 GB in `syd1`. Shares its IP, and so Hyperliquid's per-IP rate limit, with `macd_searcher` — which is what shaped D1's websocket choice.
