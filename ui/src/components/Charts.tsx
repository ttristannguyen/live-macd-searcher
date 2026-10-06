import { useEffect, useRef, useState } from 'react'
import type { SeriesBar, Window } from '../api/types'
import { barTime, HOUR_MS } from '../lib/format'

/** The width of a container, kept current — so charts draw to the pixel, not stretched. */
function useWidth() {
  const ref = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)
  useEffect(() => {
    if (!ref.current) return
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width))
    observer.observe(ref.current)
    return () => observer.disconnect()
  }, [])
  return { ref, width }
}

interface ChartProps {
  bars: SeriesBar[]
  window: Window
}

const PAD = { left: 8, right: 56, top: 10, bottom: 22 }

/** Shared x scale: one slot per hourly bar, by open_time. */
function xScale(bars: SeriesBar[], width: number) {
  const first = bars[0].open_time
  const slot = (width - PAD.left - PAD.right) / bars.length
  return {
    slot,
    x: (openTime: number) => PAD.left + ((openTime - first) / HOUR_MS + 0.5) * slot,
  }
}

function yScale(lo: number, hi: number, height: number) {
  const span = hi - lo || 1
  return (value: number) => PAD.top + (1 - (value - lo) / span) * (height - PAD.top - PAD.bottom)
}

/** The window's span — peak to resolution, or to now — shaded on both charts. */
function WindowSpan({ bars, window: w, width, height }: ChartProps & { width: number; height: number }) {
  const { x, slot } = xScale(bars, width)
  const end = w.resolved_at ?? w.updated_at
  return (
    <>
      <rect
        x={x(w.started_at) - slot / 2}
        width={x(end) - x(w.started_at) + slot}
        y={PAD.top}
        height={height - PAD.top - PAD.bottom}
        className={w.side === 'bullish' ? 'fill-bull' : 'fill-bear'}
        opacity={0.08}
      />
      {w.crossed_at !== null && (
        <g>
          <line
            x1={x(w.crossed_at)}
            x2={x(w.crossed_at)}
            y1={PAD.top}
            y2={height - PAD.bottom}
            className="stroke-ink"
            strokeDasharray="3 3"
            opacity={0.6}
          />
          <text x={x(w.crossed_at) + 4} y={PAD.top + 10} className="fill-muted text-[10px]">
            cross
          </text>
        </g>
      )}
    </>
  )
}

function TimeAxis({ bars, width, height }: { bars: SeriesBar[]; width: number; height: number }) {
  const { x } = xScale(bars, width)
  const every = Math.max(1, Math.ceil(bars.length / Math.max(2, Math.floor(width / 110))))
  return (
    <>
      {bars
        // Labels too near either edge would be clipped: drop them rather than cut them.
        .filter((bar, i) => i % every === 0 && x(bar.open_time) > 40 && x(bar.open_time) < width - PAD.right - 30)
        .map((bar) => (
          <text key={bar.open_time} x={x(bar.open_time)} y={height - 6} textAnchor="middle" className="fill-muted text-[10px]">
            {barTime(bar.open_time)}
          </text>
        ))}
    </>
  )
}

function PriceLabel({ y, value, className }: { y: number; value: number; className: string }) {
  return (
    <text x="100%" dx={-4} y={y + 3} textAnchor="end" className={`num text-[10px] ${className}`}>
      {value.toPrecision(6)}
    </text>
  )
}

export function PriceChart({ bars, window: w }: ChartProps) {
  const { ref, width } = useWidth()
  const height = 240
  const lows = bars.map((b) => Math.min(b.low, b.lower ?? b.low))
  const highs = bars.map((b) => Math.max(b.high, b.upper ?? b.high))
  const y = yScale(Math.min(...lows), Math.max(...highs), height)
  const ready = width > 0 && bars.length > 0
  const { x, slot } = ready ? xScale(bars, width) : { x: () => 0, slot: 0 }
  const line = (pick: (b: SeriesBar) => number | null) =>
    bars
      .filter((b) => pick(b) !== null)
      .map((b) => `${x(b.open_time)},${y(pick(b) as number)}`)
      .join(' ')
  // The target is the outer band on the far side: upper for bullish, lower for bearish.
  const targetIsUpper = w.side === 'bullish'
  const last = bars[bars.length - 1]

  return (
    <div ref={ref}>
      {ready && (
        <svg width={width} height={height} className="block">
          <WindowSpan bars={bars} window={w} width={width} height={height} />
          <polyline points={line((b) => b.middle)} fill="none" className="stroke-accent" strokeWidth="1" strokeDasharray="4 3" />
          <polyline points={line((b) => b.upper)} fill="none" className="stroke-accent" strokeWidth={targetIsUpper ? 2 : 1} opacity={targetIsUpper ? 0.9 : 0.45} />
          <polyline points={line((b) => b.lower)} fill="none" className="stroke-accent" strokeWidth={targetIsUpper ? 1 : 2} opacity={targetIsUpper ? 0.45 : 0.9} />
          {bars.map((b) => {
            const up = b.close >= b.open
            const body = Math.max(1, Math.abs(y(b.open) - y(b.close)))
            return (
              <g key={b.open_time} className={up ? 'fill-ink stroke-ink' : 'fill-muted stroke-muted'} opacity={b.volume === 0 ? 0.35 : 1}>
                <line x1={x(b.open_time)} x2={x(b.open_time)} y1={y(b.high)} y2={y(b.low)} strokeWidth="1" />
                <rect x={x(b.open_time) - slot * 0.32} width={slot * 0.64} y={Math.min(y(b.open), y(b.close))} height={body} fillOpacity={up ? 0.15 : 0.9} />
              </g>
            )
          })}
          <PriceLabel y={y(last.close)} value={last.close} className="fill-ink" />
          {last.upper !== null && last.lower !== null && (
            <PriceLabel y={y(targetIsUpper ? last.upper : last.lower)} value={targetIsUpper ? last.upper : last.lower} className="fill-accent" />
          )}
          <TimeAxis bars={bars} width={width} height={height} />
        </svg>
      )}
    </div>
  )
}

export function MacdChart({ bars, window: w }: ChartProps) {
  const { ref, width } = useWidth()
  const height = 150
  const values = bars.flatMap((b) => [b.hist, b.macd, b.signal])
  const extent = Math.max(...values.map(Math.abs))
  const y = yScale(-extent, extent, height)
  const ready = width > 0 && bars.length > 0
  const { x, slot } = ready ? xScale(bars, width) : { x: () => 0, slot: 0 }
  const end = w.resolved_at ?? w.updated_at
  const line = (pick: (b: SeriesBar) => number) => bars.map((b) => `${x(b.open_time)},${y(pick(b))}`).join(' ')

  return (
    <div ref={ref}>
      {ready && (
        <svg width={width} height={height} className="block">
          <WindowSpan bars={bars} window={w} width={width} height={height} />
          <line x1={PAD.left} x2={width - PAD.right} y1={y(0)} y2={y(0)} className="stroke-line" />
          {bars.map((b) => {
            const inWindow = b.open_time >= w.started_at && b.open_time <= end
            return (
              <rect
                key={b.open_time}
                x={x(b.open_time) - slot * 0.35}
                width={slot * 0.7}
                y={Math.min(y(0), y(b.hist))}
                height={Math.abs(y(b.hist) - y(0))}
                className={b.hist >= 0 ? 'fill-bull' : 'fill-bear'}
                opacity={inWindow ? 0.9 : 0.3}
              />
            )
          })}
          <polyline points={line((b) => b.macd)} fill="none" className="stroke-ink" strokeWidth="1.25" />
          <polyline points={line((b) => b.signal)} fill="none" className="stroke-accent" strokeWidth="1.25" />
          <TimeAxis bars={bars} width={width} height={height} />
        </svg>
      )}
    </div>
  )
}
