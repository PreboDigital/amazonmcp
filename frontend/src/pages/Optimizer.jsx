import { useState, useEffect } from 'react'
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
} from 'lucide-react'
import StatusBadge from '../components/StatusBadge'
import EmptyState from '../components/EmptyState'
import { optimizer } from '../lib/api'
import { useAccount } from '../lib/AccountContext'

export default function Optimizer() {
  const { activeAccount, activeAccountId } = useAccount()
  const [rules, setRules] = useState([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [runningId, setRunningId] = useState(null)
  const [preview, setPreview] = useState(null)
  const [error, setError] = useState(null)

  const [form, setForm] = useState({
    name: '',
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
        target_acos: parseFloat(form.target_acos),
        min_bid: parseFloat(form.min_bid),
        max_bid: parseFloat(form.max_bid),
        bid_step: parseFloat(form.bid_step),
        lookback_days: parseInt(form.lookback_days),
        min_clicks: parseInt(form.min_clicks),
      })
      setShowCreate(false)
      setForm({ name: '', target_acos: 30, min_bid: 0.02, max_bid: 100, bid_step: 0.10, lookback_days: 14, min_clicks: 10 })
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
                  <div className="mt-3 grid grid-cols-2 sm:grid-cols-5 gap-4 text-sm">
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
