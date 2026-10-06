"""Every number that could reasonably be argued about, with why it has that value.

The one tuning surface (DESIGN §9). Change a value here, never inline.
"""

from .vocabulary import Band, Regime

# --- detection --------------------------------------------------------------------

# Shrink steps before a run is published as a window. 2 = earlier and noisier,
# 3 = cleaner and later (PLAN D5). Two steps is two hours after the extreme.
MIN_RUN_BARS = 2

# Noise gate: a run whose peak |hist| never reached this percent of close is never
# published — a histogram wiggling around zero is noise. Kept low on purpose (PLAN D12):
# on 5,000 real 1h bars for six symbols, small-peak windows crossed and hit as often as
# large ones, and 0.15 discarded half of them. A gated window is never stored, so the
# outcome data could never correct a high gate; 0.05 drops only the smallest ~15%.
MIN_PEAK_PCT = 0.05

# Band widths below the middle band (read in the window's direction) that still count
# as `near`. 0.10 of a 4-sigma band is within 0.4 sigma of the 20-bar mean.
NEAR_MIDDLE_BAND = 0.10

# Closed bars a crossed window is followed for before resolving `expired`. One day of
# hourly bars: long enough for a 1-hour cross to play out, short enough that the outcome
# still belongs to this cross rather than the market's next idea. A guess (PLAN D7).
POST_CROSS_BARS = 24

# The standard bands, so the board agrees with any chart you check it against.
BOLLINGER_PERIOD = 20
BOLLINGER_WIDTH = 2.0

# History before a symbol is warm. EMA(26) has alpha ~0.074, so seeding error decays by
# ~e^-0.077 per bar and is below float noise after ~300 bars; the signal EMA needs its
# input converged first. 400 leaves margin and is one candleSnapshot request.
BACKFILL_BARS = 400

# --- strength (DESIGN §3). Opinions until the outcome data says otherwise. ---------

WEIGHTS = {"decay": 0.40, "persistence": 0.25, "proximity": 0.35}

# Catching turns early is the point, so reversals score highest (PLAN D3).
REGIME_MULTIPLIER: dict[Regime, float] = {
    "reversal": 1.00,
    "continuation": 0.85,
    "transition": 0.70,
}

# Price confirming the turn is worth slightly more than earliness (PLAN D6).
BAND_MULTIPLIER: dict[Band, float] = {"through": 1.00, "near": 0.95, "far": 0.80}

# The MACD line itself turning toward the signal line separates a real turn from the
# signal line drifting onto a flat MACD — the most informative binary in the score.
LINE_TURN_BONUS = 1.10

# Bars (hours) after which a longer run stops adding confidence: a 15-hour grind is not
# more convincing than a 6-hour one, just slower.
PERSISTENCE_SATURATES_AT = 6

# --- storage (DESIGN §7) -----------------------------------------------------------

# Bars older than this, counted back from the newest stored bar, are pruned. Far longer
# than warm-up needs (~17 days), and long enough to keep the bar-by-bar trace of any
# window worth looking back at. Windows themselves are never pruned: they are the
# evidence base.
BAR_RETENTION_DAYS = 90

# --- feed and universe (DESIGN §5, §6) ---------------------------------------------

# Provisional refresh cadence. Closed-bar logic is hourly regardless.
REFRESH_INTERVAL_MIN = 10

# The same floors macd_searcher uses, so both boards cover the same tradeable set
# (about 140 symbols). A thin book makes MACD meaningless.
MIN_DAY_VOLUME_USD = 300_000
MIN_OPEN_INTEREST_USD = 1_000_000

# Half of Hyperliquid's 1,200-per-minute per-IP REST budget. The other half belongs to
# macd_searcher, which shares the droplet's IP.
REST_WEIGHT_PER_MIN = 600

# Hyperliquid drops a websocket that has sent nothing for 60 seconds.
WS_PING_SECONDS = 50

# How long to wait for Hyperliquid to confirm every subscription before treating the
# connection as failed. Confirmations arrive within a second in practice.
WS_SUBSCRIBE_TIMEOUT_SECONDS = 30

# Attempts per REST request on a 429, a 5xx, or a network error, backing off 1 s, 2 s
# between them, as macd_searcher does. Every attempt is paced, so retrying can never
# push past REST_WEIGHT_PER_MIN.
REST_ATTEMPTS = 3
REST_TIMEOUT_SECONDS = 15

# A reconnect or restart skips REST for a symbol when no hour has closed since its last
# bar: the websocket resends the forming hour's full candle on its own. That decision
# reads the wall clock, so it is only trusted this long after the hour turns; nearer
# the boundary a slightly slow clock could mistake a closed hour for the forming one,
# so it fetches. The droplet's clock is NTP-synced to well under a second.
CLOCK_SKEW_MARGIN_SECONDS = 60

# Health (DESIGN §8). The feed is stale if no websocket message has arrived for this
# long — BTC alone trades every few seconds, so two minutes of silence means trouble —
# or if the newest closed bar is older than this many hours: one closes every hour, so
# two allows for a slow close without hiding a stopped one.
FEED_STALE_SECONDS = 120
BAR_STALE_HOURS = 2

# The live stream (DESIGN §8, §13). A comment every 30 s keeps an idle connection open
# through the Tailscale proxy: between refreshes the stream can be quiet for 10 minutes.
SSE_HEARTBEAT_SECONDS = 30
# Events a browser may fall behind before it is dropped rather than waited on. An hourly
# rollover is a few hundred events at most, so this is hours of backlog — a client this
# far behind is stuck, and its browser reconnects and refetches the board on its own.
SSE_CLIENT_BACKLOG = 1000

# Reconnect backoff doubles from 1 s up to this, so an exchange outage is retried about
# once a minute rather than hammered.
RECONNECT_MAX_BACKOFF_SECONDS = 60
