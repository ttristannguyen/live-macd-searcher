import { useState, type ReactNode } from 'react'

const PREVIEW = 8 // at any moment a third of the universe may be shrinking; lead with the strongest

interface Props<T> {
  title: string
  explainer: string
  items: T[] // already sorted, strongest first
  render: (item: T) => ReactNode
  empty: string
}

/** A column of the board: one stage of a window's life, strongest first. */
export function Lane<T>({ title, explainer, items, render, empty }: Props<T>) {
  const [all, setAll] = useState(false)
  const shown = all ? items : items.slice(0, PREVIEW)
  const hidden = items.length - shown.length
  return (
    <section>
      <div className="flex items-baseline justify-between border-b border-line pb-2">
        <h2 className="font-serif text-2xl">{title}</h2>
        <span className="num text-sm text-muted">{items.length}</span>
      </div>
      <p className="mt-2 text-sm text-muted">{explainer}</p>
      <div className="mt-4 flex flex-col gap-3">
        {items.length === 0 && (
          <p className="rounded-xl border border-dashed border-line p-6 text-center text-sm text-muted">{empty}</p>
        )}
        {shown.map(render)}
        {(hidden > 0 || all) && items.length > PREVIEW && (
          <button
            onClick={() => setAll(!all)}
            className="rounded-xl border border-dashed border-line py-2.5 text-sm text-muted hover:border-muted hover:text-ink"
          >
            {all ? `Show the strongest ${PREVIEW}` : `Show all ${items.length} — ${hidden} more`}
          </button>
        )}
      </div>
    </section>
  )
}
