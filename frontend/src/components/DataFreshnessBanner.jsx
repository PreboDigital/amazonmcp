import { useEffect, useState } from 'react'
import { AlertTriangle, Clock, X } from 'lucide-react'
import clsx from 'clsx'
import { reports } from '../lib/api'
import { useAccount } from '../lib/AccountContext'

function parseBackendUtc(iso) {
  if (!iso) return null
  let s = String(iso).trim()
  if (!s) return null
  if (/^\d{4}-\d{2}-\d{2} /.test(s)) s = s.replace(' ', 'T')
  const hasTz = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(s)
  if (/^\d{4}-\d{2}-\d{2}T/.test(s) && !hasTz) return new Date(`${s}Z`)
  return new Date(s)
}

/** "3 days ago" etc.; null if unparseable */
function formatRelativePast(iso) {
  const d = parseBackendUtc(iso)
  if (!d || Number.isNaN(d.getTime())) return null
  const diffMs = Date.now() - d.getTime()
  if (diffMs < 0) return 'just now'
  const rtf = new Intl.RelativeTimeFormat('en', { numeric: 'auto' })
  const sec = Math.floor(diffMs / 1000)
  if (sec < 45) return 'just now'
  const min = Math.floor(sec / 60)
  if (min < 60) return rtf.format(-min, 'minute')
  const hr = Math.floor(min / 60)
  if (hr < 48) return rtf.format(-hr, 'hour')
  const day = Math.floor(hr / 24)
  if (day < 30) return rtf.format(-day, 'day')
  const month = Math.floor(day / 30)
  if (month < 12) return rtf.format(-month, 'month')
  return rtf.format(-Math.floor(day / 365), 'year')
}

function formatMetricDay(dateStr) {
  if (!dateStr || !/^\d{4}-\d{2}-\d{2}/.test(String(dateStr))) return null
  const d = new Date(`${String(dateStr).slice(0, 10)}T12:00:00Z`)
  if (Number.isNaN(d.getTime())) return null
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' })
}

function tableCaption(key, v) {
  const label = key.replace(/_/g, ' ')
  const bits = []
  if (v?.last_synced_at) bits.push(`synced ${formatRelativePast(v.last_synced_at)}`)
  else bits.push('never synced')
  const through = v?.latest_date ? formatMetricDay(v.latest_date) : null
  if (through) bits.push(`data through ${through}`)
  return `${label} (${bits.join(' · ')})`
}

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

  const checkedRel = data.checked_at ? formatRelativePast(data.checked_at) : null
  const checkedBit = checkedRel ? ` Snapshot ${checkedRel}.` : ''

  const msg = worst.length
    ? `Some data is stale or missing: ${worst.slice(0, 4).map(([k, v]) => tableCaption(k, v)).join('; ')}${worst.length > 4 ? '…' : ''}.`
    : warns.length
      ? `Data freshness warning: ${warns.slice(0, 3).map(([k, v]) => tableCaption(k, v)).join('; ')}.`
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
      <p className="flex-1 min-w-0">
        {msg}
        {checkedBit}
        {' '}
        Run sync or scheduled crons, or open Data Sync.
      </p>
      <button type="button" className="text-slate-400 hover:text-slate-600 p-0.5 shrink-0" title="Dismiss" onClick={() => setDismissed(true)}>
        <X size={16} />
      </button>
    </div>
  )
}
