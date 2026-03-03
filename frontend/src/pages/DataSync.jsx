import { useEffect, useMemo, useState } from 'react'
import { Clock, RefreshCw, Loader2, Plus, Trash2 } from 'lucide-react'
import { cronApi } from '../lib/api'
import { useAuth } from '../lib/AuthContext'
import { useAccount } from '../lib/AccountContext'
import { getAccountScopeMeta } from '../lib/accountScope'

const JOB_OPTIONS = [
  { value: 'sync', label: 'Campaign Sync' },
  { value: 'reports', label: 'Reports' },
  { value: 'search-terms', label: 'Search Terms' },
  { value: 'products', label: 'Products' },
]

const RANGE_OPTIONS = {
  reports: [
    { value: 'yesterday', label: 'Yesterday' },
    { value: 'last_7_days', label: 'Last 7 complete days' },
    { value: 'last_30_days', label: 'Last 30 complete days' },
    { value: 'month_to_yesterday', label: 'Month to yesterday' },
  ],
  'search-terms': [
    { value: 'yesterday', label: 'Yesterday' },
    { value: 'last_7_days', label: 'Last 7 complete days' },
    { value: 'last_30_days', label: 'Last 30 complete days' },
    { value: 'month_to_yesterday', label: 'Month to yesterday' },
  ],
  products: [
    { value: 'yesterday', label: 'Yesterday' },
    { value: 'last_7_days', label: 'Last 7 complete days' },
    { value: 'last_30_days', label: 'Last 30 complete days' },
    { value: 'month_to_yesterday', label: 'Month to yesterday' },
  ],
}

const DEFAULT_RANGE_BY_JOB = {
  reports: 'yesterday',
  'search-terms': 'last_7_days',
  products: 'last_30_days',
}

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
  if (dest.includes('/cron/products')) return 'products'
  return ''
}

function parseDestinationMeta(dest) {
  if (!dest) return {}
  try {
    const url = new URL(dest, window.location.origin)
    return {
      rangePreset: url.searchParams.get('range_preset') || null,
      credentialId: url.searchParams.get('credential_id') || null,
      profileId: url.searchParams.get('profile_id') || null,
    }
  } catch {
    return {}
  }
}

function rangeLabel(job, rangePreset) {
  if (!rangePreset) return ''
  return RANGE_OPTIONS[job]?.find((o) => o.value === rangePreset)?.label || rangePreset
}

function jobDescription(job, rangePreset) {
  if (job === 'sync') {
    return 'Sync campaigns, ad groups, targets, and ads from Amazon.'
  }
  const label = rangeLabel(job, rangePreset || DEFAULT_RANGE_BY_JOB[job])
  if (job === 'reports') return `Queue exact daily performance sync for ${label.toLowerCase()}.`
  if (job === 'search-terms') return `Sync search term performance for ${label.toLowerCase()}.`
  if (job === 'products') return `Sync product/business reports for ${label.toLowerCase()}.`
  return ''
}

export default function DataSync() {
  const { isAdmin } = useAuth()
  const { activeAccount, activeAccountId, activeProfileId } = useAccount()
  const campaignScope = useMemo(() => getAccountScopeMeta(activeAccount), [activeAccount])

  const [dataSyncTriggering, setDataSyncTriggering] = useState(null)
  const [schedules, setSchedules] = useState([])
  const [schedulesLoading, setSchedulesLoading] = useState(false)
  const [error, setError] = useState(null)
  const [creating, setCreating] = useState(false)
  const [deletingId, setDeletingId] = useState(null)
  const [newJob, setNewJob] = useState('sync')
  const [newRangePreset, setNewRangePreset] = useState(DEFAULT_RANGE_BY_JOB.reports)
  const [newCronPreset, setNewCronPreset] = useState('0 */6 * * *')
  const [newCronCustom, setNewCronCustom] = useState('')
  const [runRanges, setRunRanges] = useState({
    reports: DEFAULT_RANGE_BY_JOB.reports,
    'search-terms': DEFAULT_RANGE_BY_JOB['search-terms'],
    products: DEFAULT_RANGE_BY_JOB.products,
  })

  useEffect(() => {
    if (isAdmin) loadSchedules()
  }, [isAdmin])

  useEffect(() => {
    if (RANGE_OPTIONS[newJob]) {
      setNewRangePreset((prev) => prev || DEFAULT_RANGE_BY_JOB[newJob])
    } else {
      setNewRangePreset('')
    }
  }, [newJob])

  useEffect(() => {
    if (!campaignScope.canSyncCampaigns && newJob === 'sync') {
      setNewJob('reports')
    }
  }, [campaignScope.canSyncCampaigns, newJob])

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
      await cronApi.createSchedule(newJob, cron, {
        credentialId: activeAccountId,
        profileId: activeProfileId,
        rangePreset: RANGE_OPTIONS[newJob] ? newRangePreset : null,
      })
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
      const opts = {
        credentialId: activeAccountId,
        profileId: activeProfileId,
        rangePreset: RANGE_OPTIONS[job] ? runRanges[job] : null,
      }
      if (job === 'sync') await cronApi.triggerSync(opts)
      else if (job === 'reports') await cronApi.triggerReports(opts)
      else if (job === 'search-terms') await cronApi.triggerSearchTerms(opts)
      else if (job === 'products') await cronApi.triggerProducts(opts)
      await loadSchedules()
    } catch (err) {
      setError(err.message || `Failed to run ${job}`)
    } finally {
      setDataSyncTriggering(null)
    }
  }

  function setRunRange(job, value) {
    setRunRanges((prev) => ({ ...prev, [job]: value }))
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

  const scheduleTargetLabel = activeAccount
    ? `${activeAccount.account_name || activeAccount.name}${activeAccount.marketplace ? ` · ${activeAccount.marketplace}` : ''}`
    : 'the active account'

  return (
    <div className="space-y-8">
      <div className="flex items-center gap-3">
        <div className="p-2.5 bg-brand-100 rounded-xl">
          <Clock className="w-6 h-6 text-brand-600" />
        </div>
        <div>
          <h1 className="text-xl font-semibold text-slate-800">Data Sync Schedules</h1>
          <p className="text-sm text-slate-500">Manage automated fetches of Amazon Ads data for {scheduleTargetLabel}</p>
        </div>
      </div>

      {error && (
        <div className="card p-4 bg-amber-50 border-amber-200 text-amber-800 text-sm">
          {error}
        </div>
      )}

      <div className="card p-4 bg-blue-50 border-blue-200 text-blue-900 text-sm">
        Scheduled ranges use complete days only. For example, `Last 7 complete days` means yesterday back through the prior 6 days.
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
        <div className="card p-6">
          <h3 className="text-sm font-semibold text-slate-800 mb-2">Campaign Sync</h3>
          <p className="text-xs text-slate-500 mb-4">Sync campaigns, ad groups, targets, and ads from Amazon.</p>
          {!campaignScope.canSyncCampaigns && (
            <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg p-3 mb-4">
              {campaignScope.warning}
            </p>
          )}
          <button
            onClick={() => triggerDataSync('sync')}
            disabled={dataSyncTriggering !== null || !campaignScope.canSyncCampaigns}
            className="btn-primary w-full justify-center text-sm disabled:opacity-50"
          >
            {dataSyncTriggering === 'sync' ? <Loader2 size={16} className="animate-spin" /> : <RefreshCw size={16} />}
            {dataSyncTriggering === 'sync' ? 'Running…' : 'Run now'}
          </button>
        </div>

        {['reports', 'search-terms', 'products'].map((job) => (
          <div key={job} className="card p-6">
            <h3 className="text-sm font-semibold text-slate-800 mb-2">{JOB_OPTIONS.find((o) => o.value === job)?.label}</h3>
            <p className="text-xs text-slate-500 mb-3">{jobDescription(job, runRanges[job])}</p>
            <label className="block text-[11px] font-medium text-slate-600 mb-1">Range</label>
            <select
              value={runRanges[job]}
              onChange={(e) => setRunRange(job, e.target.value)}
              className="input text-sm w-full mb-4"
            >
              {RANGE_OPTIONS[job].map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
            <button
              onClick={() => triggerDataSync(job)}
              disabled={dataSyncTriggering !== null}
              className="btn-primary w-full justify-center text-sm"
            >
              {dataSyncTriggering === job ? <Loader2 size={16} className="animate-spin" /> : <RefreshCw size={16} />}
              {dataSyncTriggering === job ? 'Running…' : 'Run now'}
            </button>
          </div>
        ))}
      </div>

      <div className="card p-6">
        <h3 className="text-sm font-semibold text-slate-800 mb-2">Automated Schedules</h3>
        <p className="text-xs text-slate-500 mb-4">
          Create schedules to run syncs automatically via Upstash QStash. Schedules are bound to the current credential/profile and selected range.
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
                <option
                  key={o.value}
                  value={o.value}
                  disabled={o.value === 'sync' && !campaignScope.canSyncCampaigns}
                >
                  {o.label}
                </option>
              ))}
            </select>
          </div>
          {RANGE_OPTIONS[newJob] && (
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">Range</label>
              <select
                value={newRangePreset}
                onChange={(e) => setNewRangePreset(e.target.value)}
                className="input text-sm w-52"
              >
                {RANGE_OPTIONS[newJob].map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </div>
          )}
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
            disabled={
              creating ||
              (newJob === 'sync' && !campaignScope.canSyncCampaigns) ||
              (newCronPreset === 'custom' && !newCronCustom.trim())
            }
            className="btn-primary text-sm disabled:opacity-50"
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
              const destination = s.destination || s.url || ''
              const job = jobFromDestination(destination)
              const jobLabel = JOB_OPTIONS.find((o) => o.value === job)?.label || 'Schedule'
              const meta = parseDestinationMeta(destination)
              return (
                <li key={s.scheduleId || i} className="py-3 px-3 rounded bg-slate-50 border border-slate-200">
                  <div className="flex items-center gap-3 font-mono text-xs">
                    <span className="font-medium text-slate-700">{jobLabel}</span>
                    {meta.rangePreset && (
                      <span className="text-slate-500">{rangeLabel(job, meta.rangePreset)}</span>
                    )}
                    {s.cron && <span className="text-slate-500">{s.cron}</span>}
                    <button
                      onClick={() => handleDeleteSchedule(s.scheduleId)}
                      disabled={deletingId === s.scheduleId}
                      className="ml-auto p-1.5 rounded hover:bg-red-50 text-slate-400 hover:text-red-600 disabled:opacity-60"
                      title="Delete schedule"
                    >
                      {deletingId === s.scheduleId ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
                    </button>
                  </div>
                  <div className="mt-1 text-xs text-slate-500">
                    {meta.profileId
                      ? `Bound to profile ${meta.profileId}`
                      : meta.credentialId
                        ? `Bound to credential ${meta.credentialId}`
                        : 'Uses the default credential at run time'}
                  </div>
                  <div className="mt-1 truncate text-[11px] text-slate-400" title={destination}>
                    {destination}
                  </div>
                </li>
              )
            })}
          </ul>
        ) : (
          <p className="text-sm text-slate-500">
            {schedulesLoading ? 'Loading…' : 'No schedules yet. Add one above.'}
          </p>
        )}
      </div>
    </div>
  )
}
