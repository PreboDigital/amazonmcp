import { useState, useEffect, useCallback } from 'react'
import {
  SearchCheck,
  Play,
  Loader2,
  BarChart3,
  AlertTriangle,
  Lightbulb,
  Clock,
  Calendar,
  ChevronDown,
  ChevronUp,
  DollarSign,
  TrendingUp,
  Target,
  Trash2,
  ShoppingCart,
  Eye,
  MousePointerClick,
} from 'lucide-react'
import MetricCard from '../components/MetricCard'
import StatusBadge from '../components/StatusBadge'
import EmptyState from '../components/EmptyState'
import { audit } from '../lib/api'
import { useAccount } from '../lib/AccountContext'

// ── Utility ──────────────────────────────────────────────────────────
function formatDateRange(startDate, endDate) {
  if (!startDate || !endDate) return ''
  const opts = { month: 'short', day: 'numeric', year: 'numeric' }
  const s = new Date(startDate + 'T00:00:00')
  const e = new Date(endDate + 'T00:00:00')
  if (isNaN(s) || isNaN(e)) return `${startDate} — ${endDate}`
  if (s.getFullYear() === e.getFullYear()) {
    return `${s.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })} — ${e.toLocaleDateString(undefined, opts)}`
  }
  return `${s.toLocaleDateString(undefined, opts)} — ${e.toLocaleDateString(undefined, opts)}`
}

// ── Severity styles ──────────────────────────────────────────────────
const severityStyles = {
  high: { dot: 'bg-red-500', text: 'text-red-700', bg: 'bg-red-50' },
  medium: { dot: 'bg-amber-500', text: 'text-amber-700', bg: 'bg-amber-50' },
  low: { dot: 'bg-blue-500', text: 'text-blue-700', bg: 'bg-blue-50' },
}

// ── Expanded Snapshot Detail ─────────────────────────────────────────
function SnapshotDetail({ snap, detail }) {
  const [activeTab, setActiveTab] = useState('overview')
  const issues = detail.issues || []
  const opportunities = detail.opportunities || []
  const reportCampaigns = detail.snapshot_data?.report_campaigns || []
  const dateRange = detail.date_range || snap.date_range

  const tabs = [
    { id: 'overview', label: 'Overview' },
    { id: 'issues', label: `Issues (${issues.length})` },
    { id: 'opportunities', label: `Opportunities (${opportunities.length})` },
    ...(reportCampaigns.length > 0 ? [{ id: 'campaigns', label: `Campaigns (${reportCampaigns.length})` }] : []),
  ]

  return (
    <div className="pt-4 space-y-4">
      {/* Tabs */}
      <div className="flex gap-1 border-b border-slate-200">
        {tabs.map(tab => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`px-4 py-2 text-xs font-medium rounded-t-lg transition-colors ${
              activeTab === tab.id
                ? 'bg-white text-indigo-600 border border-slate-200 border-b-white -mb-px'
                : 'text-slate-500 hover:text-slate-700'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Overview tab */}
      {activeTab === 'overview' && (
        <div className="space-y-4">
          {/* Date range banner */}
          {dateRange && (
            <div className="flex items-center gap-2 text-xs text-slate-500 bg-white rounded-lg px-3 py-2 border border-slate-200">
              <Calendar size={13} className="text-brand-500 shrink-0" />
              <span className="font-medium text-slate-600">{dateRange.label || 'Last 30 Days'}:</span>
              <span className="text-slate-700">{formatDateRange(dateRange.start_date, dateRange.end_date)}</span>
            </div>
          )}
          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-3">
            <MiniMetric icon={BarChart3} label="Campaigns" value={snap.campaigns_count} />
            <MiniMetric icon={Play} label="Active" value={snap.active_campaigns} color="green" />
            <MiniMetric icon={DollarSign} label="Spend" value={`$${(snap.total_spend || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`} color="blue" />
            <MiniMetric icon={ShoppingCart} label="Sales" value={`$${(snap.total_sales || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`} color="green" />
            <MiniMetric icon={Target} label="ACOS" value={`${(snap.avg_acos || 0).toFixed(1)}%`} color={snap.avg_acos > 30 ? 'red' : 'green'} />
            <MiniMetric icon={TrendingUp} label="ROAS" value={(snap.avg_roas || 0).toFixed(2)} color="brand" />
          </div>
          {(snap.waste_identified || 0) > 0 && (
            <div className="flex items-center gap-2 p-3 bg-red-50 rounded-lg border border-red-100">
              <Trash2 size={16} className="text-red-500 shrink-0" />
              <p className="text-sm text-red-700">
                <span className="font-semibold">${(snap.waste_identified).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span> estimated waste identified
              </p>
            </div>
          )}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div className="p-3 bg-white rounded-lg border border-slate-200">
              <p className="text-xs text-slate-400 mb-1">Ad Groups</p>
              <p className="text-lg font-semibold text-slate-800">{snap.total_ad_groups?.toLocaleString() || 0}</p>
            </div>
            <div className="p-3 bg-white rounded-lg border border-slate-200">
              <p className="text-xs text-slate-400 mb-1">Targets</p>
              <p className="text-lg font-semibold text-slate-800">{snap.total_targets?.toLocaleString() || 0}</p>
            </div>
          </div>
        </div>
      )}

      {/* Issues tab */}
      {activeTab === 'issues' && (
        <div className="space-y-2">
          {issues.length === 0 ? (
            <div className="py-6 text-center text-sm text-slate-400">No issues found in this audit</div>
          ) : (
            issues.map((issue, i) => {
              const sev = severityStyles[issue.severity] || severityStyles.low
              return (
                <div key={issue.id || i} className={`p-4 rounded-lg border border-slate-200 ${sev.bg}`}>
                  <div className="flex items-start gap-3">
                    <div className={`w-2 h-2 rounded-full mt-1.5 shrink-0 ${sev.dot}`} />
                    <div className="flex-1 min-w-0">
                      <p className={`text-sm font-medium ${sev.text}`}>{issue.message}</p>
                      <div className="flex items-center gap-3 mt-1.5">
                        <span className="text-xs font-medium text-slate-500 uppercase">{issue.severity}</span>
                        <span className="text-xs text-slate-400">{issue.issue_type?.replace(/_/g, ' ')}</span>
                        {issue.campaign_name && (
                          <span className="text-xs text-slate-500 truncate">{issue.campaign_name}</span>
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              )
            })
          )}
        </div>
      )}

      {/* Opportunities tab */}
      {activeTab === 'opportunities' && (
        <div className="space-y-2">
          {opportunities.length === 0 ? (
            <div className="py-6 text-center text-sm text-slate-400">No opportunities found</div>
          ) : (
            opportunities.map((opp, i) => (
              <div key={opp.id || i} className="p-4 rounded-lg border border-slate-200 bg-emerald-50/50">
                <div className="flex items-start gap-3">
                  <Lightbulb size={16} className="text-amber-500 mt-0.5 shrink-0" />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-slate-700">{opp.message}</p>
                    <div className="flex items-center gap-3 mt-1.5">
                      <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${
                        opp.potential_impact === 'high'
                          ? 'bg-emerald-100 text-emerald-700'
                          : opp.potential_impact === 'medium'
                            ? 'bg-blue-100 text-blue-700'
                            : 'bg-slate-100 text-slate-600'
                      }`}>
                        {opp.potential_impact} impact
                      </span>
                      <span className="text-xs text-slate-400">{opp.opportunity_type?.replace(/_/g, ' ')}</span>
                      {opp.campaign_name && (
                        <span className="text-xs text-slate-500 truncate">{opp.campaign_name}</span>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
      )}

      {/* Campaigns tab */}
      {activeTab === 'campaigns' && reportCampaigns.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-slate-400 uppercase tracking-wider border-b border-slate-200">
                <th className="py-2 pr-4 font-medium">Campaign</th>
                <th className="py-2 px-3 font-medium text-right">Spend</th>
                <th className="py-2 px-3 font-medium text-right">Sales</th>
                <th className="py-2 px-3 font-medium text-right">ACOS</th>
                <th className="py-2 px-3 font-medium text-right">ROAS</th>
                <th className="py-2 px-3 font-medium text-right">Clicks</th>
                <th className="py-2 px-3 font-medium text-right">Impressions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {reportCampaigns
                .sort((a, b) => (b.spend || 0) - (a.spend || 0))
                .slice(0, 50)
                .map((c, i) => {
                  const spend = c.spend || 0
                  const sales = c.sales || 0
                  const acos = sales > 0 ? (spend / sales * 100) : 0
                  const roas = spend > 0 ? (sales / spend) : 0
                  return (
                    <tr key={i} className="hover:bg-white transition-colors">
                      <td className="py-2.5 pr-4">
                        <p className="font-medium text-slate-700 truncate max-w-[280px]">{c.campaign_name || 'Unknown'}</p>
                      </td>
                      <td className="py-2.5 px-3 text-right font-medium text-slate-700">
                        ${spend.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                      </td>
                      <td className="py-2.5 px-3 text-right text-slate-600">
                        ${sales.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                      </td>
                      <td className={`py-2.5 px-3 text-right font-medium ${acos > 30 ? 'text-red-600' : acos > 20 ? 'text-amber-600' : 'text-emerald-600'}`}>
                        {acos.toFixed(1)}%
                      </td>
                      <td className="py-2.5 px-3 text-right text-slate-600">{roas.toFixed(2)}</td>
                      <td className="py-2.5 px-3 text-right text-slate-600">{(c.clicks || 0).toLocaleString()}</td>
                      <td className="py-2.5 px-3 text-right text-slate-600">{(c.impressions || 0).toLocaleString()}</td>
                    </tr>
                  )
                })}
            </tbody>
          </table>
          {reportCampaigns.length > 50 && (
            <p className="text-xs text-slate-400 text-center mt-2">Showing top 50 of {reportCampaigns.length} campaigns</p>
          )}
        </div>
      )}
    </div>
  )
}

// ── Mini Metric Card ─────────────────────────────────────────────────
function MiniMetric({ icon: Icon, label, value, color = 'slate' }) {
  const colors = {
    slate: 'text-slate-600',
    green: 'text-emerald-600',
    blue: 'text-blue-600',
    red: 'text-red-600',
    brand: 'text-indigo-600',
    amber: 'text-amber-600',
  }
  return (
    <div className="p-3 bg-white rounded-lg border border-slate-200">
      <div className="flex items-center gap-1.5 mb-1">
        <Icon size={12} className="text-slate-400" />
        <p className="text-xs text-slate-400">{label}</p>
      </div>
      <p className={`text-lg font-semibold ${colors[color]}`}>{value}</p>
    </div>
  )
}

export default function Audit() {
  const { activeAccount, activeAccountId } = useAccount()
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState(null)
  const [snapshots, setSnapshots] = useState([])
  const [error, setError] = useState(null)
  const [expandedSnapshot, setExpandedSnapshot] = useState(null)
  const [snapshotDetails, setSnapshotDetails] = useState({}) // { [id]: detailData }
  const [loadingDetail, setLoadingDetail] = useState(null)

  useEffect(() => {
    setResult(null)
    loadSnapshots()
  }, [activeAccountId])

  async function loadSnapshots() {
    try {
      const data = await audit.snapshots(activeAccountId)
      setSnapshots(data)
    } catch (err) {
      // Ignore if no snapshots yet
    }
  }

  async function runAudit() {
    setRunning(true)
    setError(null)
    try {
      const data = await audit.run(activeAccountId)
      setResult(data)
      await loadSnapshots()
    } catch (err) {
      setError(err.message)
    } finally {
      setRunning(false)
    }
  }

  const toggleSnapshot = useCallback(async (snapId) => {
    if (expandedSnapshot === snapId) {
      setExpandedSnapshot(null)
      return
    }
    setExpandedSnapshot(snapId)
    if (!snapshotDetails[snapId]) {
      setLoadingDetail(snapId)
      try {
        const detail = await audit.snapshot(snapId)
        setSnapshotDetails(prev => ({ ...prev, [snapId]: detail }))
      } catch (err) {
        console.error('Failed to load snapshot details', err)
      } finally {
        setLoadingDetail(null)
      }
    }
  }, [expandedSnapshot, snapshotDetails])

  const deleteSnapshot = useCallback(async (e, snapId) => {
    e.stopPropagation() // prevent expand/collapse
    if (!window.confirm('Delete this audit? This cannot be undone.')) return
    try {
      await audit.deleteSnapshot(snapId)
      setSnapshots(prev => prev.filter(s => s.id !== snapId))
      if (expandedSnapshot === snapId) setExpandedSnapshot(null)
      // Clean up cached details
      setSnapshotDetails(prev => {
        const copy = { ...prev }
        delete copy[snapId]
        return copy
      })
    } catch (err) {
      console.error('Failed to delete snapshot', err)
    }
  }, [expandedSnapshot])

  const summary = result?.summary || result?.analysis?.summary
  const resultDateRange = result?.date_range

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 tracking-tight">Audit & Reports</h1>
          <p className="mt-1 text-sm text-slate-500">
            {activeAccount
              ? <>Analyzing <span className="font-medium text-slate-700">{activeAccount.account_name || activeAccount.name}</span></>
              : 'Analyze campaign performance, identify waste, and find opportunities'}
          </p>
        </div>
        <button
          onClick={runAudit}
          disabled={running || !activeAccount}
          className="btn-primary"
        >
          {running ? (
            <><Loader2 size={16} className="animate-spin" /> Running Audit...</>
          ) : (
            <><Play size={16} /> Run Full Audit</>
          )}
        </button>
      </div>

      {!activeAccount && (
        <div className="card bg-amber-50 border-amber-200 p-4 text-sm text-amber-800">
          Add and select an account in Settings before running an audit.
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="card bg-red-50 border-red-200 p-4 flex items-start gap-3">
          <AlertTriangle size={18} className="text-red-500 mt-0.5 shrink-0" />
          <div>
            <p className="text-sm font-medium text-red-800">Audit Failed</p>
            <p className="text-sm text-red-600 mt-0.5">{error}</p>
          </div>
        </div>
      )}

      {/* Audit Results */}
      {summary && (
        <>
          {/* Date range indicator */}
          {resultDateRange && (
            <div className="flex items-center gap-2 text-sm text-slate-500 bg-slate-50 rounded-lg px-4 py-2.5 border border-slate-200">
              <Calendar size={15} className="text-brand-500 shrink-0" />
              <span className="font-medium text-slate-700">{resultDateRange.label || 'Last 30 Days'}:</span>
              <span>{formatDateRange(resultDateRange.start_date, resultDateRange.end_date)}</span>
            </div>
          )}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <MetricCard title="Campaigns" value={summary.total_campaigns} icon={BarChart3} color="brand" />
            <MetricCard title="Active" value={summary.active_campaigns} subtitle={`${summary.paused_campaigns} paused`} icon={BarChart3} color="green" />
            <MetricCard title="Ad Groups" value={summary.total_ad_groups} icon={BarChart3} color="blue" />
            <MetricCard title="Targets" value={summary.total_targets} icon={BarChart3} color="amber" />
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <div>
              <h2 className="text-sm font-semibold text-slate-900 mb-3 flex items-center gap-2">
                <AlertTriangle size={16} className="text-red-500" />
                Issues Found ({result?.analysis?.issues?.length || 0})
              </h2>
              <div className="card divide-y divide-slate-100">
                {(result?.analysis?.issues || []).length === 0 ? (
                  <div className="p-6 text-center text-sm text-slate-400">No issues detected</div>
                ) : (
                  result.analysis.issues.map((issue, i) => (
                    <div key={i} className="p-4 flex items-start gap-3">
                      <div className={`w-2 h-2 rounded-full mt-1.5 shrink-0 ${
                        issue.severity === 'high' ? 'bg-red-500' : issue.severity === 'medium' ? 'bg-amber-500' : 'bg-blue-500'
                      }`} />
                      <div>
                        <p className="text-sm font-medium text-slate-700">{issue.message}</p>
                        <p className="text-xs text-slate-400 mt-0.5">{issue.type}</p>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>

            <div>
              <h2 className="text-sm font-semibold text-slate-900 mb-3 flex items-center gap-2">
                <Lightbulb size={16} className="text-amber-500" />
                Opportunities ({result?.analysis?.opportunities?.length || 0})
              </h2>
              <div className="card divide-y divide-slate-100">
                {(result?.analysis?.opportunities || []).length === 0 ? (
                  <div className="p-6 text-center text-sm text-slate-400">No opportunities found</div>
                ) : (
                  result.analysis.opportunities.map((opp, i) => (
                    <div key={i} className="p-4 flex items-start gap-3">
                      <div className={`w-2 h-2 rounded-full mt-1.5 shrink-0 ${
                        opp.potential_impact === 'high' ? 'bg-emerald-500' : 'bg-blue-500'
                      }`} />
                      <div>
                        <p className="text-sm font-medium text-slate-700">{opp.message}</p>
                        <span className={`text-xs font-medium ${
                          opp.potential_impact === 'high' ? 'text-emerald-600' : 'text-blue-600'
                        }`}>
                          {opp.potential_impact} impact
                        </span>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>
        </>
      )}

      {/* Previous Snapshots */}
      <div>
        <h2 className="text-sm font-semibold text-slate-900 mb-3 flex items-center gap-2">
          <Clock size={16} className="text-slate-400" />
          Audit History {activeAccount && <span className="font-normal text-slate-400">for {activeAccount.account_name || activeAccount.name}</span>}
        </h2>
        {snapshots.length === 0 ? (
          <EmptyState
            icon={SearchCheck}
            title="No audits yet"
            description={activeAccount
              ? `Run your first audit for ${activeAccount.account_name || activeAccount.name} to see campaign performance data here.`
              : 'Select an account and run your first audit.'}
            action={activeAccount && (
              <button onClick={runAudit} disabled={running} className="btn-primary">
                {running ? 'Running...' : 'Run First Audit'}
              </button>
            )}
          />
        ) : (
          <div className="card divide-y divide-slate-100">
            {snapshots.map((snap) => {
              const isExpanded = expandedSnapshot === snap.id
              const detail = snapshotDetails[snap.id]
              const isLoading = loadingDetail === snap.id

              return (
                <div key={snap.id}>
                  {/* Collapsed row */}
                  <button
                    onClick={() => toggleSnapshot(snap.id)}
                    className="w-full px-5 py-4 flex items-center gap-4 hover:bg-slate-50 transition-colors text-left"
                  >
                    <div className="flex-1 grid grid-cols-2 sm:grid-cols-6 gap-4 text-sm">
                      <div>
                        <p className="text-xs text-slate-400">Date</p>
                        <p className="font-medium text-slate-700">{new Date(snap.created_at).toLocaleDateString()}</p>
                      </div>
                      <div>
                        <p className="text-xs text-slate-400">Period</p>
                        <p className="font-medium text-slate-600 text-xs">
                          {snap.date_range
                            ? formatDateRange(snap.date_range.start_date, snap.date_range.end_date)
                            : 'Last 30 days'}
                        </p>
                      </div>
                      <div>
                        <p className="text-xs text-slate-400">Campaigns</p>
                        <p className="font-medium text-slate-700">{snap.campaigns_count}</p>
                      </div>
                      <div>
                        <p className="text-xs text-slate-400">Spend</p>
                        <p className="font-medium text-slate-700">${(snap.total_spend || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</p>
                      </div>
                      <div>
                        <p className="text-xs text-slate-400">ACOS</p>
                        <p className="font-medium text-slate-700">{(snap.avg_acos || 0).toFixed(1)}%</p>
                      </div>
                      <div className="flex items-center justify-end gap-2">
                        <StatusBadge status={snap.status} />
                        <button
                          onClick={(e) => deleteSnapshot(e, snap.id)}
                          className="p-1 rounded hover:bg-red-50 text-slate-300 hover:text-red-500 transition-colors"
                          title="Delete audit"
                        >
                          <Trash2 size={14} />
                        </button>
                        {isExpanded ? <ChevronUp size={16} className="text-slate-400" /> : <ChevronDown size={16} className="text-slate-400" />}
                      </div>
                    </div>
                  </button>

                  {/* Expanded detail panel */}
                  {isExpanded && (
                    <div className="px-5 pb-6 border-t border-slate-100 bg-slate-50/50">
                      {isLoading ? (
                        <div className="py-8 flex items-center justify-center gap-2 text-sm text-slate-400">
                          <Loader2 size={16} className="animate-spin" /> Loading audit details...
                        </div>
                      ) : detail ? (
                        <SnapshotDetail snap={snap} detail={detail} />
                      ) : (
                        <div className="py-8 text-center text-sm text-slate-400">Failed to load details</div>
                      )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
