import { useState, useEffect, useRef } from 'react'
import {
  TrendingUp,
  Plus,
  Play,
  Eye,
  Loader2,
  Trash2,
  X,
  ArrowUpRight,
  ArrowDownRight,
  CheckCircle,
  Search,
  ChevronDown,
  RefreshCw,
} from 'lucide-react'
import StatusBadge from '../components/StatusBadge'
import EmptyState from '../components/EmptyState'
import { optimizer, campaignManager } from '../lib/api'
import { useAccount } from '../lib/AccountContext'

// ── Campaign Multi-Select for Bid Rules ────────────────────────────────────
function CampaignSelector({ campaigns, selectedIds, onChange, loading, onRefresh }) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const ref = useRef(null)

  useEffect(() => {
    function onClickOutside(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', onClickOutside)
    return () => document.removeEventListener('mousedown', onClickOutside)
  }, [])

  const filtered = campaigns.filter((c) => {
    const q = search.toLowerCase()
    return (c.campaign_name || '').toLowerCase().includes(q) || (c.amazon_campaign_id || '').toLowerCase().includes(q)
  })

  const autoCampaigns = filtered.filter(c => c.targeting_type?.toLowerCase() === 'auto')
  const manualCampaigns = filtered.filter(c => c.targeting_type?.toLowerCase() === 'manual')
  const otherCampaigns = filtered.filter(c => !['auto', 'manual'].includes(c.targeting_type?.toLowerCase()))

  function toggle(campaign) {
    const id = campaign.amazon_campaign_id
    if (selectedIds.includes(id)) {
      onChange(selectedIds.filter((x) => x !== id))
    } else {
      onChange([...selectedIds, id])
    }
  }

  function isSelected(campaign) {
    return selectedIds.includes(campaign.amazon_campaign_id)
  }

  const selectedCampaigns = campaigns.filter(c => selectedIds.includes(c.amazon_campaign_id))

  return (
    <div className="relative" ref={ref}>
      <label className="label">Campaigns</label>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="input flex items-center justify-between gap-2 text-left min-h-[42px] cursor-pointer"
      >
        <div className="flex-1 min-w-0">
          {selectedIds.length === 0 ? (
            <span className="text-slate-400">All campaigns (or select specific ones)</span>
          ) : (
            <div className="flex flex-wrap gap-1">
              {selectedCampaigns.slice(0, 3).map((c) => (
                <span key={c.amazon_campaign_id} className="inline-flex items-center gap-1 bg-brand-50 text-brand-700 text-xs font-medium px-2 py-0.5 rounded-md">
                  {c.campaign_name || c.amazon_campaign_id}
                  <button type="button" onClick={(e) => { e.stopPropagation(); toggle(c) }} className="hover:text-brand-900">
                    <X size={10} />
                  </button>
                </span>
              ))}
              {selectedCampaigns.length > 3 && <span className="text-xs text-slate-500 self-center">+{selectedCampaigns.length - 3} more</span>}
            </div>
          )}
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          {selectedIds.length > 0 && (
            <span className="bg-brand-600 text-white text-[10px] font-bold w-5 h-5 rounded-full flex items-center justify-center">{selectedIds.length}</span>
          )}
          <ChevronDown size={16} className={`text-slate-400 transition-transform ${open ? 'rotate-180' : ''}`} />
        </div>
      </button>

      {open && (
        <div className="absolute z-50 mt-1 w-full bg-white border border-slate-200 rounded-xl shadow-xl max-h-[280px] overflow-hidden flex flex-col">
          <div className="p-2 border-b border-slate-100 flex items-center gap-2">
            <Search size={14} className="text-slate-400 shrink-0" />
            <input type="text" placeholder="Search campaigns..." value={search} onChange={(e) => setSearch(e.target.value)} className="flex-1 text-sm border-0 outline-none bg-transparent placeholder:text-slate-300" autoFocus />
            <button type="button" onClick={onRefresh} className="p-1 hover:bg-slate-100 rounded transition-colors" title="Refresh campaigns">
              <RefreshCw size={12} className={`text-slate-400 ${loading ? 'animate-spin' : ''}`} />
            </button>
          </div>
          <div className="px-3 py-1.5 border-b border-slate-100 flex items-center gap-2">
            <button type="button" onClick={() => onChange([])} className="text-[11px] font-medium text-slate-500 hover:text-slate-700 transition-colors">Clear (use all campaigns)</button>
          </div>
          <div className="overflow-y-auto flex-1">
            {loading ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 size={18} className="animate-spin text-slate-400" />
                <span className="ml-2 text-sm text-slate-400">Loading campaigns...</span>
              </div>
            ) : filtered.length === 0 ? (
              <div className="py-8 text-center text-sm text-slate-400">
                {search ? 'No campaigns match your search' : 'No campaigns found. Sync campaigns in Campaign Manager first.'}
              </div>
            ) : (
              <>
                {[
                  { label: 'Auto Campaigns', items: autoCampaigns },
                  { label: 'Manual Campaigns', items: manualCampaigns },
                  { label: 'Other', items: otherCampaigns },
                ].filter(g => g.items.length > 0).map(group => (
                  <div key={group.label}>
                    <div className="px-3 py-1.5 text-[10px] uppercase tracking-wider font-semibold text-slate-500 bg-slate-50 sticky top-0">
                      {group.label} ({group.items.length})
                    </div>
                    {group.items.map((c) => (
                      <button
                        key={c.amazon_campaign_id}
                        type="button"
                        onClick={() => toggle(c)}
                        className={`w-full px-3 py-2 text-left text-sm flex items-center gap-2 hover:bg-slate-50 transition-colors ${isSelected(c) ? 'bg-brand-50 text-brand-700' : 'text-slate-700'}`}
                      >
                        <span className={`w-4 h-4 rounded border flex items-center justify-center shrink-0 ${isSelected(c) ? 'bg-brand-600 border-brand-600 text-white' : 'border-slate-300'}`}>
                          {isSelected(c) ? '✓' : ''}
                        </span>
                        <span className="truncate">{c.campaign_name || c.amazon_campaign_id}</span>
                      </button>
                    ))}
                  </div>
                ))}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

export default function Optimizer() {
  const { activeAccount, activeAccountId } = useAccount()
  const [rules, setRules] = useState([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [runningId, setRunningId] = useState(null)
  const [preview, setPreview] = useState(null)
  const [error, setError] = useState(null)
  const [campaigns, setCampaigns] = useState([])
  const [campaignsLoading, setCampaignsLoading] = useState(false)

  const [form, setForm] = useState({
    name: '',
    campaign_ids: [],
    target_acos: 30,
    min_bid: 0.02,
    max_bid: 100,
    bid_step: 0.10,
    lookback_days: 14,
    min_clicks: 10,
  })

  useEffect(() => {
    setPreview(null)
    loadRules()
  }, [activeAccountId])

  useEffect(() => {
    if (showCreate && activeAccountId) loadCampaigns()
  }, [showCreate, activeAccountId])

  async function loadCampaigns() {
    setCampaignsLoading(true)
    try {
      const data = await campaignManager.listCampaigns(activeAccountId, { page_size: 500 })
      setCampaigns(data.campaigns || [])
    } catch {
      setCampaigns([])
    } finally {
      setCampaignsLoading(false)
    }
  }

  async function loadRules() {
    setLoading(true)
    try {
      const data = await optimizer.rules(activeAccountId)
      setRules(data)
    } catch (err) {
      // Ignore
    } finally {
      setLoading(false)
    }
  }

  async function createRule(e) {
    e.preventDefault()
    setError(null)
    try {
      await optimizer.createRule({
        ...form,
        credential_id: activeAccountId,
        campaign_ids: form.campaign_ids?.length ? form.campaign_ids : null,
        target_acos: parseFloat(form.target_acos),
        min_bid: parseFloat(form.min_bid),
        max_bid: parseFloat(form.max_bid),
        bid_step: parseFloat(form.bid_step),
        lookback_days: parseInt(form.lookback_days),
        min_clicks: parseInt(form.min_clicks),
      })
      setShowCreate(false)
      setForm({ name: '', campaign_ids: [], target_acos: 30, min_bid: 0.02, max_bid: 100, bid_step: 0.10, lookback_days: 14, min_clicks: 10 })
      await loadRules()
    } catch (err) {
      setError(err.message)
    }
  }

  async function runOptimization(ruleId, dryRun = true) {
    setRunningId(ruleId)
    setError(null)
    setPreview(null)
    try {
      const result = await optimizer.run(ruleId, dryRun, activeAccountId)
      if (dryRun) {
        setPreview({ ruleId, ...result })
      } else {
        await loadRules()
      }
    } catch (err) {
      setError(err.message)
    } finally {
      setRunningId(null)
    }
  }

  async function deleteRule(ruleId) {
    if (!confirm('Delete this optimization rule?')) return
    try {
      await optimizer.deleteRule(ruleId)
      await loadRules()
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 tracking-tight">Bid Optimizer</h1>
          <p className="mt-1 text-sm text-slate-500">
            {activeAccount
              ? <>Optimizing <span className="font-medium text-slate-700">{activeAccount.account_name || activeAccount.name}</span></>
              : 'Set ACOS targets and let the optimizer adjust bids to hit your goals'}
          </p>
        </div>
        <button onClick={() => setShowCreate(true)} disabled={!activeAccount} className="btn-primary">
          <Plus size={16} /> New Bid Rule
        </button>
      </div>

      {!activeAccount && (
        <div className="card bg-amber-50 border-amber-200 p-4 text-sm text-amber-800">
          Add and select an account in Settings before creating bid rules.
        </div>
      )}

      {/* Info */}
      <div className="card bg-gradient-to-r from-emerald-50 to-teal-50 border-emerald-100 p-5">
        <div className="flex items-start gap-3">
          <TrendingUp size={18} className="text-emerald-600 mt-0.5 shrink-0" />
          <div>
            <h3 className="text-sm font-semibold text-emerald-900">How Bid Optimization Works</h3>
            <p className="text-sm text-emerald-700 mt-1">
              Set your target ACOS and the optimizer analyzes each keyword/target's performance.
              Bids are <strong>increased</strong> for targets performing well below target ACOS (room to grow)
              and <strong>decreased</strong> for targets above target. Always preview before applying.
            </p>
          </div>
        </div>
      </div>

      {error && (
        <div className="card bg-red-50 border-red-200 p-4 text-sm text-red-700">{error}</div>
      )}

      {/* Create Modal */}
      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
          <div className="card w-full max-w-lg p-6 space-y-5">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-lg font-semibold text-slate-900">New Bid Optimization Rule</h2>
                {activeAccount && <p className="text-xs text-slate-400 mt-0.5">For {activeAccount.account_name || activeAccount.name}</p>}
              </div>
              <button onClick={() => setShowCreate(false)} className="p-1.5 hover:bg-slate-100 rounded-lg transition-colors">
                <X size={18} className="text-slate-400" />
              </button>
            </div>

            <form onSubmit={createRule} className="space-y-4">
              <div>
                <label className="label">Rule Name</label>
                <input type="text" className="input" placeholder="e.g., All SP Campaigns — 25% ACOS" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
              </div>
              <CampaignSelector
                campaigns={campaigns}
                selectedIds={form.campaign_ids || []}
                onChange={(ids) => setForm({ ...form, campaign_ids: ids })}
                loading={campaignsLoading}
                onRefresh={loadCampaigns}
              />
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="label">Target ACOS (%)</label>
                  <input type="number" step="0.1" min="1" className="input" value={form.target_acos} onChange={(e) => setForm({ ...form, target_acos: e.target.value })} required />
                </div>
                <div>
                  <label className="label">Bid Step ($)</label>
                  <input type="number" step="0.01" min="0.01" className="input" value={form.bid_step} onChange={(e) => setForm({ ...form, bid_step: e.target.value })} />
                  <p className="text-xs text-slate-400 mt-1">Amount to adjust per cycle</p>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="label">Min Bid ($)</label>
                  <input type="number" step="0.01" min="0.02" className="input" value={form.min_bid} onChange={(e) => setForm({ ...form, min_bid: e.target.value })} />
                </div>
                <div>
                  <label className="label">Max Bid ($)</label>
                  <input type="number" step="0.01" min="0.02" className="input" value={form.max_bid} onChange={(e) => setForm({ ...form, max_bid: e.target.value })} />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="label">Min Clicks</label>
                  <input type="number" min="1" className="input" value={form.min_clicks} onChange={(e) => setForm({ ...form, min_clicks: e.target.value })} />
                  <p className="text-xs text-slate-400 mt-1">Before making bid decisions</p>
                </div>
                <div>
                  <label className="label">Lookback (days)</label>
                  <input type="number" min="1" className="input" value={form.lookback_days} onChange={(e) => setForm({ ...form, lookback_days: e.target.value })} />
                </div>
              </div>
              <div className="flex justify-end gap-3 pt-2">
                <button type="button" onClick={() => setShowCreate(false)} className="btn-secondary">Cancel</button>
                <button type="submit" className="btn-primary">Create Rule</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Preview Panel */}
      {preview && (
        <div className="card border-brand-200 shadow-lg">
          <div className="p-5 border-b border-slate-100">
            <div className="flex items-center justify-between">
              <h3 className="text-base font-semibold text-slate-900">Optimization Preview</h3>
              <div className="flex items-center gap-2">
                <button onClick={() => runOptimization(preview.ruleId, false)} className="btn-primary text-sm">
                  <CheckCircle size={14} /> Apply Changes
                </button>
                <button onClick={() => setPreview(null)} className="btn-ghost">
                  <X size={14} /> Dismiss
                </button>
              </div>
            </div>
            <div className="mt-4 grid grid-cols-2 sm:grid-cols-4 gap-4">
              <div className="bg-slate-50 rounded-lg p-3">
                <p className="text-xs text-slate-400">Analyzed</p>
                <p className="text-lg font-bold text-slate-900">{preview.summary?.total_analyzed || 0}</p>
              </div>
              <div className="bg-emerald-50 rounded-lg p-3">
                <p className="text-xs text-emerald-600">Increases</p>
                <p className="text-lg font-bold text-emerald-700">{preview.summary?.increases || 0}</p>
              </div>
              <div className="bg-red-50 rounded-lg p-3">
                <p className="text-xs text-red-600">Decreases</p>
                <p className="text-lg font-bold text-red-700">{preview.summary?.decreases || 0}</p>
              </div>
              <div className="bg-slate-50 rounded-lg p-3">
                <p className="text-xs text-slate-400">Unchanged</p>
                <p className="text-lg font-bold text-slate-900">{preview.summary?.unchanged || 0}</p>
              </div>
            </div>
          </div>
          {preview.changes?.length > 0 && (
            <div className="max-h-80 overflow-y-auto divide-y divide-slate-100">
              {preview.changes.map((change, i) => (
                <div key={i} className="px-5 py-3 flex items-center gap-4 text-sm">
                  <div className={`flex items-center justify-center w-7 h-7 rounded-full ${
                    change.direction === 'increase' ? 'bg-emerald-50 text-emerald-600' : 'bg-red-50 text-red-600'
                  }`}>
                    {change.direction === 'increase' ? <ArrowUpRight size={14} /> : <ArrowDownRight size={14} />}
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="font-mono text-xs text-slate-500 truncate">{change.target_id}</p>
                    <p className="text-xs text-slate-400 mt-0.5">{change.reason}</p>
                  </div>
                  <div className="text-right shrink-0">
                    <p className="text-sm">
                      <span className="text-slate-400">${change.current_bid}</span>
                      <span className="mx-1.5 text-slate-300">&rarr;</span>
                      <span className="font-semibold text-slate-900">${change.new_bid}</span>
                    </p>
                    <p className={`text-xs font-medium ${change.direction === 'increase' ? 'text-emerald-600' : 'text-red-600'}`}>
                      {change.change > 0 ? '+' : ''}{change.change}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Rules */}
      {loading ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="animate-spin text-slate-400" size={24} />
        </div>
      ) : rules.length === 0 ? (
        <EmptyState
          icon={TrendingUp}
          title="No optimization rules"
          description={activeAccount
            ? `Create a rule for ${activeAccount.account_name || activeAccount.name} to start optimizing bids.`
            : 'Select an account, then create a bid rule.'}
          action={activeAccount && (
            <button onClick={() => setShowCreate(true)} className="btn-primary">
              <Plus size={16} /> Create First Rule
            </button>
          )}
        />
      ) : (
        <div className="space-y-3">
          {rules.map((rule) => (
            <div key={rule.id} className="card p-5">
              <div className="flex items-start justify-between">
                <div className="flex-1">
                  <div className="flex items-center gap-3">
                    <h3 className="text-sm font-semibold text-slate-900">{rule.name}</h3>
                    <StatusBadge status={rule.status} />
                    {rule.is_active && <span className="badge-green">Active</span>}
                  </div>
                  <div className="mt-3 grid grid-cols-2 sm:grid-cols-6 gap-4 text-sm">
                    <div>
                      <p className="text-xs text-slate-400">Campaigns</p>
                      <p className="text-slate-700">{rule.campaign_ids?.length ? `${rule.campaign_ids.length} selected` : 'All campaigns'}</p>
                    </div>
                    <div>
                      <p className="text-xs text-slate-400">Target ACOS</p>
                      <p className="font-bold text-brand-600">{rule.target_acos}%</p>
                    </div>
                    <div>
                      <p className="text-xs text-slate-400">Bid Range</p>
                      <p className="text-slate-700">${rule.min_bid} – ${rule.max_bid}</p>
                    </div>
                    <div>
                      <p className="text-xs text-slate-400">Bid Step</p>
                      <p className="text-slate-700">${rule.bid_step}</p>
                    </div>
                    <div>
                      <p className="text-xs text-slate-400">Targets Adjusted</p>
                      <p className="font-medium text-slate-700">{rule.total_targets_adjusted ?? rule.targets_adjusted ?? 0}</p>
                    </div>
                    <div>
                      <p className="text-xs text-slate-400">Last Run</p>
                      <p className="text-slate-700">{rule.last_run_at ? new Date(rule.last_run_at).toLocaleDateString() : 'Never'}</p>
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-2 ml-4">
                  <button onClick={() => runOptimization(rule.id, true)} disabled={runningId === rule.id} className="btn-secondary text-xs">
                    {runningId === rule.id ? <Loader2 size={14} className="animate-spin" /> : <Eye size={14} />} Preview
                  </button>
                  <button onClick={() => runOptimization(rule.id, false)} disabled={runningId === rule.id} className="btn-primary text-xs">
                    <Play size={14} /> Apply
                  </button>
                  <button onClick={() => deleteRule(rule.id)} className="btn-ghost text-red-500 hover:bg-red-50">
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
