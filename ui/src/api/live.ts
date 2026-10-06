import { useEffect, useRef, useState } from 'react'
import { fetchHealth, fetchLive, fetchRecent } from './client'
import { TERMINAL, type Health, type Provisional, type Window } from './types'

const RECENT = 24

export interface LiveBoard {
  live: Window[] // active and crossed
  recent: Window[] // resolved, newest first
  provisional: Map<string, Provisional>
  health: Health | null
  asOf: number | null // open_time of the newest closed bar seen
  connected: boolean // the stream itself, as opposed to the exchange feed
}

/**
 * The board, kept current by the stream — nothing polls (PLAN M8).
 *
 * On every (re)connect it refetches, because events sent while disconnected are gone
 * (DESIGN §8). A refetch must not undo an event that arrived while it was in flight, so
 * every row remembers when it was last touched, and only rows untouched since the
 * refetch began are dropped for being absent from its answer.
 */
export function useLiveBoard(): LiveBoard {
  const touched = useRef(new Map<number, number>()) // window id -> when last updated here
  const [rows, setRows] = useState(new Map<number, Window>())
  const [recent, setRecent] = useState<Window[]>([])
  const [provisional, setProvisional] = useState(new Map<string, Provisional>())
  const [health, setHealth] = useState<Health | null>(null)
  const [asOf, setAsOf] = useState<number | null>(null)
  const [connected, setConnected] = useState(false)

  useEffect(() => {
    const seeNewBar = (openTime: number | null) =>
      openTime !== null && setAsOf((current) => (current === null ? openTime : Math.max(current, openTime)))

    const upsert = (window: Window) => {
      touched.current.set(window.id, performance.now())
      seeNewBar(window.updated_at)
      if (TERMINAL.includes(window.state)) {
        setRows((current) => without(current, window.id))
        setRecent((current) => [window, ...current.filter((w) => w.id !== window.id)].slice(0, RECENT))
      } else {
        setRows((current) => new Map(current).set(window.id, window))
      }
    }

    const refetch = async () => {
      const startedAt = performance.now()
      const [live, latest, nowHealth] = await Promise.all([fetchLive(), fetchRecent(RECENT), fetchHealth()])
      setRows((current) => {
        const next = new Map(live.windows.map((w) => [w.id, w]))
        for (const [id, window] of current) {
          // Touched by an event since the refetch began: the event is newer — keep it.
          if (!next.has(id) && (touched.current.get(id) ?? 0) > startedAt) next.set(id, window)
        }
        return next
      })
      setRecent(latest.windows)
      setHealth(nowHealth)
      seeNewBar(live.as_of)
    }

    const stream = new EventSource('/api/stream')
    stream.onopen = () => {
      setConnected(true)
      refetch().catch(() => setConnected(false))
    }
    stream.onerror = () => setConnected(false) // EventSource reconnects by itself
    for (const kind of ['opened', 'updated', 'crossed', 'resolved']) {
      stream.addEventListener(`window.${kind}`, (event) => upsert(JSON.parse((event as MessageEvent).data)))
    }
    stream.addEventListener('tick.provisional', (event) => {
      const { readings } = JSON.parse((event as MessageEvent).data) as { readings: Provisional[] }
      setProvisional(new Map(readings.map((r) => [r.symbol, r])))
    })
    stream.addEventListener('health', (event) => setHealth(JSON.parse((event as MessageEvent).data)))
    return () => stream.close()
  }, [])

  return { live: [...rows.values()], recent, provisional, health, asOf, connected }
}

function without<K, V>(map: Map<K, V>, key: K): Map<K, V> {
  if (!map.has(key)) return map
  const next = new Map(map)
  next.delete(key)
  return next
}
