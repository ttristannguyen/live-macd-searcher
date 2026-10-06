import { useEffect, useState } from 'react'
import type { Health } from '../api/types'
import { barTime, HOUR_MS } from '../lib/format'

interface Props {
  health: Health | null
  asOf: number | null
  connected: boolean
}

export function Header({ health, asOf, connected }: Props) {
  const watched = health ? health.symbols : null
  return (
    <header className="border-b border-line">
      <div className="mx-auto flex max-w-6xl flex-wrap items-end justify-between gap-6 px-5 py-7">
        <div className="max-w-2xl">
          <h1 className="font-serif text-4xl tracking-tight">Contraction Board</h1>
          <p className="mt-3 leading-relaxed text-muted">
            Watches {watched ?? 'the'} Hyperliquid perps on 1-hour bars for MACD histograms{' '}
            <span className="text-ink">shrinking toward zero</span> — momentum fading before a cross — and follows
            every one until the move is granted or denied.
          </p>
        </div>
        <div className="flex flex-col items-start gap-1.5 text-sm sm:items-end">
          <HealthPill health={health} connected={connected} />
          <span className="text-muted">
            as of the <span className="num text-ink">{barTime(asOf)}</span> bar
          </span>
          <NextClose />
        </div>
      </div>
    </header>
  )
}

const LOOK = {
  ok: { dot: 'bg-bull', label: 'Live' },
  starting: { dot: 'bg-accent', label: 'Warming up' },
  stale: { dot: 'bg-bear', label: 'Stale' },
  failed: { dot: 'bg-bear', label: 'Detector stopped' },
} as const

function HealthPill({ health, connected }: { health: Health | null; connected: boolean }) {
  // The page's own link to the server comes first: without it, nothing below is current.
  const look = !connected ? { dot: 'bg-bear', label: 'Reconnecting…' } : LOOK[health?.status ?? 'starting']
  const reasons = !connected ? ['lost the connection to the board; retrying'] : (health?.reasons ?? [])
  const live = connected && health?.status === 'ok'
  return (
    <div className="flex flex-col items-start sm:items-end" title={reasons.join('\n')}>
      <span className="flex items-center gap-2 rounded-full border border-line bg-card px-3 py-1 font-medium">
        <span className="relative flex h-2.5 w-2.5">
          {live && <span className={`absolute inline-flex h-full w-full animate-ping rounded-full ${look.dot} opacity-50`} />}
          <span className={`relative inline-flex h-2.5 w-2.5 rounded-full ${look.dot}`} />
        </span>
        {look.label}
        {health && health.warming > 0 && <span className="text-muted">· {health.warming} warming</span>}
      </span>
      {reasons.length > 0 && <span className="mt-1 max-w-xs text-xs text-bear sm:text-right">{reasons[0]}</span>}
    </div>
  )
}

/** A wall-clock countdown for the reader. The detector never uses it: a bar closes when
 *  the exchange sends the next one, so this is "about when", not "exactly when". */
function NextClose() {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [])
  const left = HOUR_MS - (now % HOUR_MS)
  const minutes = Math.floor(left / 60_000)
  const seconds = Math.floor((left % 60_000) / 1000)
  return (
    <span className="text-muted">
      next bar closes in{' '}
      <span className="num text-ink">
        {minutes}:{String(seconds).padStart(2, '0')}
      </span>
    </span>
  )
}
