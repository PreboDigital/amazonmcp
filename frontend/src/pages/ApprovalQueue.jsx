import { useState, useEffect } from 'react'
import {
  Shield,
  CheckCircle,
  XCircle,
  Clock,
  Loader2,
  ArrowUpRight,
  ArrowDownRight,
  Bot,
  User,
  TrendingUp,
  DollarSign,
  Target,
  Zap,
  Filter,
  CheckSquare,
  Send,
  AlertTriangle,
  X,
  ChevronDown,
  ChevronUp,
  Layers,
  Trash2,
} from 'lucide-react'
import StatusBadge from '../components/StatusBadge'
import EmptyState from '../components/EmptyState'
import { approvals } from '../lib/api'
import { useAccount } from '../lib/AccountContext'

const changeTypeLabels = {
  bid_update: { label: 'Bid Change', icon: Target, color: 'text-brand-600 bg-brand-50' },
  budget_update: { label: 'Budget Change', icon: DollarSign, color: 'text-emerald-600 bg-emerald-50' },
  campaign_state: { label: 'Campaign State', icon: Zap, color: 'text-amber-600 bg-amber-50' },
  campaign_create: { label: 'Create Campaign', icon: Zap, color: 'text-emerald-600 bg-emerald-50' },
  campaign_update: { label: 'Update Campaign', icon: Zap, color: 'text-amber-600 bg-amber-50' },
  campaign_delete: { label: 'Delete Campaign', icon: XCircle, color: 'text-red-600 bg-red-50' },
  campaign_bundle: { label: 'AI Campaign (Full)', icon: Zap, color: 'text-emerald-600 bg-emerald-50' },
  target_state: { label: 'Target State', icon: Target, color: 'text-purple-600 bg-purple-50' },
  target_create: { label: 'Create Target', icon: Target, color: 'text-blue-600 bg-blue-50' },
  target_update: { label: 'Update Target', icon: Target, color: 'text-purple-600 bg-purple-50' },
  target_delete: { label: 'Delete Target', icon: XCircle, color: 'text-red-600 bg-red-50' },
  keyword_add: { label: 'Add Keyword', icon: TrendingUp, color: 'text-blue-600 bg-blue-50' },
  keyword_remove: { label: 'Remove Keyword', icon: XCircle, color: 'text-red-600 bg-red-50' },
  ad_group_create: { label: 'Create Ad Group', icon: Layers, color: 'text-blue-600 bg-blue-50' },
  ad_group_update: { label: 'Update Ad Group', icon: Layers, color: 'text-amber-600 bg-amber-50' },
  ad_group_delete: { label: 'Delete Ad Group', icon: XCircle, color: 'text-red-600 bg-red-50' },
  ad_create: { label: 'Create Ad', icon: Zap, color: 'text-blue-600 bg-blue-50' },
  ad_update: { label: 'Update Ad', icon: Zap, color: 'text-amber-600 bg-amber-50' },
  ad_delete: { label: 'Delete Ad', icon: XCircle, color: 'text-red-600 bg-red-50' },
  harvest: { label: 'Keyword Harvest', icon: Zap, color: 'text-purple-600 bg-purple-50' },
}

const sourceLabels = {
  manual: { label: 'Manual', icon: User, color: 'text-slate-600' },
  ai_optimizer: { label: 'AI Optimizer', icon: Bot, color: 'text-brand-600' },
  ai_assistant: { label: 'AI Assistant', icon: Bot, color: 'text-brand-600' },
  ai_insight: { label: 'AI Insight', icon: Bot, color: 'text-purple-600' },
  bid_optimizer: { label: 'Bid Optimizer', icon: TrendingUp, color: 'text-emerald-600' },
  harvester: { label: 'Harvester', icon: Zap, color: 'text-amber-600' },
}

export default function ApprovalQueue() {
  const { activeAccount, activeAccountId, activeProfileId } = useAccount()
  const [changes, setChanges] = useState([])
  const [summary, setSummary] = useState(null)
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('pending')
  const [selected, setSelected] = useState(new Set())
  const [expandedId, setExpandedId] = useState(null)
  const [applying, setApplying] = useState(false)
  const [reviewNote, setReviewNote] = useState('')
  const [showBatchReview, setShowBatchReview] = useState(false)
  const [error, setError] = useState(null)
  const [successMsg, setSuccessMsg] = useState(null)
  const [deletingId, setDeletingId] = useState(null)
  const [deletingBatch, setDeletingBatch] = useState(false)

  useEffect(() => {
    loadData()
    setSelected(new Set())
  }, [activeAccountId, activeProfileId, filter])

  async function loadData() {
    setLoading(true)
    try {
      const [changesData, summaryData] = await Promise.allSettled([
        approvals.list(activeAccountId, filter !== 'all' ? filter : null, { profile_id: activeProfileId || undefined }),
        approvals.summary(activeAccountId, activeProfileId),
      ])
      setChanges(changesData.status === 'fulfilled' ? changesData.value : [])
      setSummary(summaryData.status === 'fulfilled' ? summaryData.value : null)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  async function reviewSingle(changeId, action) {
    setError(null)
    try {
      await approvals.review(changeId, action)
      flash(`Change ${action}d successfully`)
      await loadData()
    } catch (err) {
      setError(err.message)
    }
  }

  async function batchReview(action) {
    if (selected.size === 0) return
    setError(null)
    try {
      await approvals.batchReview([...selected], action, reviewNote || null)
      flash(`${selected.size} changes ${action}d`)
      setSelected(new Set())
      setShowBatchReview(false)
      setReviewNote('')
      await loadData()
    } catch (err) {
      setError(err.message)
    }
  }

  async function deleteSingle(changeId) {
    if (!window.confirm('Delete this change? This cannot be undone.')) return
    setDeletingId(changeId)
    setError(null)
    try {
      await approvals.delete(changeId)
      flash('Change deleted')
      await loadData()
    } catch (err) {
      setError(err.message)
    } finally {
      setDeletingId(null)
    }
  }

  async function batchDelete() {
    if (selected.size === 0) return
    if (!window.confirm(`Delete ${selected.size} change(s)? This cannot be undone.`)) return
    setDeletingBatch(true)
    setError(null)
    try {
      const ids = [...selected]
      await Promise.all(ids.map((id) => approvals.delete(id)))
      flash(`${ids.length} changes deleted`)
      setSelected(new Set())
      await loadData()
    } catch (err) {
      setError(err.message)
    } finally {
      setDeletingBatch(false)
    }
  }

  async function applyApproved() {
    const approvedIds = changes.filter(c => c.status === 'approved').map(c => c.id)
    if (approvedIds.length === 0) return

    setApplying(true)
    setError(null)
    try {
      const result = await approvals.apply(approvedIds)
      flash(`Applied ${result.applied} changes to Amazon Ads (${result.failed} failed)`)
      await loadData()
    } catch (err) {
      setError(err.message)
    } finally {
      setApplying(false)
    }
  }

  function toggleSelect(id) {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function toggleSelectAll() {
    if (selected.size === changes.length) {
      setSelected(new Set())
    } else {
      setSelected(new Set(changes.map(c => c.id)))
    }
  }

  function flash(msg) {
    setSuccessMsg(msg)
    setTimeout(() => setSuccessMsg(null), 4000)
  }

  const approvedCount = changes.filter(c => c.status === 'approved').length

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="flex items-center justify-center w-10 h-10 rounded-xl bg-gradient-to-br from-amber-400 to-orange-500 text-white">
            <Shield size={20} />
          </div>
          <div>
            <h1 className="text-xl font-bold text-slate-900 tracking-tight">Approval Queue</h1>
            <p className="text-xs text-slate-500">
              {activeAccount
                ? <>Review changes for <span className="font-medium text-slate-700">{activeAccount.account_name || activeAccount.name}</span> before pushing to Amazon Ads</>
                : 'Review and approve changes before they are applied to your campaigns'}
            </p>
          </div>
        </div>
        {approvedCount > 0 && (
          <button onClick={applyApproved} disabled={applying} className="btn-primary">
            {applying ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />}
            Push {approvedCount} to Ads Manager
          </button>
        )}
      </div>

      {/* Success / Error */}
      {successMsg && (
        <div className="card bg-emerald-50 border-emerald-200 p-4 flex items-center gap-3">
          <CheckCircle size={18} className="text-emerald-600 shrink-0" />
          <p className="text-sm text-emerald-800">{successMsg}</p>
          <button onClick={() => setSuccessMsg(null)} className="ml-auto"><X size={14} className="text-emerald-400" /></button>
        </div>
      )}

      {error && (
        <div className="card bg-red-50 border-red-200 p-4 flex items-center gap-3">
          <AlertTriangle size={18} className="text-red-600 shrink-0" />
          <p className="text-sm text-red-800">{error}</p>
          <button onClick={() => setError(null)} className="ml-auto"><X size={14} className="text-red-400" /></button>
        </div>
      )}

      {/* Summary Cards */}
      {summary && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <button
            onClick={() => setFilter('pending')}
            className={`card p-4 text-left transition-all ${filter === 'pending' ? 'ring-2 ring-amber-500/30 border-amber-200' : 'hover:border-slate-300'}`}
          >
            <div className="flex items-center gap-2 mb-1">
              <Clock size={14} className="text-amber-500" />
              <p className="text-xs text-slate-500">Pending</p>
            </div>
            <p className="text-2xl font-bold text-slate-900">{summary.total_pending || 0}</p>
          </button>
          <button
            onClick={() => setFilter('approved')}
            className={`card p-4 text-left transition-all ${filter === 'approved' ? 'ring-2 ring-emerald-500/30 border-emerald-200' : 'hover:border-slate-300'}`}
          >
            <div className="flex items-center gap-2 mb-1">
              <CheckCircle size={14} className="text-emerald-500" />
              <p className="text-xs text-slate-500">Approved</p>
            </div>
            <p className="text-2xl font-bold text-slate-900">{summary.total_approved || 0}</p>
          </button>
          <button
            onClick={() => setFilter('rejected')}
            className={`card p-4 text-left transition-all ${filter === 'rejected' ? 'ring-2 ring-red-500/30 border-red-200' : 'hover:border-slate-300'}`}
          >
            <div className="flex items-center gap-2 mb-1">
              <XCircle size={14} className="text-red-500" />
              <p className="text-xs text-slate-500">Rejected</p>
            </div>
            <p className="text-2xl font-bold text-slate-900">{summary.total_rejected || 0}</p>
          </button>
          <button
            onClick={() => setFilter('applied')}
            className={`card p-4 text-left transition-all ${filter === 'applied' ? 'ring-2 ring-brand-500/30 border-brand-200' : 'hover:border-slate-300'}`}
          >
            <div className="flex items-center gap-2 mb-1">
              <Send size={14} className="text-brand-500" />
              <p className="text-xs text-slate-500">Applied</p>
            </div>
            <p className="text-2xl font-bold text-slate-900">{summary.total_applied || 0}</p>
          </button>
        </div>
      )}

      {/* Batch Actions Bar */}
      {selected.size > 0 && (filter === 'pending' || filter === 'rejected') && (
        <div className="card bg-brand-50 border-brand-200 p-4 flex items-center gap-4">
          <div className="flex items-center gap-2">
            <CheckSquare size={16} className="text-brand-600" />
            <p className="text-sm font-medium text-brand-800">{selected.size} selected</p>
          </div>
          <div className="flex-1" />
          {filter === 'pending' && (
            <>
              <button onClick={() => batchReview('approve')} className="btn-primary text-xs">
                <CheckCircle size={14} /> Approve All
              </button>
              <button onClick={() => batchReview('reject')} className="btn-ghost text-red-600 text-xs hover:bg-red-50">
                <XCircle size={14} /> Reject All
              </button>
            </>
          )}
          <button onClick={batchDelete} disabled={deletingBatch} className="btn-ghost text-red-600 text-xs hover:bg-red-50">
            {deletingBatch ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
            Delete All
          </button>
          <button onClick={() => setSelected(new Set())} className="btn-ghost text-xs">
            Clear
          </button>
        </div>
      )}

      {/* How It Works Banner */}
      <div className="card bg-gradient-to-r from-amber-50 to-orange-50 border-amber-100 p-5">
        <div className="flex items-start gap-3">
          <Shield size={18} className="text-amber-600 mt-0.5 shrink-0" />
          <div>
            <h3 className="text-sm font-semibold text-amber-900">Safe Change Management</h3>
            <p className="text-sm text-amber-700 mt-1">
              All changes — from AI recommendations, bid optimizations, and keyword harvests —
              land here first. <strong>Review each change</strong>, then approve or reject.
              Only approved changes can be pushed to Amazon Ads Manager.
            </p>
          </div>
        </div>
      </div>

      {/* Changes List */}
      {loading ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="animate-spin text-slate-400" size={24} />
        </div>
      ) : changes.length === 0 ? (
        <EmptyState
          icon={Shield}
          title={filter === 'pending' ? 'No pending changes' : `No ${filter} changes`}
          description={
            filter === 'pending'
              ? 'Run the AI optimizer or bid optimizer to generate recommendations that will appear here for review.'
              : `No changes with status "${filter}" found.`
          }
        />
      ) : (
        <div className="space-y-2">
          {/* Select all header */}
          {(filter === 'pending' || filter === 'rejected') && (
            <div className="flex items-center gap-3 px-2 py-1">
              <button onClick={toggleSelectAll} className="text-xs text-slate-500 hover:text-brand-600 transition-colors">
                {selected.size === changes.length ? 'Deselect all' : 'Select all'}
              </button>
            </div>
          )}

          {changes.map((change) => {
            const typeInfo = changeTypeLabels[change.change_type] || changeTypeLabels.bid_update
            const sourceInfo = sourceLabels[change.source] || sourceLabels.manual
            const TypeIcon = typeInfo.icon
            const SourceIcon = sourceInfo.icon
            const isExpanded = expandedId === change.id
            const isSelected = selected.has(change.id)

            return (
              <div key={change.id} className={`card transition-all ${
                isSelected ? 'ring-2 ring-brand-500/30 border-brand-200' : ''
              }`}>
                <div className="p-4 flex items-start gap-3">
                  {/* Checkbox (pending and rejected are deletable/selectable) */}
                  {(change.status === 'pending' || change.status === 'rejected') && (
                    <button
                      onClick={() => toggleSelect(change.id)}
                      className={`w-5 h-5 rounded border-2 flex items-center justify-center shrink-0 mt-0.5 transition-all ${
                        isSelected ? 'bg-brand-600 border-brand-600 text-white' : 'border-slate-300 hover:border-brand-400'
                      }`}
                    >
                      {isSelected && <CheckCircle size={12} />}
                    </button>
                  )}

                  {/* Type Icon */}
                  <div className={`flex items-center justify-center w-9 h-9 rounded-lg shrink-0 ${typeInfo.color}`}>
                    <TypeIcon size={16} />
                  </div>

                  {/* Content */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-sm font-semibold text-slate-900">{typeInfo.label}</span>
                      <StatusBadge status={change.status} />
                      <div className={`flex items-center gap-1 text-[10px] font-medium ${sourceInfo.color}`}>
                        <SourceIcon size={10} />
                        {sourceInfo.label}
                      </div>
                      {change.confidence && (
                        <span className="text-[10px] font-medium text-slate-400">
                          {Math.round(change.confidence * 100)}% confidence
                        </span>
                      )}
                    </div>

                    <div className="mt-1.5 flex items-center gap-3 text-sm">
                      {change.entity_name && (
                        <span className="text-slate-600 truncate max-w-[200px]">{change.entity_name}</span>
                      )}
                      {change.campaign_name && (
                        <span className="text-xs text-slate-400 truncate max-w-[200px]">in {change.campaign_name}</span>
                      )}
                    </div>

                    {/* Value change */}
                    {(change.current_value || change.proposed_value) && (
                      <div className="mt-2 flex items-center gap-2 text-sm">
                        <span className="text-slate-400 font-mono text-xs">{change.current_value || '—'}</span>
                        <span className="text-slate-300">&rarr;</span>
                        <span className="font-semibold text-slate-900 font-mono text-xs">{change.proposed_value || '—'}</span>
                      </div>
                    )}

                    {/* AI Reasoning */}
                    {change.ai_reasoning && (
                      <p className="mt-1.5 text-xs text-slate-500 italic line-clamp-2">
                        <Bot size={10} className="inline mr-1" />
                        {change.ai_reasoning}
                      </p>
                    )}

                    {/* Estimated Impact */}
                    {change.estimated_impact && (
                      <p className="mt-1 text-xs font-medium text-emerald-600">
                        Impact: {change.estimated_impact}
                      </p>
                    )}
                  </div>

                  {/* Actions */}
                  <div className="flex items-center gap-1.5 shrink-0 ml-2">
                    {change.status === 'pending' && (
                      <>
                        <button
                          onClick={() => reviewSingle(change.id, 'approve')}
                          className="p-2 rounded-lg hover:bg-emerald-50 text-emerald-600 transition-colors"
                          title="Approve"
                        >
                          <CheckCircle size={18} />
                        </button>
                        <button
                          onClick={() => reviewSingle(change.id, 'reject')}
                          className="p-2 rounded-lg hover:bg-red-50 text-red-500 transition-colors"
                          title="Reject"
                        >
                          <XCircle size={18} />
                        </button>
                      </>
                    )}
                    {(change.status === 'pending' || change.status === 'rejected') && (
                      <button
                        onClick={() => deleteSingle(change.id)}
                        disabled={deletingId === change.id}
                        className="p-2 rounded-lg hover:bg-red-50 text-slate-400 hover:text-red-600 transition-colors disabled:opacity-50"
                        title="Delete"
                      >
                        {deletingId === change.id ? <Loader2 size={18} className="animate-spin" /> : <Trash2 size={18} />}
                      </button>
                    )}
                    <button
                      onClick={() => setExpandedId(isExpanded ? null : change.id)}
                      className="p-2 rounded-lg hover:bg-slate-100 text-slate-400 transition-colors"
                    >
                      {isExpanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                    </button>
                  </div>
                </div>

                {/* Expanded Detail */}
                {isExpanded && (
                  <div className="px-4 pb-4 border-t border-slate-100 pt-3">
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-xs">
                      <div>
                        <p className="text-slate-400">Entity ID</p>
                        <p className="font-mono text-slate-600 truncate">{change.entity_id || '—'}</p>
                      </div>
                      <div>
                        <p className="text-slate-400">Campaign ID</p>
                        <p className="font-mono text-slate-600 truncate">{change.campaign_id || '—'}</p>
                      </div>
                      <div>
                        <p className="text-slate-400">Batch</p>
                        <p className="text-slate-600 truncate">{change.batch_label || '—'}</p>
                      </div>
                      <div>
                        <p className="text-slate-400">Created</p>
                        <p className="text-slate-600">{new Date(change.created_at).toLocaleString()}</p>
                      </div>
                    </div>

                    {change.review_note && (
                      <div className="mt-3 bg-slate-50 rounded-lg p-3">
                        <p className="text-xs text-slate-400 mb-1">Review Note</p>
                        <p className="text-sm text-slate-700">{change.review_note}</p>
                      </div>
                    )}

                    {change.error_message && (
                      <div className="mt-3 bg-red-50 rounded-lg p-3">
                        <p className="text-xs text-red-400 mb-1">Error</p>
                        <p className="text-sm text-red-700">{change.error_message}</p>
                      </div>
                    )}

                    {change.apply_result && (
                      <div className="mt-3 bg-emerald-50 rounded-lg p-3">
                        <p className="text-xs text-emerald-400 mb-1">Applied Result</p>
                        <pre className="text-xs text-emerald-700 overflow-x-auto">
                          {JSON.stringify(change.apply_result, null, 2)}
                        </pre>
                      </div>
                    )}

                    {change.change_detail && (
                      <details className="mt-3">
                        <summary className="text-xs text-slate-400 cursor-pointer hover:text-slate-600">
                          View full change detail
                        </summary>
                        <pre className="mt-2 bg-slate-800 text-emerald-300 text-xs p-3 rounded-lg overflow-x-auto">
                          {JSON.stringify(change.change_detail, null, 2)}
                        </pre>
                      </details>
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
