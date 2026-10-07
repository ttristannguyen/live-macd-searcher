# Build Plan

Milestones for [DESIGN.md](DESIGN.md), in dependency order. Tick boxes as they land.

**Now:** M9 — Run it permanently (soak) · M9b — Data for analysis

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

## M3 — Store ✅ (2026-10-05)

Blocked by: M2

- [x] `schema.sql` — `bars`, `windows` (with `asset_class`), indexes, `CHECK` constraints from DESIGN §7; `UNIQUE (symbol, started_at)` is a window's identity
- [x] Applied on boot; SQLite in WAL mode
- [x] `upsert_bar()` — idempotent on `(symbol, open_time)`
- [x] ~~`open_window()` / `update_window()` / `cross_window()` / `resolve_window()`~~ → one `upsert_window()`. A `Window` is a complete immutable snapshot, so one upsert on `(symbol, started_at)` covers every transition; a guard on `updated_at` stops an older snapshot overwriting a newer one
- [x] `prune_bars()` — bars more than `BAR_RETENTION_DAYS` (90) older than the newest stored bar; `windows` never pruned. *Nightly scheduling* is runtime wiring: M5
- [x] Test: replaying the same bar range twice yields identical windows and no duplicate rows — plus replaying an earlier part of the range rolls nothing back

**Done when:** replay is provably a no-op. This is the property that makes reconnects and
restarts safe, so it gets a test before anything depends on it. — met: 121 passed. Real
indicators and detector over 800 bars into SQLite, then a full replay and a partial one,
leave both tables byte-identical. Removing the `updated_at` guard turns the partial
replay test red (a full replay alone would not have caught it).

---

## M4 — Ingest (Hyperliquid) ✅ (2026-10-05)

Blocked by: M3. Reuses `macd_searcher/hyperliquid.py`'s request shapes, `growthMode` /
delisted filters, and retry-with-backoff on 429 and 5xx.

- [x] `MarketFeed` Protocol — `universe()`, `candles(symbol, start, end)` (REST), and ~~`stream(symbols)`~~ `connect(symbols)`: a context manager that returns only once Hyperliquid has *confirmed* every subscription, so a REST gap-fill started afterwards can't leave a hole
- [x] `FakeFeed` (`tests/fakes.py`) — one true price history served as REST (as of a session's `now_hour`) and as scripted websocket sessions; returns candles *overlapping* a range, as the real API does
- [x] REST pacer: every REST call — retries included — goes through it; never more than `REST_WEIGHT_PER_MIN` (weight 20 per call plus `candleSnapshot`'s per-60-items surcharge, rounded up)
- [x] Universe: `metaAndAssetCtxs` for core and `xyz`, not delisted, `growthMode` enabled, both liquidity floors. *Daily refresh, and keeping a symbol subscribed until its windows resolve, are scheduling: M5*
- [x] Backfill: exactly `BACKFILL_BARS` closed bars per symbol on a cold start (hour-aligned range)
- [x] Websocket: one connection, `candle` / `1h` subscription per symbol, ping every `WS_PING_SECONDS` whether or not data flows
- [x] Closedness: `CandleCloser` holds the latest snapshot and releases it only when a later `open_time` arrives; same-hour snapshots keep the larger volume, so REST and websocket can be mixed in any order; **closed bars only** reach `on_closed`
- [ ] ~~Refresh every `REFRESH_INTERVAL_MIN`~~ → M5. The closer holds the forming candles; the timer and `peek()` belong to `SymbolState`
- [x] Reconnect: subscribe (confirmed) and buffer → paced REST gap-fill from the hour that was forming at the drop → drain buffer; backoff 1 s doubling to `RECONNECT_MAX_BACKOFF_SECONDS`, reset after a healthy session. Only `FeedError` is retried — a bug propagates. *Re-seeding a symbol after an unfillable gap (> 5,000 h) → M5*
- [x] Out-of-order guard: the closer ignores closed hours; the detector (M2) ignores any bar not strictly after the last
- [x] `scripts/smoke_hyperliquid.py` — 30 s of live candles for one core and one `xyz:` symbol; `--warm` runs the live gate below
- [x] Test (`FakeFeed`): a disconnect mid-hour, with a stale message and overlapping gap-fill and buffered messages, gives the same bars and windows per symbol as an uninterrupted run
- [x] Test: the pacer never exceeds its budget in any minute (fake clock)

Probed on the live API 2026-10-05 (recorded in `ingest/hyperliquid.py` and DESIGN §6):
candle prices arrive as strings; there's no snapshot on subscribe; `xyz:` symbols work on
the websocket; one unlisted coin drops the *whole* connection; `candleSnapshot` returns
every candle overlapping the range; and an hour with no trades has no candle at all (D13).

**Done when:** `FakeFeed` drives the whole pipeline offline and deterministically, and the
live feed warms the full universe without a single window emitted during warm-up and
without a single REST 429. — met: 145 passed offline; eight planted ingest bugs each
turned a test red. Live (from a desktop IP, not the droplet's): 165 symbols (99 core,
66 `xyz`) warmed in 7.1 min at 600 weight/min, **0 REST 429s, 0 windows** with a peak
before warm-up ended. 162 symbols got exactly 400 bars; `xyz:HO` (newly listed, 98) stays
`warming`; `xyz:JPY` and `xyz:EWZ` got 399 — each missing an hour with no trades (D13).
An earlier run, with ranges not yet hour-aligned, showed one symbol at 402 closed bars;
the overlap behaviour explains 401, not 402, and it did not reproduce once aligned.

---

## M5 — Runtime wiring ✅ (2026-10-05)

Blocked by: M4

- [x] `SymbolState` (`detect/symbol_state.py`, pure) — MACD + Bollinger + `WindowDetector`, one per symbol; `on_bar()` commits (guarding the indicators against duplicate and out-of-order bars, not just the detector), `peek()` does not. No separate ring buffer: the bars live in SQLite. Replaced `tests/pipeline.py`
- [x] Refresh every `REFRESH_INTERVAL_MIN`: `peek()` each held forming candle into `Runtime.provisional`, never persisted (SSE broadcast is M7)
- [x] Daily job: `prune_bars()`, then the universe refresh. `universe()` now returns `tradeable` and `listed`; a symbol below the floors stays subscribed while it has live windows, but only while still listed; `Ingest.resubscribe()` applies a change by ending the session
- [x] `Ingest(last_stored=...)` from the `bars` table, so a restart resumes after the last stored bar and fills quiet hours from its close
- [x] Re-seed a symbol whose last stored bar is older than `BAR_RETENTION_DAYS` (the rule, not "> 5,000 h": splicing a fresh backfill onto old bars would seed the next boot's replay differently)
- [x] Boot sequence: universe → replay every stored bar per symbol from the earliest (bitwise-identical state) → feed resumes after the last stored bar
- [x] Single asyncio loop (`TaskGroup`: ingest, refresh, daily); `dict[str, SymbolState]` as the only shared state
- [x] Logging: boot (symbols, warm from stored bars), connect, gap-fill (bars, symbols fetched, seconds), disconnects, windows, universe changes, prunes
- [x] `record_bar()`: a bar and its window snapshots in one transaction, so a restart's replay writes nothing
- [x] `synchronous=NORMAL`: per-bar commits went from 28 ms to 0.04 ms on a spinning disk (DESIGN §7)
- [x] A reconnect or restart only fetches symbols for which an hour may have closed (`CLOCK_SKEW_MARGIN_SECONDS`): within the hour it streams again in seconds instead of re-fetching everything
- [x] `python -m live_macd_searcher` / `live-macd-searcher` — runs headless; M6 puts the same runtime inside the web app
- [x] Test: restart mid-run reproduces identical window rows

**Done when:** killing and restarting the process changes nothing about the stored windows.
— met: offline, a process killed mid-hour 583 with a BTC window `active` (it crosses during
the downtime and resolves after) and restarted at 587 leaves `bars` and `windows`
identical to a process that never stopped. Replaying only the last 300 or 450 stored bars
instead of all of them each turned that test red. 170 passed. Live (desktop): cold start
of 166 symbols in 7.1 min; killed and restarted twice — boot rebuilt 165–166 warm symbols
from stored bars in ~2 s and streamed again within ~9 s; at the 23:00 rollover, 162 of 167
symbols closed their bar from the stream within minutes, and 5 thin `xyz` markets waited
for their first trade (late, never early). No windows yet, correctly: a window needs two
post-warm-up shrink steps, so the first can open at the second rollover.

---

## M6 — API ✅ (2026-10-05)

Blocked by: M5

- [x] `web/models.py` — response shapes; `asset_class`, `side`, `regime`, `band`, `state` reuse the `Literal` types from `detect/vocabulary.py`; `WindowOut` held to the stored window's fields by a test
- [x] `web/queries.py` — the web layer's SQL, each query restating its own `SELECT`; every filter value a parameter
- [x] `GET /api/windows` with `state` / `asset_class` / `side` / `regime` / `band` (each repeatable) / `min_strength` / `min_bars` / `limit`; defaults to `active` + `crossed`, ranked by strength; carries the health `status` and `as_of`
- [x] `GET /api/windows/{id}` — window plus bar-by-bar trace from the peak through the latest bar applied, and `trace_complete` once retention prunes
- [x] `GET /api/symbols/{symbol}/series` — OHLC + Bollinger middle/upper/lower + macd/signal/hist, recomputed from all stored bars (`web/series.py`)
- [x] `GET /api/health` — `starting` / `ok` / `stale` / `failed` with reasons; streaming, last message and refresh ages, newest-bar age, warm vs warming, REST 429s and weight spent (`web/health.py`, pure)
- [x] The detector runs in the app's lifespan; if it stops, health says `failed` and the process exits non-zero for systemd to restart
- [x] Every request reads through a `mode=ro` connection: no endpoint can write
- [x] `live-macd-searcher` / `python -m live_macd_searcher` runs detector + web on `127.0.0.1:8001`
- [x] Test: bad `state`, `asset_class`, `side`, `regime`, or `band` returns 422 before any SQL runs — proved with a spy connection that records every `execute`

**Done when:** illegal input is impossible to express and health honestly reports a stale
feed rather than serving stale windows silently. — met: 203 passed. Five planted bugs
(vocabulary loosened to `str`, a writable web connection, a chart recomputed from recent
bars only, a stale bar not reported, a crashed detector ignored) each turned a test red.
Charted values equal the stored windows' to the last bit. Live: the real app on the
smoke database reported `starting` then `ok` within ~3 s of boot (gap-filling 7 of 169
symbols), served `xyz:GOLD` series, and answered a bad `side` with 422 — bound to
127.0.0.1 only.

---

## M7 — Stream ✅ (2026-10-06)

Blocked by: M6

- [x] `GET /api/stream` — SSE (`web/stream.py`); opens with a `: connected` comment so the stream starts at once through any proxy
- [x] Events: `window.opened`, `window.updated`, `window.crossed`, `window.resolved` (each carrying the row `id`, in `/api/windows`' shape), `tick.provisional` (one batch per refresh). Published only after the bar and its windows are stored
- [x] A dropped or slow client is disconnected, never allowed to block the detector — non-blocking put into a bounded per-client queue (`SSE_CLIENT_BACKLOG`); the intent is recorded at the `except QueueFull`
- [x] SSE comment heartbeat every `SSE_HEARTBEAT_SECONDS` (30), so idle connections survive the Tailscale proxy (DESIGN §13)
- [x] Test: killing a subscriber mid-stream leaves the detector running — a real uvicorn server, a stand-in detector publishing every 5 ms, a client that hangs up after three events
- [x] Stream tests bound every wait: a regression fails in seconds rather than hanging the suite (one did, during mutation testing, before this)

**Done when:** the detector's throughput is measurably unaffected by client count. — met:
benchmarked over the real closed-bar path (state, atomic write, row-id lookup, publish),
2 symbols x 1,400 bars, median of 3 — 0 clients 3,981 bars/s; 1 keeping up 97%; 10, 93%;
100, 84%; 10 or 100 stalled, 94–96% (dropped once their backlog fills). Real load is ~166
bars an hour. 209 passed; four planted bugs (a backed-up client kept, a closed stream
left subscribed, no keep-alive, windows announced before they are stored) each turned a
test red.

---

## M8 — UI ✅ (2026-10-06)

Blocked by: M7. Same toolchain as `macd_searcher` (React 18, Vite, TypeScript, Tailwind 3),
a deliberately different look: the **Contraction Board** — light "paper and ink" by
default, dark when the system asks — built to explain the app as much as to show it.
No `react-query` or chart library: the stream drives the data, and charts are SVG.

- [x] `ui/api/` — typed client, and `useLiveBoard()`: fetch on every (re)connect, then the stream; a refetch never undoes an event that arrived while it was in flight
- [x] `ui/components/` — header (purpose in one sentence, health, as-of bar, next close), "How a window works" in four steps, filters, window card (strength ring, "unwound since the peak" meter, bar *n* of 24, distance to target, excursions, chips), price panel with Bollinger Bands (target band heavier) and MACD panel, window shaded and cross marked on both; "Just resolved" strip (deliberately no hit rates); an error boundary so a render error says so instead of a blank page
- [x] `ui/pages/` — the board: a contracting lane and a following lane, each its strongest 8 with the rest a click away (a third of the universe can be shrinking at once); filters for asset class, side, regime, and band
- [x] Rows update in place from the stream; **nothing polls** — health arrives on the stream's heartbeat (`health` events, M7 extended), and `/api/windows?order=recent` feeds the resolved strip
- [x] On stream reconnect, refetch — events missed while disconnected are gone
- [x] Vite dev server proxies `/api` to `127.0.0.1:8001`; production build served same-origin by FastAPI from `ui/dist`
- [x] Provisional readings shown dimmed, labelled, and only when from a bar after the last one applied

**Done when:** the board runs for an hour without a manual refresh and without a single
row repainting its history. — met: the real page in headless Edge for 60 minutes across
the 14:00 rollover, sampled every 5 minutes — never reloaded, lane counts identical to the
API at all 12 samples (55/48 before the rollover, 46/55 after), the as-of bar advancing
12:00 → 1:00 pm on its own; 115 window events over 114 windows with **0** history
repaints (peaks fixed, bars/strength/regime frozen after the cross, states only forward,
nothing changed after resolving) and **0** page exceptions. Getting there took three
harness fixes, none in the app: the first hour's "blank page" was the harness navigating
to the URL "60" (its minutes argument, misread as the URL).

---

## M9 — Run it permanently (DigitalOcean droplet, beside `macd_searcher`)

Blocked by: M8. Follow DESIGN §13; every choice below copies `macd_searcher` on purpose.

- [x] `live-macd-searcher` console script in `pyproject.toml` — one command runs detector + web, takes `--host` / `--port` (M6)
- [x] `deploy/live-macd-searcher.service` — modelled on `macd_searcher/deploy/macd-searcher-web.service`: `User=tristan`, `WorkingDirectory=/home/tristan/live-macd-searcher`, `--host 127.0.0.1 --port 8001`, `Restart=on-failure`, install steps in the header comment
- [x] On the droplet: confirm port 8001 is free (`ss -ltnp`) and note what `tailscale serve status` shows *before* changing anything — 2026-10-04: 8001 free; serve has only 443 `/` → `127.0.0.1:8000`
- [ ] Check `free -h` and swap before the first `ui` build (1 GB droplet)
- [ ] Clone to `~/live-macd-searcher`, `uv sync`, build `ui/dist`, enable the unit
- [ ] `sudo tailscale serve --bg --https=8443 http://127.0.0.1:8001`; `tailscale serve status` shows the new handler and the existing one unchanged
- [x] README deployment section mirroring `macd_searcher`'s: install, update one-liner, restart, `journalctl -u live-macd-searcher -f`
- [x] Feed-staleness alarm (log-level is fine to start) — `HealthAlarm`: health judged every `HEALTH_CHECK_SECONDS`, each change logged, stale/failed as WARNING
- [x] Backup: `scripts/sync_prod_db.ps1` adapted from `macd_searcher` — SQLite online backup on the droplet, pulled to the desktop (written; first real run on the droplet)
- [ ] Check: the board loads at `https://<droplet>.<tailnet>.ts.net:8443/` and a window appears without a refresh
- [x] Check: nothing answers on the droplet's public IP at 8001 — 2026-10-06: `curl` from the desktop timed out after 5 s (dropped, not even refused)
- [ ] Check: `macd_searcher`'s page and cron still work, unaffected
- [ ] Check: `macd_searcher`'s `logs/scan.log` shows no more `attempt N failed` retry warnings in the week after deploy than the week before
- [ ] Check (one-off): a day of stored websocket bars matches `candleSnapshot` for every symbol (DESIGN §11 — if not, closed bars move to REST confirmation). `scripts/audit_bars.py`. **First run, desktop, 2026-10-06 — 6 h x 185 symbols: 1,109 agree, 1 differs** (ZEC 13:00, websocket-closed: close 1342.6 vs 1342.7, volume short by 0.02 — the hour's last trade missed). See D14
- [ ] 7-day soak: no memory growth, no missed bars, no unexplained `warming` symbols, zero REST 429s. Baseline on the desktop: ~57 MB resident with 185 symbols

**Done when:** it has survived a week unattended on the droplet, including at least one
restart of the service, is reachable over the tailnet and nowhere else, and
`macd_searcher` never noticed it arrived.

---

## M9b — Data for analysis

Blocked by: M9 deploy. M10 can only fit what was recorded, and none of this can be
backfilled once lost — so it lands before the data piles up. What M10 will ask, and what
it needs:

| M10 question | Needs |
| --- | --- |
| Does strength *at open* predict the outcome? | each window's state at every event, not just its latest |
| Did a retune help? | which rules (code + every constant) produced each window |
| How often does price hit the band anyway (the baseline)? Returns at fixed horizons? Re-test a gate? | bars, kept beyond 90 days |

- [x] **D-1 `window_events`** — append-only: one row per event (opened / updated / crossed / resolved) with the window's state at that bar: strength, regime, band, band offset, hist/macd/signal %, line turn, bars, bars since cross, excursions. Written in the same transaction as the bar; idempotent (a replayed event is ignored). Tested: state at each bar survives the window row moving on; replays never duplicate; a restart leaves the event log identical
- [x] **D-2 `runs`** — provenance: one row per boot with the code version (git commit) and every constant in `detect/config.py`; each event carries its `run_id`, so the *opened* event says which rules produced a window. The version is `git rev-parse`, marked `+dirty` if the checkout has local edits
- [ ] **D-3 `bar_archive`** — pruning *moves* bars older than `BAR_RETENTION_DAYS` instead of deleting them; boot still replays only `bars`, so restarts stay fast
- [ ] **D-4 backfill** — `scripts/backfill_events.py` rebuilds the events of windows recorded before D-1 by replaying stored bars (exact: the rules haven't changed since); marked as reconstructed, never mixed up with live rows
- [ ] **D-5 deploy** — on the droplet: pull, restart, backfill, and check the new tables fill at the next close

**Done when:** for every window, its whole journey can be read back exactly as it was at
each bar, under the rules that produced it, and no bar is ever deleted — each with a test
that would notice if it stopped being true.

---

## M10 — Outcomes (gated: do not start early)

Blocked by: M9 **and at least three weeks of stored windows.**

- [ ] `GET /api/stats/outcomes` — cross rate by `asset_class` x `side` x `regime` x `band` x `bars`; hit rate by `band_at_cross` and by whether `band_through_at` preceded the cross
- [ ] Compare realised cross and hit rates against the `strength` score
- [ ] Retune `WEIGHTS`, `REGIME_MULTIPLIER`, and `BAND_MULTIPLIER` from the data; record what changed and why
- [ ] Revisit D3, D6, D7, and D12 with evidence
- [ ] Does strength discriminate? On the first live board (2026-10-06) many windows scored 96–100: with decay high and persistence saturated, the product hits the clamp. Check the spread of stored strengths against outcomes before retuning
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
- [x] **D12 — Is `MIN_PEAK_PCT = 0.15` gating out evidence?** → _answer (2026-10-05):_ lowered to **0.05**. On 5,000 real 1h bars of six symbols with the gate off (~2,850 resolved windows), 0.15 dropped about half of all windows, yet hit and cross rates were flat across peak size (<0.05%: 30% hit; 0.15–0.25%: 26%; ≥0.5%: 32%). A gated window is never stored, so a high gate could never be corrected by the outcome data; 0.05 drops only the smallest ~15%. Revisit in M10 with the stored outcomes.
- [x] **D13 — Hours with no trades.** → _answer (2026-10-05):_ keep the previous hour's price — a flat bar at the previous close (open = high = low = close), zero volume. Not a copy of the previous candle, whose high and low would invent a range the target check and excursions would read. Done in `CandleCloser`, which also fills a gap after the last stored bar on restart (DESIGN §6).
- [x] **D14 — Confirm closed bars by REST?** → _answer (2026-10-06):_ yes. Every websocket-closed bar is fetched from REST before the detector sees it; REST's candle wins (DESIGN §6). Resumption now keys off the last bar *delivered*, so a failed confirmation loses nothing. Cost: ~3,900 weight an hour in our paced half, and windows move within minutes of the close instead of seconds. Watch: the overlap with `macd_searcher`'s 4-hourly scan (its retry count, M9); `bars_corrected` in health.
- [x] **D8 — Timeframe and cadence.** → _answer (2026-10-04):_ 1-hour bars, provisional refresh every 10 minutes. Only closed hourly bars move windows. Replaces the original 5-minute design. (First written as REST polling; moved to a websocket by D1.)
- [x] **D9 — What happens after a cross?** → _answer (2026-10-04):_ follow the window until it reaches the target band (`hit`), the histogram flips back (`reversed`), or `POST_CROSS_BARS` pass (`expired`).
- [x] **D10 — Bollinger Bands' role.** → _answer (2026-10-04):_ a first-class `band` field (`far` / `near` / `through` the middle band), filterable, and a score multiplier like regime. Not a gate.
- [x] **D11 — Where does it run?** → _answer (2026-10-04):_ the existing DigitalOcean droplet, beside `macd_searcher`, as a systemd unit on `127.0.0.1:8001`, reached over Tailscale at `:8443`. See DESIGN §13. The droplet is 1 vCPU / 1 GB in `syd1`. Shares its IP, and so Hyperliquid's per-IP rate limit, with `macd_searcher` — which is what shaped D1's websocket choice.
