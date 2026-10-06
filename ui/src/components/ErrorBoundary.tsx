import { Component, type ErrorInfo, type ReactNode } from 'react'

interface State {
  error: Error | null
}

/** Fail honestly (CLAUDE.md): a render error must say so, never leave a silently blank
 *  page that a glance could mistake for "nothing happening". */
export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('The board stopped rendering:', error, info.componentStack)
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="mx-auto max-w-xl px-5 py-24">
        <h1 className="font-serif text-3xl">The board hit an error</h1>
        <p className="mt-3 text-muted">
          What it shows can't be trusted until it reloads. The detector is unaffected — it runs on
          the server.
        </p>
        <pre className="mt-4 overflow-x-auto rounded-lg border border-line bg-card p-3 text-xs text-bear">
          {String(this.state.error.stack ?? this.state.error)}
        </pre>
        <button
          onClick={() => window.location.reload()}
          className="mt-4 rounded-lg bg-ink px-4 py-2 text-sm text-paper"
        >
          Reload the board
        </button>
      </div>
    )
  }
}
