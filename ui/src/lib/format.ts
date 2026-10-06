import type { Side, Window } from '../api/types'

export const HOUR_MS = 3_600_000
export const POST_CROSS_BARS = 24 // mirrors detect/config.py; shown, never decided here

export const pct = (value: number | null, digits = 2) =>
  value === null ? '—' : `${value > 0 ? '+' : ''}${value.toFixed(digits)}%`

export const price = (value: number | null) => {
  if (value === null) return '—'
  const digits = value >= 1000 ? 1 : value >= 1 ? 3 : 5
  return value.toLocaleString(undefined, { maximumFractionDigits: digits })
}

/** An hourly bar, in the viewer's own time: "Tue 14:00". */
export const barTime = (openTime: number | null) =>
  openTime === null
    ? '—'
    : new Date(openTime).toLocaleString(undefined, { weekday: 'short', hour: '2-digit', minute: '2-digit' })

/** `xyz:TSLA` → the market `TSLA`, on the `xyz` DEX. Core perps have no DEX. */
export function splitSymbol(symbol: string): { dex: string | null; market: string } {
  const at = symbol.indexOf(':')
  return at === -1 ? { dex: null, market: symbol } : { dex: symbol.slice(0, at), market: symbol.slice(at + 1) }
}

/** How much of the momentum has unwound since the peak, 0..1 — strength's `decay`. */
export const unwound = (w: Window) => 1 - w.hist_pct / w.peak_pct

/** Bars until the cross if the average shrink rate so far continues — strength's maths. */
export const barsToCross = (w: Window) => w.hist_pct / ((w.peak_pct - w.hist_pct) / w.bars)

/** Distance from the latest close to the target band, in band widths (0 = touching). */
export function toTarget(side: Side, bandOffset: number): number {
  const toward = side === 'bullish' ? bandOffset : -bandOffset
  return 0.5 - toward
}

export const sideColour = (side: Side) => (side === 'bullish' ? 'text-bull' : 'text-bear')
