import { useState, type ReactNode } from 'react'

const STORAGE_KEY = 'howItWorksOpen'

function remembered(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) !== 'no'
  } catch {
    return true // private window or blocked storage: just show it
  }
}

/** The lifecycle in four steps. Its drawings are schematic — the only ones on the page
 *  that aren't data. */
export function HowItWorks() {
  const [open, setOpen] = useState(remembered)
  const toggle = () => {
    setOpen(!open)
    try {
      localStorage.setItem(STORAGE_KEY, open ? 'no' : 'yes')
    } catch {
      // Remembering is a nicety; without it the panel simply starts open.
    }
  }

  return (
    <section className="mx-auto max-w-6xl px-5 pt-6">
      <button onClick={toggle} className="text-sm text-muted hover:text-ink">
        {open ? '▾' : '▸'} How a window works
      </button>
      {open && (
        <ol className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Step n={1} title="Contracting" glyph={<ShrinkingGlyph />}>
            After a <b>peak</b>, the MACD histogram shrinks bar after bar without changing sign. Two
            shrink steps open a <b>window</b>: momentum is fading.
          </Step>
          <Step n={2} title="Crossed" glyph={<CrossGlyph />}>
            The histogram changes sign — the cross the window anticipated. Its <b>strength</b> is frozen
            here: that number is the prediction.
          </Step>
          <Step n={3} title="Following" glyph={<FollowGlyph />}>
            For up to 24 bars, watch for price to touch the <b>target</b>: the outer Bollinger band on the
            far side.
          </Step>
          <Step n={4} title="Resolved" glyph={<OutcomeGlyph />}>
            <b className="text-bull">hit</b> — target touched · <b className="text-bear">reversed</b> — histogram
            flipped back · <b>expired</b> — neither · <b>failed</b> — re-expanded before crossing.
          </Step>
        </ol>
      )}
    </section>
  )
}

function Step({ n, title, glyph, children }: { n: number; title: string; glyph: ReactNode; children: ReactNode }) {
  return (
    <li className="rounded-xl border border-line bg-card p-4">
      <div className="flex items-center justify-between">
        <span className="font-serif text-lg">
          <span className="num mr-2 text-muted">{n}</span>
          {title}
        </span>
        {glyph}
      </div>
      <p className="mt-2 text-sm leading-relaxed text-muted">{children}</p>
    </li>
  )
}

// --- schematic glyphs: 64 x 32, zero line at y = 16 ---------------------------------

const Zero = () => <line x1="0" x2="64" y1="16" y2="16" className="stroke-line" strokeWidth="1" />

function ShrinkingGlyph() {
  return (
    <svg width="64" height="32" aria-hidden>
      <Zero />
      {[14, 11, 8, 5, 3].map((h, i) => (
        <rect key={i} x={6 + i * 11} y={16} width="7" height={h} className="fill-bull" opacity={i === 0 ? 0.35 : 0.85} />
      ))}
    </svg>
  )
}

function CrossGlyph() {
  return (
    <svg width="64" height="32" aria-hidden>
      <Zero />
      {/* A bullish window: the shrinking negative run, then the histogram above zero. */}
      {[8, 5, 2].map((h, i) => (
        <rect key={i} x={6 + i * 11} y={16} width="7" height={h} className="fill-bull" opacity={0.45} />
      ))}
      {[3, 6].map((h, i) => (
        <rect key={i} x={39 + i * 11} y={16 - h} width="7" height={h} className="fill-bull" opacity={0.9} />
      ))}
    </svg>
  )
}

function FollowGlyph() {
  return (
    <svg width="64" height="32" aria-hidden>
      <line x1="0" x2="64" y1="6" y2="6" className="stroke-accent" strokeWidth="1.5" strokeDasharray="3 2" />
      <polyline points="2,28 14,24 24,26 36,17 48,12 60,7" fill="none" className="stroke-ink" strokeWidth="1.5" />
      <circle cx="60" cy="7" r="2.5" className="fill-accent" />
    </svg>
  )
}

function OutcomeGlyph() {
  return (
    <svg width="64" height="32" aria-hidden>
      {['fill-bull', 'fill-bear', 'fill-muted', 'fill-line'].map((fill, i) => (
        <circle key={i} cx={9 + i * 15} cy="16" r="5" className={fill} />
      ))}
    </svg>
  )
}
