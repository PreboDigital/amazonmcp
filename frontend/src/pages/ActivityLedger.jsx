import { useEffect, useState } from 'react'
import { History, Loader2 } from 'lucide-react'
import { activity } from '../lib/api'
import { useAccount } from '../lib/AccountContext'

export default function ActivityLedger() {
  const { activeAccountId } = useAccount()
  const [events, setEvents] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    if (!activeAccountId) {
      setEvents([])
      setLoading(false)
      return undefined
    }
    setLoading(true)
    setError(null)
    activity.ledger(activeAccountId, 100)
      .then((r) => {
        if (!cancelled) setEvents(r.events || [])
      })
      .catch((e) => {
        if (!cancelled) setError(e.message)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [activeAccountId])

  return (
    <div className="space-y-6 max-w-4xl mx-auto">
      <div className="flex items-center gap-3">
        <div className="flex items-center justify-center w-10 h-10 rounded-lg bg-slate-100 text-slate-600">
          <History size={20} />
        </div>
        <div>
          <h1 className="text-2xl font-bold text-slate-900 tracking-tight">Change log</h1>
          <p className="text-sm text-slate-500 mt-0.5">Activity, approvals, and applied bid changes for this account.</p>
        </div>
      </div>

      <div className="card overflow-hidden">
        {loading && (
          <div className="py-16 flex justify-center">
            <Loader2 className="w-8 h-8 animate-spin text-brand-500" />
          </div>
        )}
        {error && (
          <div className="p-4 text-sm text-red-600">{error}</div>
        )}
        {!loading && !error && events.length === 0 && (
          <p className="p-8 text-center text-sm text-slate-500">No events yet.</p>
        )}
        {!loading && !error && events.length > 0 && (
          <ul className="divide-y divide-slate-100">
            {events.map((ev, i) => (
              <li key={`${ev.at}-${ev.kind}-${i}`} className="px-5 py-3 flex flex-col gap-1">
                <div className="flex flex-wrap items-center gap-2 text-xs text-slate-400">
                  <span className="font-mono tabular-nums">{ev.at || '—'}</span>
                  <span className="px-1.5 py-0.5 rounded bg-slate-100 text-slate-600 uppercase tracking-wide">{ev.kind}</span>
                  {ev.category && <span>{ev.category}</span>}
                </div>
                <p className="text-sm text-slate-800">{ev.description || ev.action}</p>
                {ev.details && Object.keys(ev.details).length > 0 && (
                  <pre className="text-[11px] text-slate-500 bg-slate-50 rounded p-2 overflow-x-auto max-h-24">{JSON.stringify(ev.details, null, 2)}</pre>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
