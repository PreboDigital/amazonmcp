import { useState, useEffect } from 'react'
import { Clock, RefreshCw, Loader2 } from 'lucide-react'
import { cronApi } from '../lib/api'
import { useAuth } from '../lib/AuthContext'

export default function DataSync() {
  const { isAdmin } = useAuth()
  const [dataSyncTriggering, setDataSyncTriggering] = useState(null)
  const [schedules, setSchedules] = useState([])
  const [schedulesLoading, setSchedulesLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (isAdmin) loadSchedules()
  }, [isAdmin])

  async function loadSchedules() {
    if (!isAdmin) return
    setSchedulesLoading(true)
    setError(null)
    try {
      const data = await cronApi.listSchedules()
      setSchedules(data?.schedules || [])
      if (data?.error) setError(data.error)
    } catch (err) {
      setSchedules([])
      setError(err.message)
    } finally {
      setSchedulesLoading(false)
    }
  }

  async function triggerDataSync(job) {
    setDataSyncTriggering(job)
    setError(null)
    try {
      if (job === 'sync') await cronApi.triggerSync()
      else if (job === 'reports') await cronApi.triggerReports()
      else if (job === 'search-terms') await cronApi.triggerSearchTerms()
      await loadSchedules()
    } catch (err) {
      setError(err.message || `Failed to run ${job}`)
    } finally {
      setDataSyncTriggering(null)
    }
  }

  if (!isAdmin) {
    return (
      <div className="p-8">
        <div className="card p-8 text-center">
          <Clock className="w-12 h-12 text-amber-500 mx-auto mb-4" />
          <h2 className="text-lg font-semibold text-slate-800">Admin only</h2>
          <p className="text-slate-600 mt-2">Data sync scheduling is available to administrators only.</p>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-8">
      <div className="flex items-center gap-3">
        <div className="p-2.5 bg-brand-100 rounded-xl">
          <Clock className="w-6 h-6 text-brand-600" />
        </div>
        <div>
          <h1 className="text-xl font-semibold text-slate-800">Data Sync Schedules</h1>
          <p className="text-sm text-slate-500">Manage automated fetches of Amazon Ads data</p>
        </div>
      </div>

      {error && (
        <div className="card p-4 bg-amber-50 border-amber-200 text-amber-800 text-sm">
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div className="card p-6">
          <h3 className="text-sm font-semibold text-slate-800 mb-2">Campaign Sync</h3>
          <p className="text-xs text-slate-500 mb-4">Sync campaigns, ad groups, targets, and ads from Amazon.</p>
          <button
            onClick={() => triggerDataSync('sync')}
            disabled={dataSyncTriggering !== null}
            className="btn-primary w-full justify-center text-sm"
          >
            {dataSyncTriggering === 'sync' ? (
              <Loader2 size={16} className="animate-spin" />
            ) : (
              <RefreshCw size={16} />
            )}
            {dataSyncTriggering === 'sync' ? 'Running…' : 'Run now'}
          </button>
        </div>
        <div className="card p-6">
          <h3 className="text-sm font-semibold text-slate-800 mb-2">Reports</h3>
          <p className="text-xs text-slate-500 mb-4">Fetch performance reports (last 7 days).</p>
          <button
            onClick={() => triggerDataSync('reports')}
            disabled={dataSyncTriggering !== null}
            className="btn-primary w-full justify-center text-sm"
          >
            {dataSyncTriggering === 'reports' ? (
              <Loader2 size={16} className="animate-spin" />
            ) : (
              <RefreshCw size={16} />
            )}
            {dataSyncTriggering === 'reports' ? 'Running…' : 'Run now'}
          </button>
        </div>
        <div className="card p-6">
          <h3 className="text-sm font-semibold text-slate-800 mb-2">Search Terms</h3>
          <p className="text-xs text-slate-500 mb-4">Sync search term performance data.</p>
          <button
            onClick={() => triggerDataSync('search-terms')}
            disabled={dataSyncTriggering !== null}
            className="btn-primary w-full justify-center text-sm"
          >
            {dataSyncTriggering === 'search-terms' ? (
              <Loader2 size={16} className="animate-spin" />
            ) : (
              <RefreshCw size={16} />
            )}
            {dataSyncTriggering === 'search-terms' ? 'Running…' : 'Run now'}
          </button>
        </div>
      </div>

      <div className="card p-6">
        <h3 className="text-sm font-semibold text-slate-800 mb-2">QStash Schedules</h3>
        <p className="text-xs text-slate-500 mb-4">
          Automated schedules configured via Upstash QStash. Set QSTASH_TOKEN and CRON_SECRET, then create schedules in the Upstash Console or via curl (see RAILWAY.md).
        </p>
        <button onClick={loadSchedules} disabled={schedulesLoading} className="btn-secondary text-sm mb-4">
          {schedulesLoading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
          Refresh
        </button>
        {schedules.length > 0 ? (
          <ul className="text-sm text-slate-600 space-y-2">
            {schedules.map((s, i) => (
              <li key={s.scheduleId || i} className="flex items-center gap-3 font-mono text-xs">
                <span className="truncate max-w-[300px]" title={s.destination || s.url}>
                  {s.destination || s.url || 'Schedule'}
                </span>
                {s.cron && <span className="text-slate-400 shrink-0">{s.cron}</span>}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-slate-400">
            {schedulesLoading ? 'Loading…' : 'No QStash schedules configured.'}
          </p>
        )}
      </div>
    </div>
  )
}
