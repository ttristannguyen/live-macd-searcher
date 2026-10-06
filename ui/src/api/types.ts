// Mirrors src/live_macd_searcher/web/models.py and detect/vocabulary.py. The words are
// the app's vocabulary (CLAUDE.md) — the UI uses them and only them.

export type Side = 'bullish' | 'bearish'
export type Regime = 'reversal' | 'continuation' | 'transition'
export type Band = 'far' | 'near' | 'through'
export type State = 'active' | 'crossed' | 'failed' | 'hit' | 'reversed' | 'expired'
export type AssetClass = 'crypto' | 'equity' | 'index' | 'commodity' | 'fx'
export type HealthStatus = 'starting' | 'ok' | 'stale' | 'failed'

export const TERMINAL: State[] = ['failed', 'hit', 'reversed', 'expired']

export interface Window {
  id: number
  symbol: string
  asset_class: AssetClass
  side: Side
  state: State
  started_at: number // ms, the peak bar's open
  peak_pct: number
  price_at_open: number
  bars: number
  regime: Regime
  line_turn: boolean
  strength: number
  updated_at: number
  hist_pct: number
  macd_pct: number
  signal_pct: number
  band: Band
  band_offset: number
  band_through_at: number | null
  crossed_at: number | null
  price_at_cross: number | null
  band_at_cross: Band | null
  bars_since_cross: number
  max_favourable_pct: number | null
  max_adverse_pct: number | null
  resolved_at: number | null
  price_at_resolve: number | null
}

export interface Board {
  status: HealthStatus
  as_of: number | null
  windows: Window[]
}

export interface SeriesBar {
  open_time: number
  open: number
  high: number
  low: number
  close: number
  volume: number
  macd: number
  signal: number
  hist: number
  middle: number | null
  upper: number | null
  lower: number | null
}

export interface Series {
  symbol: string
  bars: SeriesBar[]
}

export interface Health {
  status: HealthStatus
  reasons: string[]
  streaming: boolean
  last_message_age_s: number | null
  last_refresh_age_s: number | null
  newest_bar_age_s: number | null
  symbols: number
  warm: number
  warming: number
  rest_429s: number
  rest_weight_spent: number
  bars_corrected: number
}

/** A reading from the still-forming bar: displayed, never persisted, never acted on. */
export interface Provisional {
  symbol: string
  open_time: number
  close: number
  hist_pct: number
  macd_pct: number
  signal_pct: number
  band_offset: number
}
