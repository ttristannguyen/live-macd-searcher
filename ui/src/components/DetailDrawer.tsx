import { useEffect, useState, type ReactNode } from 'react'
import { fetchSeries } from '../api/client'
import type { SeriesBar, Window } from '../api/types'
import { barTime, HOUR_MS, pct, price, sideColour, splitSymbol } from '../lib/format'
import { MacdChart, PriceChart } from './Charts'
import { bandWords, Chip, StrengthRing } from './WindowCard'

const CONTEXT_BARS = 36 // shown either side of the window, for the setup and the aftermath
const MAX_BARS = 2160 // all the bars retention keeps

interface Props {
  window: Window
  asOf: number | null
  onClose: () => void
}

const OUTCOME: Record<string, string> = {
  active: 'contracting — the cross hasn’t happened yet',
  crossed: 'crossed — following the move toward the target',
  hit: 'hit — price touched the target band: the move was granted',
  reversed: 'reversed — the histogram flipped back: the move was denied',
  expired: 'expired — 24 bars with neither: no verdict',
  failed: 'failed — the histogram re-expanded before crossing',
}

export function DetailDrawer({ window: w, asOf, onClose }: Props) {
  const [bars, setBars] = useState<SeriesBar[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  useEffect(() => {
    // Enough bars to reach back past the peak, from the newest stored bar.
    const newest = asOf ?? w.updated_at
    const needed = Math.ceil((newest - w.started_at) / HOUR_MS) + CONTEXT_BARS + 1
    setBars(null)
    fetchSeries(w.symbol, Math.min(needed, MAX_BARS))
      .then((series) => {
        const end = w.resolved_at ?? w.updated_at
        const from = w.started_at - CONTEXT_BARS * HOUR_MS
        const to = end + CONTEXT_BARS * HOUR_MS
        setBars(series.bars.filter((b) => b.open_time >= from && b.open_time <= to))
      })
      .catch((e: Error) => setError(e.message))
  }, [w.id, w.symbol, w.started_at, w.updated_at, w.resolved_at, asOf])

  const { dex, market } = splitSymbol(w.symbol)
  return (
    <div className="fixed inset-0 z-20 flex justify-end bg-ink/30" onClick={onClose}>
      <aside
        className="h-full w-full max-w-3xl overflow-y-auto border-l border-line bg-paper p-6 shadow-2xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="flex items-baseline gap-2">
              <h2 className="font-serif text-3xl">{market}</h2>
              {dex && <span className="text-muted">{dex}</span>}
            </div>
            <p className={`mt-1 ${sideColour(w.side)}`}>
              {w.side === 'bullish' ? '↑' : '↓'} {w.side} window · <span className="text-muted">{OUTCOME[w.state]}</span>
            </p>
          </div>
          <div className="flex items-center gap-3">
            <StrengthRing value={w.strength} side={w.side} />
            <button onClick={onClose} className="rounded-lg border border-line px-3 py-1 text-sm text-muted hover:text-ink">
              Close
            </button>
          </div>
        </div>

        <div className="mt-4 flex flex-wrap gap-1.5">
          <Chip>{w.regime}</Chip>
          <Chip>{bandWords(w.side, w.band)}</Chip>
          {w.line_turn && <Chip>MACD turning</Chip>}
          <Chip>{w.asset_class}</Chip>
        </div>

        <dl className="mt-5 grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-4">
          <Fact label="Peak">{barTime(w.started_at)}</Fact>
          <Fact label="Opened at">{price(w.price_at_open)}</Fact>
          <Fact label="Crossed">{barTime(w.crossed_at)}</Fact>
          <Fact label="Price at cross">{price(w.price_at_cross)}</Fact>
          <Fact label="Shrink steps">{w.bars}</Fact>
          <Fact label="Peak histogram">{pct(w.peak_pct, 3)}</Fact>
          <Fact label="Best since cross">{pct(w.max_favourable_pct)}</Fact>
          <Fact label="Worst since cross">{w.max_adverse_pct === null ? '—' : pct(-w.max_adverse_pct)}</Fact>
          {w.resolved_at !== null && <Fact label="Resolved">{barTime(w.resolved_at)}</Fact>}
          {w.price_at_resolve !== null && <Fact label="Price at resolve">{price(w.price_at_resolve)}</Fact>}
          {w.band_at_cross !== null && <Fact label="Band at cross">{bandWords(w.side, w.band_at_cross)}</Fact>}
          <Fact label="Through the middle">{barTime(w.band_through_at)}</Fact>
        </dl>

        <section className="mt-6 rounded-xl border border-line bg-card p-3">
          {error && <p className="text-sm text-bear">Couldn’t load the bars: {error}</p>}
          {!error && !bars && <p className="p-6 text-center text-sm text-muted">Loading bars…</p>}
          {bars && bars.length > 0 && (
            <>
              <Legend>
                Price, with Bollinger Bands — <span className="text-accent">the heavier band is the target</span>
              </Legend>
              <PriceChart bars={bars} window={w} />
              <Legend>
                MACD histogram, with the <span className="text-ink">MACD</span> and{' '}
                <span className="text-accent">signal</span> lines — the window’s bars are solid
              </Legend>
              <MacdChart bars={bars} window={w} />
            </>
          )}
          {bars && bars.length === 0 && (
            <p className="p-6 text-center text-sm text-muted">These bars are older than the 90 days kept.</p>
          )}
        </section>
        <p className="mt-3 text-xs text-muted">
          Indicator values are recomputed from the stored bars — exactly the numbers the detector saw. Faded
          candles are hours with no trades, filled at the previous close.
        </p>
      </aside>
    </div>
  )
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="num mt-0.5">{children}</dd>
    </div>
  )
}

function Legend({ children }: { children: ReactNode }) {
  return <p className="px-2 pb-1 pt-2 text-xs text-muted">{children}</p>
}
