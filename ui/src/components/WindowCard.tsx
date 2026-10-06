import type { ReactNode } from 'react'
import type { Band, Provisional, Regime, Side, Window } from '../api/types'
import {
  barsToCross,
  barTime,
  pct,
  POST_CROSS_BARS,
  sideColour,
  splitSymbol,
  toTarget,
  unwound,
} from '../lib/format'

interface Props {
  window: Window
  provisional: Provisional | undefined
  onOpen: (window: Window) => void
}

/** One live window. Every figure on it is stored data or arithmetic on stored data. */
export function WindowCard({ window: w, provisional, onOpen }: Props) {
  const { dex, market } = splitSymbol(w.symbol)
  // Only a reading from a bar *after* the last one applied says anything new.
  const forming = provisional && provisional.open_time > w.updated_at ? provisional : undefined
  return (
    <button
      onClick={() => onOpen(w)}
      className="w-full rounded-xl border border-line bg-card p-4 text-left transition hover:-translate-y-px hover:border-muted/60 hover:shadow-sm"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-baseline gap-2">
            <span className="text-lg font-semibold">{market}</span>
            {dex && <span className="text-xs text-muted">{dex}</span>}
          </div>
          <div className={`text-sm ${sideColour(w.side)}`}>
            {w.side === 'bullish' ? '↑' : '↓'} {w.side} <span className="text-muted">· {w.asset_class}</span>
          </div>
        </div>
        <StrengthRing value={w.strength} side={w.side} />
      </div>

      {w.state === 'active' ? <Contracting w={w} /> : <Following w={w} />}

      <div className="mt-3 flex flex-wrap gap-1.5">
        <Chip title={REGIME_MEANING[w.regime]}>{w.regime}</Chip>
        <Chip title="Where close sits against the Bollinger middle band, read in the window's direction">
          {bandWords(w.side, w.band)}
        </Chip>
        {w.line_turn && <Chip title="The MACD line itself is turning toward its signal line">MACD turning</Chip>}
      </div>

      {forming && <FormingLine w={w} forming={forming} />}
    </button>
  )
}

function Contracting({ w }: { w: Window }) {
  const share = Math.min(Math.max(unwound(w), 0), 1)
  return (
    <div className="mt-4">
      <div className="flex justify-between text-xs text-muted">
        <span>unwound since the peak</span>
        <span className="num text-ink">{Math.round(share * 100)}%</span>
      </div>
      <Meter share={share} side={w.side} />
      <div className="num mt-2 text-xs text-muted">
        {w.bars} shrink steps · ≈{barsToCross(w).toFixed(1)} bars to the cross at this pace
        <br />
        histogram {pct(w.hist_pct, 3)} of price, from a peak of {pct(w.peak_pct, 3)}
      </div>
    </div>
  )
}

function Following({ w }: { w: Window }) {
  const distance = toTarget(w.side, w.band_offset)
  return (
    <div className="mt-4">
      <div className="flex justify-between text-xs text-muted">
        <span>crossed {barTime(w.crossed_at)} — following</span>
        <span className="num text-ink">
          bar {w.bars_since_cross} of {POST_CROSS_BARS}
        </span>
      </div>
      <Meter share={w.bars_since_cross / POST_CROSS_BARS} side={w.side} muted />
      <div className="num mt-2 text-xs text-muted">
        target {distance > 0 ? `${distance.toFixed(2)} band-widths away` : 'reached on close'}
        <br />
        {w.bars_since_cross === 0 ? (
          'crossed this bar — no bars since the cross yet'
        ) : (
          <>
            best {pct(w.max_favourable_pct)} · worst{' '}
            {w.max_adverse_pct === null ? '—' : pct(-w.max_adverse_pct)} since the cross
          </>
        )}
      </div>
    </div>
  )
}

/** The forming bar, dimmed and labelled: it is displayed, never acted on (invariant 1). */
function FormingLine({ w, forming }: { w: Window; forming: Provisional }) {
  let words: string
  if (w.state === 'active') {
    const sameSide = Math.sign(forming.hist_pct) === Math.sign(w.hist_pct)
    words = !sameSide
      ? 'would cross if it closed now'
      : Math.abs(forming.hist_pct) < Math.abs(w.hist_pct)
        ? 'still shrinking'
        : 're-expanding'
  } else {
    const distance = toTarget(w.side, forming.band_offset)
    words = distance > 0 ? `target ${distance.toFixed(2)} band-widths away` : 'at the target'
  }
  return (
    <div className="mt-3 border-t border-dashed border-line pt-2 text-xs italic text-muted/80">
      forming bar, provisional: {words}
    </div>
  )
}

function Meter({ share, side, muted = false }: { share: number; side: Side; muted?: boolean }) {
  const fill = side === 'bullish' ? 'bg-bull' : 'bg-bear'
  return (
    <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-line">
      <div className={`h-full rounded-full ${fill} ${muted ? 'opacity-50' : ''}`} style={{ width: `${share * 100}%` }} />
    </div>
  )
}

export function StrengthRing({ value, side }: { value: number; side: Side }) {
  const radius = 17
  const circumference = 2 * Math.PI * radius
  return (
    <div className="relative h-11 w-11 shrink-0" title="Strength, 0–100: frozen at the cross as the prediction">
      <svg viewBox="0 0 44 44" className="h-11 w-11 -rotate-90">
        <circle cx="22" cy="22" r={radius} fill="none" className="stroke-line" strokeWidth="4" />
        <circle
          cx="22"
          cy="22"
          r={radius}
          fill="none"
          strokeWidth="4"
          strokeLinecap="round"
          className={side === 'bullish' ? 'stroke-bull' : 'stroke-bear'}
          strokeDasharray={`${(value / 100) * circumference} ${circumference}`}
        />
      </svg>
      <span className="num absolute inset-0 flex items-center justify-center text-sm font-semibold">
        {Math.round(value)}
      </span>
    </div>
  )
}

export function Chip({ children, title }: { children: ReactNode; title?: string }) {
  return (
    <span title={title} className="rounded-full border border-line px-2 py-0.5 text-xs text-muted">
      {children}
    </span>
  )
}

const REGIME_MEANING: Record<Regime, string> = {
  reversal: 'Both lines on the losing side of zero: a trend running out of steam',
  continuation: 'Both lines on the winning side of zero: a pullback inside a trend',
  transition: 'The lines straddle zero: directionless',
}

export function bandWords(side: Side, band: Band): string {
  const below = side === 'bullish' // a bullish move travels up through the middle band
  if (band === 'through') return 'through the middle band'
  if (band === 'near') return below ? 'just under the middle band' : 'just over the middle band'
  return below ? 'well below the middle band' : 'well above the middle band'
}
