import type { State, Window } from '../api/types'
import { barTime, pct, sideColour, splitSymbol } from '../lib/format'

const OUTCOME_LOOK: Partial<Record<State, string>> = {
  hit: 'bg-bull/15 text-bull',
  reversed: 'bg-bear/15 text-bear',
  expired: 'bg-line text-muted',
  failed: 'bg-line text-muted',
}

interface Props {
  windows: Window[]
  onOpen: (window: Window) => void
}

/** The outcome record as it is written. Deliberately no hit rates: a rate means nothing
 *  until weeks of windows exist to compare against a baseline (PLAN M10). */
export function ResolvedStrip({ windows, onOpen }: Props) {
  return (
    <section className="mx-auto max-w-6xl px-5 pb-12 pt-10">
      <div className="border-b border-line pb-2">
        <h2 className="font-serif text-2xl">Just resolved</h2>
      </div>
      <p className="mt-2 text-sm text-muted">
        Every window is followed to an outcome — this is the record the strength scores will be checked
        against. No hit rates yet: those mean something only after weeks of data, against a baseline.
      </p>
      {windows.length === 0 ? (
        <p className="mt-4 rounded-xl border border-dashed border-line p-6 text-center text-sm text-muted">
          Nothing has resolved yet.
        </p>
      ) : (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-muted">
              <tr>
                <th className="py-2 pr-4 font-normal">Outcome</th>
                <th className="py-2 pr-4 font-normal">Market</th>
                <th className="py-2 pr-4 text-right font-normal">Strength</th>
                <th className="py-2 pr-4 font-normal">Regime</th>
                <th className="py-2 pr-4 text-right font-normal">Best / worst after cross</th>
                <th className="py-2 font-normal">Resolved</th>
              </tr>
            </thead>
            <tbody>
              {windows.map((w) => {
                const { market } = splitSymbol(w.symbol)
                return (
                  <tr key={w.id} onClick={() => onOpen(w)} className="cursor-pointer border-t border-line hover:bg-card">
                    <td className="py-2 pr-4">
                      <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${OUTCOME_LOOK[w.state] ?? ''}`}>{w.state}</span>
                    </td>
                    <td className="py-2 pr-4">
                      {market} <span className={sideColour(w.side)}>{w.side === 'bullish' ? '↑' : '↓'}</span>
                    </td>
                    <td className="num py-2 pr-4 text-right">{Math.round(w.strength)}</td>
                    <td className="py-2 pr-4 capitalize text-muted">{w.regime}</td>
                    <td className="num py-2 pr-4 text-right text-muted">
                      {w.crossed_at === null
                        ? 'never crossed'
                        : `${pct(w.max_favourable_pct)} / ${w.max_adverse_pct === null ? '—' : pct(-w.max_adverse_pct)}`}
                    </td>
                    <td className="num py-2 text-muted">{barTime(w.resolved_at)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
