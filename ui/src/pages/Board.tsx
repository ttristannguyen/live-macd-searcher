import { useCallback, useMemo, useState } from 'react'
import { useLiveBoard } from '../api/live'
import type { Window } from '../api/types'
import { DetailDrawer } from '../components/DetailDrawer'
import { Filters, NO_FILTERS, type FilterState } from '../components/Filters'
import { Header } from '../components/Header'
import { HowItWorks } from '../components/HowItWorks'
import { Lane } from '../components/Lane'
import { ResolvedStrip } from '../components/ResolvedStrip'
import { WindowCard } from '../components/WindowCard'

function matches(w: Window, f: FilterState): boolean {
  return (
    (f.side === 'all' || w.side === f.side) &&
    (f.asset === 'all' || w.asset_class === f.asset) &&
    (f.regime === 'all' || w.regime === f.regime) &&
    (f.band === 'all' || w.band === f.band)
  )
}

const byStrength = (a: Window, b: Window) => b.strength - a.strength

export function Board() {
  const board = useLiveBoard()
  const [filters, setFilters] = useState<FilterState>(NO_FILTERS)
  const [open, setOpen] = useState<Window | null>(null)
  const close = useCallback(() => setOpen(null), [])

  const shown = useMemo(() => board.live.filter((w) => matches(w, filters)), [board.live, filters])
  const contracting = shown.filter((w) => w.state === 'active').sort(byStrength)
  const following = shown.filter((w) => w.state === 'crossed').sort(byStrength)
  const card = (w: Window) => (
    <WindowCard key={w.id} window={w} provisional={board.provisional.get(w.symbol)} onOpen={setOpen} />
  )
  // A drawer that is open stays current as its window updates in place.
  const openNow = open ? (board.live.find((w) => w.id === open.id) ?? board.recent.find((w) => w.id === open.id) ?? open) : null

  return (
    <div className="min-h-screen">
      <Header health={board.health} asOf={board.asOf} connected={board.connected} />
      <HowItWorks />

      <main className="mx-auto max-w-6xl px-5 pt-8">
        <Filters value={filters} onChange={setFilters} />
        <div className="mt-8 grid gap-10 lg:grid-cols-2">
          <Lane
            title="Contracting"
            explainer="The setup: histograms shrinking toward zero, strongest first. Each could cross on a coming close."
            items={contracting}
            render={card}
            empty="No window is contracting right now. One opens when a histogram shrinks for two closed bars after a peak."
          />
          <Lane
            title="Following the move"
            explainer="After the cross: watching up to 24 bars for price to reach the target band."
            items={following}
            render={card}
            empty="Nothing is being followed right now. A window lands here the bar its histogram crosses."
          />
        </div>
      </main>

      <ResolvedStrip windows={board.recent} onOpen={setOpen} />
      {openNow && <DetailDrawer window={openNow} asOf={board.asOf} onClose={close} />}
    </div>
  )
}
