import type { AssetClass, Band, Regime, Side } from '../api/types'

export interface FilterState {
  side: Side | 'all'
  asset: AssetClass | 'all'
  regime: Regime | 'all'
  band: Band | 'all'
}

export const NO_FILTERS: FilterState = { side: 'all', asset: 'all', regime: 'all', band: 'all' }

interface Props {
  value: FilterState
  onChange: (next: FilterState) => void
}

export function Filters({ value, onChange }: Props) {
  const set = <K extends keyof FilterState>(key: K) => (next: FilterState[K]) => onChange({ ...value, [key]: next })
  return (
    <div className="flex flex-wrap gap-x-6 gap-y-3 text-sm">
      <Segmented label="Side" value={value.side} options={['all', 'bullish', 'bearish']} onChange={set('side')} />
      <Segmented
        label="Market"
        value={value.asset}
        options={['all', 'crypto', 'equity', 'index', 'commodity', 'fx']}
        onChange={set('asset')}
      />
      <Segmented
        label="Regime"
        value={value.regime}
        options={['all', 'reversal', 'continuation', 'transition']}
        onChange={set('regime')}
      />
      <Segmented label="Band" value={value.band} options={['all', 'far', 'near', 'through']} onChange={set('band')} />
    </div>
  )
}

const LABEL: Record<string, string> = { fx: 'FX' }

function Segmented<T extends string>(props: {
  label: string
  value: T
  options: T[]
  onChange: (next: T) => void
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-muted">{props.label}</span>
      <div className="flex rounded-lg border border-line bg-card p-0.5">
        {props.options.map((option) => (
          <button
            key={option}
            onClick={() => props.onChange(option)}
            className={`rounded-md px-2.5 py-1 transition ${
              option === props.value ? 'bg-ink text-paper' : 'text-muted hover:text-ink'
            }`}
          >
            {LABEL[option] ?? option[0].toUpperCase() + option.slice(1)}
          </button>
        ))}
      </div>
    </div>
  )
}
