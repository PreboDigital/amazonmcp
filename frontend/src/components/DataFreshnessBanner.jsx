import { useEffect, useState } from 'react'
import { AlertTriangle, Clock, X } from 'lucide-react'
import clsx from 'clsx'
import { reports } from '../lib/api'
import { useAccount } from '../lib/AccountContext'

/**
 * Shows when cached performance / search-term / product data is stale for the active profile.
 */
export default function DataFreshnessBanner() {
  const { activeAccountId, activeProfileId } = useAccount()
  const [data, setData] = useState(null)
  const [dismissed, setDismissed] = useState(false)

  useEffect(() => {
    setDismissed(false)
  }, [activeAccountId, activeProfileId])

  useEffect(() => {
    let cancelled = false
    if (!activeAccountId) {
      setData(null)
      return undefined
    }
    reports.dataFreshness(activeAccountId, activeProfileId)
      .then((d) => {
        if (!cancelled) setData(d)
      })
      .catch(() => {
        if (!cancelled) setData(null)
      })
    return () => { cancelled = true }
  }, [activeAccountId, activeProfileId])

  if (!data || dismissed) return null

  const tables = data.tables || {}
  const worst = Object.entries(tables).filter(([, v]) => v?.staleness === 'stale' || v?.staleness === 'never')
  const warns = Object.entries(tables).filter(([, v]) => v?.staleness === 'warn')
  const overall = data.overall?.status

  if (overall !== 'stale' && warns.length === 0) return null

  const msg = worst.length
    ? `Some data is stale or missing: ${worst.slice(0, 4).map(([k]) => k.replace(/_/g, ' ')).join(', ')}${worst.length > 4 ? '…' : ''}.`
    : warns.length
      ? `Data freshness warning: ${warns.slice(0, 3).map(([k]) => k.replace(/_/g, ' ')).join(', ')}.`
      : null
  if (!msg) return null

  return (
    <div
      className={clsx(
        'flex items-start gap-3 px-4 py-2.5 border-b text-sm',
        worst.length ? 'bg-amber-50 border-amber-100 text-amber-900' : 'bg-slate-50 border-slate-200 text-slate-700',
      )}
    >
      {worst.length ? <AlertTriangle size={18} className="shrink-0 mt-0.5 text-amber-600" /> : <Clock size={18} className="shrink-0 mt-0.5 text-slate-500" />}
      <p className="flex-1 min-w-0">{msg} Run sync or scheduled crons, or open Data Sync.</p>
      <button type="button" className="text-slate-400 hover:text-slate-600 p-0.5 shrink-0" title="Dismiss" onClick={() => setDismissed(true)}>
        <X size={16} />
      </button>
    </div>
  )
}
