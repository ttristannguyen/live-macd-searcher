"""Response shapes. The vocabulary types come from `detect/vocabulary.py` — one definition,
from the detector to the API — so a value the detector can't produce can't be served."""

from typing import Literal

from pydantic import BaseModel

from ..detect.vocabulary import AssetClass, Band, Regime, Side, State

HealthStatus = Literal["starting", "ok", "stale", "failed"]


class WindowOut(BaseModel):
    id: int
    symbol: str
    asset_class: AssetClass
    side: Side
    state: State
    started_at: int
    peak_pct: float
    price_at_open: float
    bars: int
    regime: Regime
    line_turn: bool
    strength: float
    updated_at: int
    hist_pct: float
    macd_pct: float
    signal_pct: float
    band: Band
    band_offset: float
    band_through_at: int | None
    crossed_at: int | None
    price_at_cross: float | None
    band_at_cross: Band | None
    bars_since_cross: int
    max_favourable_pct: float | None
    max_adverse_pct: float | None
    resolved_at: int | None
    price_at_resolve: float | None


class Board(BaseModel):
    """The board, with the feed's health alongside — so stale windows are never served
    silently (PLAN M6)."""

    status: HealthStatus
    as_of: int | None  # open_time of the newest stored bar
    windows: list[WindowOut]


class SeriesBar(BaseModel):
    """One stored bar with its indicator values, recomputed from the stored bars exactly
    as the runtime computed them. Bands are null for the first 19 bars of history."""

    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float  # 0 on an hour with no trades, filled at the previous close
    macd: float
    signal: float
    hist: float
    middle: float | None
    upper: float | None
    lower: float | None


class Series(BaseModel):
    symbol: str
    bars: list[SeriesBar]


class WindowDetail(BaseModel):
    window: WindowOut
    trace: list[SeriesBar]  # peak bar through the latest bar applied
    trace_complete: bool  # false once retention has pruned some of its bars


class Health(BaseModel):
    status: HealthStatus
    reasons: list[str]  # why it isn't "ok", in plain words
    streaming: bool
    last_message_age_s: float | None
    last_refresh_age_s: float | None
    newest_bar_age_s: float | None
    symbols: int
    warm: int
    warming: int
    rest_429s: int
    rest_weight_spent: int
    bars_corrected: int  # websocket bars REST corrected before the detector saw them
