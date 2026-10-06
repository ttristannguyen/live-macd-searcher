import type { Board, Health, Series } from './types'

async function get<T>(path: string): Promise<T> {
  const response = await fetch(path)
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`)
  return response.json() as Promise<T>
}

/** Every live window — active and crossed. Filtering happens in the page. */
export const fetchLive = () => get<Board>('/api/windows?state=active&state=crossed&limit=1000')

/** The latest resolutions, newest first: the outcome record being written. */
export const fetchRecent = (limit = 24) =>
  get<Board>(
    `/api/windows?state=hit&state=reversed&state=expired&state=failed&order=recent&limit=${limit}`,
  )

export const fetchHealth = () => get<Health>('/api/health')

export const fetchSeries = (symbol: string, bars: number) =>
  get<Series>(`/api/symbols/${encodeURIComponent(symbol)}/series?bars=${bars}`)
