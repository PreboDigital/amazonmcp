import { useState, useEffect } from 'react'
import { Clock, RefreshCw, Loader2, Plus, Trash2 } from 'lucide-react'
import { cronApi } from '../lib/api'
import { useAuth } from '../lib/AuthContext'

const JOB_OPTIONS = [
  { value: 'sync', label: 'Campaign Sync' },
  { value: 'reports', label: 'Reports' },
  { value: 'search-terms', label: 'Search Terms' },
]

const CRON_PRESETS = [
  { value: '0 */6 * * *', label: 'Every 6 hours' },
  { value: '0 */12 * * *', label: 'Every 12 hours' },
  { value: '0 6 * * *', label: 'Daily at 6:00 UTC' },
  { value: '0 7 * * *', label: 'Daily at 7:00 UTC' },
  { value: 'custom', label: 'Custom cron…' },
]

function jobFromDestination(dest) {
  if (!dest) return ''
  if (dest.includes('/cron/sync')) return 'sync'
  if (dest.includes('/cron/reports')) return 'reports'
  if (dest.includes('/cron/search-terms')) return 'search-terms'
  return ''
}

export default function DataSync() {
  const { isAdmin } = useAuth()
  const [dataSyncTriggering, setDataSyncTriggering] = useState(null)
  const [schedules, setSchedules] = useState([])
  const [schedulesLoading, setSchedulesLoading] = useState(false)
  const [error, setError] = useState(null)
  const [creating, setCreating] = useState(false)
  const [deletingId, setDeletingId] = useState(null)
  const [newJob, setNewJob] = useState('sync')
  const [newCronPreset, setNewCronPreset] = useState('0 */6 * * *')
  const [newCronCustom, setNewCronCustom] = useState('')

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

  async function handleCreateSchedule() {
    const cron = newCronPreset === 'custom' ? newCronCustom.trim() : newCronPreset
    if (!cron) {
      setError('Enter a cron expression')
      return
    }
    setCreating(true)
    setError(null)
    try {
      await cronApi.createSchedule(newJob, cron)
      await loadSchedules()
      setNewCronCustom('')
      setNewCronPreset('0 */6 * * *')
    } catch (err) {
      setError(err.message || 'Failed to create schedule')
    } finally {
      setCreating(false)
    }
  }

  async function handleDeleteSchedule(scheduleId) {
    setDeletingId(scheduleId)
    setError(null)
    try {
      await cronApi.deleteSchedule(scheduleId)
      await loadSchedules()
    } catch (err) {
      setError(err.message || 'Failed to delete schedule')
    } finally {
      setDeletingId(null)
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
        <h3 className="text-sm font-semibold text-slate-800 mb-2">Automated Schedules</h3>
        <p className="text-xs text-slate-500 mb-4">
          Create schedules to run syncs automatically via Upstash QStash. Requires QSTASH_TOKEN and CRON_SECRET in your environment.
        </p>

        <div className="flex flex-wrap items-end gap-3 mb-6 p-4 bg-slate-50 rounded-lg border border-slate-200">
          <div>
            <label className="block text-xs font-medium text-slate-600 mb-1">Job</label>
            <select
              value={newJob}
              onChange={(e) => setNewJob(e.target.value)}
              className="input text-sm w-40"
            >
              {JOB_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-600 mb-1">Frequency</label>
            <select
              value={newCronPreset}
              onChange={(e) => setNewCronPreset(e.target.value)}
              className="input text-sm w-44"
            >
              {CRON_PRESETS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </div>
          {newCronPreset === 'custom' && (
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">Cron expression</label>
              <input
                type="text"
                value={newCronCustom}
                onChange={(e) => setNewCronCustom(e.target.value)}
                placeholder="0 */6 * * *"
                className="input text-sm w-36 font-mono"
              />
            </div>
          )}
          <button
            onClick={handleCreateSchedule}
            disabled={creating || (newCronPreset === 'custom' && !newCronCustom.trim())}
            className="btn-primary text-sm"
          >
            {creating ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
            Add schedule
          </button>
        </div>

        <div className="flex items-center gap-2 mb-3">
          <button onClick={loadSchedules} disabled={schedulesLoading} className="btn-secondary text-sm">
            {schedulesLoading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
            Refresh
          </button>
        </div>
        {schedules.length > 0 ? (
          <ul className="text-sm text-slate-600 space-y-2">
            {schedules.map((s, i) => {
              const job = jobFromDestination(s.destination || s.url)
              const jobLabel = JOB_OPTIONS.find((o) => o.value === job)?.label || 'Schedule'
              return (
                <li key={s.scheduleId || i} className="flex items-center gap-3 font-mono text-xs py-2 px-3 rounded bg-slate-50 border border-slate-200">
                  <span className="font-medium text-slate-700">{jobLabel}</span>
                  {s.cron && <span className="text-slate-500">{s.cron}</span>}
                  <span className="truncate max-w-[200px] text-slate-400" title={s.destination || s.url}>
                    {s.destination || s.url || ''}
                  </span>
                  <button
                    onClick={() => handleDeleteSchedule(s.scheduleId)}
                    disabled={deletingId === s.scheduleId}
                    className="ml-auto p-1.5 text-red-600 hover:bg-red-50 rounded transition-colors"
                    title="Delete schedule"
                  >
                    {deletingId === s.scheduleId ? (
                      <Loader2 size={14} className="animate-spin" />
                    ) : (
                      <Trash2 size={14} />
                    )}
                  </button>
                </li>
              )
            })}
          </ul>
        ) : (
          <p className="text-sm text-slate-400">
            {schedulesLoading ? 'Loading…' : 'No schedules yet. Add one above.'}
          </p>
        )}
      </div>
    </div>
  )
}
