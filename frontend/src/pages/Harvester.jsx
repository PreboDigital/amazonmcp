import { useState, useEffect, useRef } from 'react'
import {
  Sparkles,
  Plus,
  Loader2,
  Trash2,
  X,
  Zap,
  Search,
  Check,
  ChevronDown,
  ChevronUp,
  ChevronRight,
  RefreshCw,
  ArrowRight,
  Shield,
  Clock,
  Eye,
  Edit3,
  AlertTriangle,
  CheckCircle,
  Info,
  Send,
  Target,
  Calendar,
  Hash,
  Filter,
  Ban,
  GitBranch,
  ArrowDown,
} from 'lucide-react'
import StatusBadge from '../components/StatusBadge'
import EmptyState from '../components/EmptyState'
import { harvest } from '../lib/api'
import { useAccount } from '../lib/AccountContext'

// ── Campaign Multi-Select Dropdown ────────────────────────────────────

function CampaignSelector({ campaigns, selected, onChange, loading, onRefresh, filterType }) {
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
    const matchesSearch = (c.campaign_name || '').toLowerCase().includes(q) || (c.amazon_campaign_id || '').toLowerCase().includes(q)
    if (filterType) return matchesSearch && c.targeting_type?.toLowerCase() === filterType.toLowerCase()
    return matchesSearch
  })

  const autoCampaigns = filtered.filter(c => c.targeting_type?.toLowerCase() === 'auto')
  const manualCampaigns = filtered.filter(c => c.targeting_type?.toLowerCase() === 'manual')
  const otherCampaigns = filtered.filter(c => !['auto', 'manual'].includes(c.targeting_type?.toLowerCase()))

  function toggle(campaign) {
    const exists = selected.find((s) => s.amazon_campaign_id === campaign.amazon_campaign_id)
    if (exists) {
      onChange(selected.filter((s) => s.amazon_campaign_id !== campaign.amazon_campaign_id))
    } else {
      onChange([...selected, campaign])
    }
  }

  function isSelected(campaign) {
    return selected.some((s) => s.amazon_campaign_id === campaign.amazon_campaign_id)
  }

  function selectAllAuto() {
    const autoOnly = campaigns.filter(c => c.targeting_type?.toLowerCase() === 'auto')
    onChange(autoOnly)
  }

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="input flex items-center justify-between gap-2 text-left min-h-[42px] cursor-pointer"
      >
        <div className="flex-1 min-w-0">
          {selected.length === 0 ? (
            <span className="text-slate-400">
              {filterType === 'auto' ? 'Select auto campaigns...' : filterType === 'manual' ? 'Select a manual campaign...' : 'Select campaigns...'}
            </span>
          ) : (
            <div className="flex flex-wrap gap-1">
              {selected.slice(0, 3).map((c) => (
                <span key={c.amazon_campaign_id} className="inline-flex items-center gap-1 bg-brand-50 text-brand-700 text-xs font-medium px-2 py-0.5 rounded-md">
                  {c.campaign_name || c.amazon_campaign_id}
                  <button type="button" onClick={(e) => { e.stopPropagation(); toggle(c) }} className="hover:text-brand-900">
                    <X size={10} />
                  </button>
                </span>
              ))}
              {selected.length > 3 && <span className="text-xs text-slate-500 self-center">+{selected.length - 3} more</span>}
            </div>
          )}
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          {selected.length > 0 && (
            <span className="bg-brand-600 text-white text-[10px] font-bold w-5 h-5 rounded-full flex items-center justify-center">{selected.length}</span>
          )}
          <ChevronDown size={16} className={`text-slate-400 transition-transform ${open ? 'rotate-180' : ''}`} />
        </div>
      </button>

      {open && (
        <div className="absolute z-50 mt-1 w-full bg-white border border-slate-200 rounded-xl shadow-xl max-h-[340px] overflow-hidden flex flex-col">
          <div className="p-2 border-b border-slate-100 flex items-center gap-2">
            <Search size={14} className="text-slate-400 shrink-0" />
            <input type="text" placeholder="Search campaigns..." value={search} onChange={(e) => setSearch(e.target.value)} className="flex-1 text-sm border-0 outline-none bg-transparent placeholder:text-slate-300" autoFocus />
            <button type="button" onClick={onRefresh} className="p-1 hover:bg-slate-100 rounded transition-colors" title="Refresh campaigns">
              <RefreshCw size={12} className={`text-slate-400 ${loading ? 'animate-spin' : ''}`} />
            </button>
          </div>

          {!filterType && (
            <div className="px-3 py-1.5 border-b border-slate-100 flex items-center gap-2">
              <button type="button" onClick={selectAllAuto} className="text-[11px] font-medium text-brand-600 hover:text-brand-700 transition-colors">Select all auto</button>
              <span className="text-slate-300">|</span>
              <button type="button" onClick={() => onChange([])} className="text-[11px] font-medium text-slate-500 hover:text-slate-700 transition-colors">Clear all</button>
            </div>
          )}

          <div className="overflow-y-auto flex-1">
            {loading ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 size={18} className="animate-spin text-slate-400" />
                <span className="ml-2 text-sm text-slate-400">Loading campaigns...</span>
              </div>
            ) : filtered.length === 0 ? (
              <div className="py-8 text-center text-sm text-slate-400">
                {search ? 'No campaigns match your search' : 'No campaigns found. Click refresh to sync.'}
              </div>
            ) : (
              <>
                {[
                  { label: 'Auto Campaigns', items: autoCampaigns, color: 'text-purple-600 bg-purple-50/50' },
                  { label: 'Manual Campaigns', items: manualCampaigns, color: 'text-blue-600 bg-blue-50/50' },
                  { label: 'Other', items: otherCampaigns, color: 'text-slate-500 bg-slate-50/50' },
                ].filter(g => g.items.length > 0).map(group => (
                  <div key={group.label}>
                    <div className={`px-3 py-1.5 text-[10px] uppercase tracking-wider font-semibold ${group.color} sticky top-0`}>
                      {group.label} ({group.items.length})
                    </div>
                    {group.items.map((c) => (
                      <CampaignOption key={c.amazon_campaign_id} campaign={c} selected={isSelected(c)} onToggle={() => toggle(c)} />
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

// Single-select campaign dropdown
function CampaignSingleSelect({ campaigns, selected, onChange, loading, onRefresh, filterType }) {
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
    const matchesSearch = (c.campaign_name || '').toLowerCase().includes(q) || (c.amazon_campaign_id || '').toLowerCase().includes(q)
    if (filterType) return matchesSearch && c.targeting_type?.toLowerCase() === filterType.toLowerCase()
    return matchesSearch
  })

  function select(campaign) {
    onChange(campaign)
    setOpen(false)
  }

  return (
    <div className="relative" ref={ref}>
      <button type="button" onClick={() => setOpen(!open)} className="input flex items-center justify-between gap-2 text-left min-h-[42px] cursor-pointer">
        <div className="flex-1 min-w-0">
          {!selected ? (
            <span className="text-slate-400">Select target manual campaign...</span>
          ) : (
            <span className="text-sm font-medium text-slate-800">{selected.campaign_name || selected.amazon_campaign_id}</span>
          )}
        </div>
        <ChevronDown size={16} className={`text-slate-400 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <div className="absolute z-50 mt-1 w-full bg-white border border-slate-200 rounded-xl shadow-xl max-h-[280px] overflow-hidden flex flex-col">
          <div className="p-2 border-b border-slate-100 flex items-center gap-2">
            <Search size={14} className="text-slate-400 shrink-0" />
            <input type="text" placeholder="Search manual campaigns..." value={search} onChange={(e) => setSearch(e.target.value)} className="flex-1 text-sm border-0 outline-none bg-transparent placeholder:text-slate-300" autoFocus />
            <button type="button" onClick={onRefresh} className="p-1 hover:bg-slate-100 rounded transition-colors">
              <RefreshCw size={12} className={`text-slate-400 ${loading ? 'animate-spin' : ''}`} />
            </button>
          </div>
          <div className="overflow-y-auto flex-1">
            {filtered.length === 0 ? (
              <div className="py-6 text-center text-sm text-slate-400">No manual campaigns found</div>
            ) : (
              filtered.map((c) => (
                <CampaignOption key={c.amazon_campaign_id} campaign={c} selected={selected?.amazon_campaign_id === c.amazon_campaign_id} onToggle={() => select(c)} />
              ))
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function CampaignOption({ campaign, selected, onToggle }) {
  const stateColors = { enabled: 'bg-emerald-500', paused: 'bg-amber-500', archived: 'bg-slate-400' }
  return (
    <button type="button" onClick={onToggle} className={`w-full px-3 py-2.5 flex items-center gap-3 hover:bg-slate-50 transition-colors text-left ${selected ? 'bg-brand-50/50' : ''}`}>
      <div className={`w-4 h-4 rounded border-2 flex items-center justify-center shrink-0 transition-all ${selected ? 'bg-brand-600 border-brand-600' : 'border-slate-300'}`}>
        {selected && <Check size={10} className="text-white" />}
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <p className="text-sm font-medium text-slate-800 truncate">{campaign.campaign_name || 'Unnamed Campaign'}</p>
          <div className={`w-1.5 h-1.5 rounded-full shrink-0 ${stateColors[campaign.state] || 'bg-slate-300'}`} />
        </div>
        <div className="flex items-center gap-3 mt-0.5">
          <span className="text-[10px] font-mono text-slate-400 truncate">{campaign.amazon_campaign_id}</span>
          {campaign.daily_budget && <span className="text-[10px] text-slate-400">${campaign.daily_budget}/day</span>}
          <span className={`text-[10px] font-medium px-1.5 py-0 rounded ${campaign.targeting_type?.toLowerCase() === 'auto' ? 'text-purple-700 bg-purple-100' : 'text-blue-700 bg-blue-100'}`}>
            {campaign.targeting_type || 'unknown'}
          </span>
        </div>
      </div>
    </button>
  )
}

// ── Main Harvester Page ──────────────────────────────────────────────

export default function Harvester() {
  const { activeAccount, activeAccountId } = useAccount()
  const [configs, setConfigs] = useState([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [createStep, setCreateStep] = useState(1) // 1=campaigns, 2=settings, 3=review
  const [runningId, setRunningId] = useState(null)
  const [error, setError] = useState(null)
  const [successMsg, setSuccessMsg] = useState(null)
  const [expandedId, setExpandedId] = useState(null)
  const [runHistory, setRunHistory] = useState({})
  const [editingConfig, setEditingConfig] = useState(null)

  const [campaigns, setCampaigns] = useState([])
  const [campaignsLoading, setCampaignsLoading] = useState(false)

  const [form, setForm] = useState({
    name: '',
    source_campaigns: [],
    target_mode: 'new', // "new" or "existing"
    target_campaign: null, // selected existing manual campaign
    negate_in_source: true,
    sales_threshold: 1.0,
    acos_threshold: '',
    clicks_threshold: '',
    match_type: '',
    lookback_days: 30,
  })

  useEffect(() => { loadConfigs() }, [activeAccountId])

  async function loadConfigs() {
    setLoading(true)
    try { setConfigs(await harvest.configs(activeAccountId)) } catch {}
    finally { setLoading(false) }
  }

  async function loadCampaigns() {
    setCampaignsLoading(true)
    try { setCampaigns(await harvest.campaigns(activeAccountId)) }
    catch (err) { setError('Failed to load campaigns: ' + err.message) }
    finally { setCampaignsLoading(false) }
  }

  async function loadRunHistory(configId) {
    try { setRunHistory((prev) => ({ ...prev, [configId]: [] })); const runs = await harvest.runs(configId, activeAccountId); setRunHistory((prev) => ({ ...prev, [configId]: runs })) } catch {}
  }

  function openCreateModal() {
    setForm({ name: '', source_campaigns: [], target_mode: 'new', target_campaign: null, negate_in_source: true, sales_threshold: 1.0, acos_threshold: '', clicks_threshold: '', match_type: '', lookback_days: 30 })
    setCreateStep(1)
    setEditingConfig(null)
    setShowCreate(true)
    if (campaigns.length === 0) loadCampaigns()
  }

  function openEditModal(config) {
    setForm({
      name: config.name,
      source_campaigns: (config.source_campaigns || []).map(c => ({ amazon_campaign_id: c.amazon_campaign_id, campaign_name: c.campaign_name, targeting_type: c.targeting_type, state: c.state, daily_budget: c.daily_budget })),
      target_mode: config.target_mode || 'new',
      target_campaign: config.target_campaign_selection || null,
      negate_in_source: config.negate_in_source !== false,
      sales_threshold: config.sales_threshold || 1.0,
      acos_threshold: config.acos_threshold || '',
      clicks_threshold: config.clicks_threshold || '',
      match_type: config.match_type || '',
      lookback_days: config.lookback_days || 30,
    })
    setCreateStep(1)
    setEditingConfig(config)
    setShowCreate(true)
    if (campaigns.length === 0) loadCampaigns()
  }

  function nextStep() {
    if (createStep === 1) {
      if (form.source_campaigns.length === 0) { setError('Select at least one source auto campaign'); return }
      setCreateStep(2)
    } else if (createStep === 2) {
      if (form.target_mode === 'existing' && !form.target_campaign) { setError('Select a target manual campaign'); return }
      if (!form.name.trim()) {
        const names = form.source_campaigns.map(c => c.campaign_name || 'Campaign').slice(0, 2).join(', ')
        setForm(f => ({ ...f, name: `Harvest: ${names}${form.source_campaigns.length > 2 ? ` +${form.source_campaigns.length - 2}` : ''}` }))
      }
      setCreateStep(3)
    }
    setError(null)
  }

  async function submitConfig(e) {
    e.preventDefault()
    setError(null)
    try {
      const payload = {
        credential_id: activeAccountId,
        name: form.name,
        source_campaigns: form.source_campaigns.map(c => ({ amazon_campaign_id: c.amazon_campaign_id, campaign_name: c.campaign_name, targeting_type: c.targeting_type, state: c.state, daily_budget: c.daily_budget })),
        target_mode: form.target_mode,
        target_campaign_selection: form.target_mode === 'existing' && form.target_campaign ? { amazon_campaign_id: form.target_campaign.amazon_campaign_id, campaign_name: form.target_campaign.campaign_name } : null,
        negate_in_source: form.negate_in_source,
        sales_threshold: parseFloat(form.sales_threshold) || 1.0,
        acos_threshold: form.acos_threshold ? parseFloat(form.acos_threshold) : null,
        clicks_threshold: form.clicks_threshold ? parseInt(form.clicks_threshold) : null,
        match_type: form.match_type || null,
        lookback_days: parseInt(form.lookback_days) || 30,
      }
      if (editingConfig) {
        await harvest.update(editingConfig.id, payload)
        flash('Harvest rule updated successfully')
      } else {
        await harvest.create(payload)
        flash('Harvest rule created successfully')
      }
      setShowCreate(false)
      await loadConfigs()
    } catch (err) { setError(err.message) }
  }

  async function runHarvest(configId) {
    setRunningId(configId)
    setError(null)
    try {
      const result = await harvest.run(configId, activeAccountId, true)
      if (result.status === 'queued_for_approval') flash(result.message)
      else flash(`Harvest completed: ${result.keywords_harvested || 0} keywords harvested`)
      await loadConfigs()
    } catch (err) { setError(err.message) }
    finally { setRunningId(null) }
  }

  async function deleteConfig(configId) {
    if (!confirm('Delete this harvest configuration?')) return
    try { await harvest.delete(configId); flash('Harvest rule deleted'); await loadConfigs() }
    catch (err) { setError(err.message) }
  }

  function flash(msg) { setSuccessMsg(msg); setTimeout(() => setSuccessMsg(null), 5000) }

  function toggleExpand(configId) {
    if (expandedId === configId) { setExpandedId(null) }
    else { setExpandedId(configId); if (!runHistory[configId]) loadRunHistory(configId) }
  }

  const manualCampaigns = campaigns.filter(c => c.targeting_type?.toLowerCase() === 'manual')

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="flex items-center justify-center w-10 h-10 rounded-xl bg-gradient-to-br from-purple-500 to-brand-600 text-white">
            <Sparkles size={20} />
          </div>
          <div>
            <h1 className="text-xl font-bold text-slate-900 tracking-tight">Keyword Harvester</h1>
            <p className="text-xs text-slate-500">
              {activeAccount ? <>Harvesting for <span className="font-medium text-slate-700">{activeAccount.account_name || activeAccount.name}</span></> : 'Automatically harvest high-performing keywords from auto campaigns'}
            </p>
          </div>
        </div>
        <button onClick={openCreateModal} disabled={!activeAccount} className="btn-primary"><Plus size={16} /> New Harvest Rule</button>
      </div>

      {!activeAccount && (
        <div className="card bg-amber-50 border-amber-200 p-4 text-sm text-amber-800">Add and select an account in Settings before configuring harvest rules.</div>
      )}

      {/* How it works — clearer */}
      <div className="card bg-gradient-to-r from-purple-50 to-brand-50 border-purple-100 p-5">
        <div className="flex items-start gap-3">
          <Zap size={18} className="text-purple-600 mt-0.5 shrink-0" />
          <div>
            <h3 className="text-sm font-semibold text-purple-900">How Keyword Harvesting Works</h3>
            <div className="mt-2 space-y-2 text-sm text-purple-700">
              <div className="flex items-start gap-2">
                <span className="bg-purple-200 text-purple-800 text-[10px] font-bold w-5 h-5 rounded-full flex items-center justify-center shrink-0 mt-0.5">1</span>
                <span><strong>Select source auto campaigns</strong> — the campaigns you want to harvest keywords from.</span>
              </div>
              <div className="flex items-start gap-2">
                <span className="bg-purple-200 text-purple-800 text-[10px] font-bold w-5 h-5 rounded-full flex items-center justify-center shrink-0 mt-0.5">2</span>
                <span><strong>Choose a target</strong> — either let Amazon create a new manual campaign, or pick an existing one.</span>
              </div>
              <div className="flex items-start gap-2">
                <span className="bg-purple-200 text-purple-800 text-[10px] font-bold w-5 h-5 rounded-full flex items-center justify-center shrink-0 mt-0.5">3</span>
                <span><strong>Review & approve</strong> — changes go to the Approval Queue. Nothing touches Amazon Ads until you approve and push.</span>
              </div>
              <div className="flex items-start gap-2">
                <span className="bg-purple-200 text-purple-800 text-[10px] font-bold w-5 h-5 rounded-full flex items-center justify-center shrink-0 mt-0.5">4</span>
                <span><strong>Auto-negate</strong> — harvested keywords are negated in the source auto campaign to prevent cannibalization.</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Messages */}
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
          <p className="text-sm text-red-700">{error}</p>
          <button onClick={() => setError(null)} className="ml-auto"><X size={14} className="text-red-400" /></button>
        </div>
      )}

      {/* ═══ Create / Edit Modal ═══ */}
      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
          <div className="card w-full max-w-2xl p-0 overflow-hidden max-h-[90vh] flex flex-col">
            {/* Header */}
            <div className="flex items-center justify-between px-6 py-4 border-b border-slate-100">
              <div>
                <h2 className="text-lg font-semibold text-slate-900">{editingConfig ? 'Edit Harvest Rule' : 'New Harvest Rule'}</h2>
                {activeAccount && <p className="text-xs text-slate-400 mt-0.5">For {activeAccount.account_name || activeAccount.name}</p>}
              </div>
              <div className="flex items-center gap-3">
                <div className="flex items-center gap-1.5">
                  {[{ n: 1, l: 'Source' }, { n: 2, l: 'Target & Settings' }, { n: 3, l: 'Review' }].map((s, i) => (
                    <div key={s.n} className="flex items-center gap-1">
                      {i > 0 && <ChevronRight size={10} className="text-slate-300" />}
                      <div className={`flex items-center gap-1 text-[11px] font-medium ${createStep >= s.n ? 'text-brand-600' : 'text-slate-400'}`}>
                        <div className={`w-4.5 h-4.5 rounded-full flex items-center justify-center text-[9px] font-bold ${createStep >= s.n ? 'bg-brand-600 text-white' : 'bg-slate-200 text-slate-500'}`}>{s.n}</div>
                        <span className="hidden sm:inline">{s.l}</span>
                      </div>
                    </div>
                  ))}
                </div>
                <button onClick={() => setShowCreate(false)} className="p-1.5 hover:bg-slate-100 rounded-lg transition-colors"><X size={18} className="text-slate-400" /></button>
              </div>
            </div>

            {/* Body */}
            <div className="flex-1 overflow-y-auto px-6 py-5">
              {/* Step 1: Source campaigns */}
              {createStep === 1 && (
                <div className="space-y-5">
                  <div>
                    <label className="label flex items-center gap-2"><Target size={14} className="text-purple-500" /> Source Auto Campaigns</label>
                    <CampaignSelector campaigns={campaigns} selected={form.source_campaigns} onChange={(s) => setForm({ ...form, source_campaigns: s })} loading={campaignsLoading} onRefresh={loadCampaigns} filterType="auto" />
                    <p className="text-xs text-slate-400 mt-1.5">Select the auto campaigns to harvest high-performing keywords from. You can select multiple.</p>
                  </div>
                  <div>
                    <label className="label">Rule Name</label>
                    <input type="text" className="input" placeholder="Auto-generated if left empty" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
                  </div>
                </div>
              )}

              {/* Step 2: Target + Settings */}
              {createStep === 2 && (
                <div className="space-y-5">
                  {/* Target campaign selection */}
                  <div>
                    <label className="label flex items-center gap-2"><GitBranch size={14} className="text-blue-500" /> Where should harvested keywords go?</label>
                    <div className="grid grid-cols-2 gap-3 mt-1">
                      <button
                        type="button"
                        onClick={() => setForm({ ...form, target_mode: 'new', target_campaign: null })}
                        className={`p-4 rounded-xl border-2 text-left transition-all ${form.target_mode === 'new' ? 'border-brand-500 bg-brand-50/50 ring-1 ring-brand-500/20' : 'border-slate-200 hover:border-slate-300'}`}
                      >
                        <div className="flex items-center gap-2 mb-1.5">
                          <Plus size={14} className={form.target_mode === 'new' ? 'text-brand-600' : 'text-slate-400'} />
                          <span className={`text-sm font-semibold ${form.target_mode === 'new' ? 'text-brand-900' : 'text-slate-700'}`}>Create New Campaign</span>
                        </div>
                        <p className="text-xs text-slate-500">Amazon automatically creates a new manual campaign and sets up continuous keyword monitoring.</p>
                      </button>
                      <button
                        type="button"
                        onClick={() => setForm({ ...form, target_mode: 'existing' })}
                        className={`p-4 rounded-xl border-2 text-left transition-all ${form.target_mode === 'existing' ? 'border-brand-500 bg-brand-50/50 ring-1 ring-brand-500/20' : 'border-slate-200 hover:border-slate-300'}`}
                      >
                        <div className="flex items-center gap-2 mb-1.5">
                          <ArrowRight size={14} className={form.target_mode === 'existing' ? 'text-brand-600' : 'text-slate-400'} />
                          <span className={`text-sm font-semibold ${form.target_mode === 'existing' ? 'text-brand-900' : 'text-slate-700'}`}>Use Existing Campaign</span>
                        </div>
                        <p className="text-xs text-slate-500">Add harvested keywords as targets to a manual campaign you already have running.</p>
                      </button>
                    </div>

                    {form.target_mode === 'existing' && (
                      <div className="mt-3">
                        <label className="text-xs text-slate-500 font-medium mb-1 block">Select Target Manual Campaign</label>
                        <CampaignSingleSelect campaigns={campaigns} selected={form.target_campaign} onChange={(c) => setForm({ ...form, target_campaign: c })} loading={campaignsLoading} onRefresh={loadCampaigns} filterType="manual" />
                      </div>
                    )}

                    {form.target_mode === 'new' && (
                      <div className="mt-3 bg-blue-50 border border-blue-200 rounded-lg p-3 flex items-start gap-2">
                        <Info size={14} className="text-blue-600 mt-0.5 shrink-0" />
                        <p className="text-xs text-blue-700">Amazon will create a new Sponsored Products manual campaign with its own ad group. The campaign ID will be shown here after execution.</p>
                      </div>
                    )}
                  </div>

                  {/* Negation toggle */}
                  <div className="bg-slate-50 rounded-xl p-4">
                    <label className="flex items-center gap-3 cursor-pointer">
                      <button type="button" onClick={() => setForm({ ...form, negate_in_source: !form.negate_in_source })}
                        className={`relative w-10 h-5 rounded-full transition-colors ${form.negate_in_source ? 'bg-brand-600' : 'bg-slate-300'}`}>
                        <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${form.negate_in_source ? 'left-5' : 'left-0.5'}`} />
                      </button>
                      <div>
                        <span className="text-sm font-medium text-slate-800 flex items-center gap-1.5">
                          <Ban size={14} className="text-red-500" /> Negate keywords in source auto campaign
                        </span>
                        <p className="text-xs text-slate-500 mt-0.5">
                          {form.negate_in_source
                            ? 'Harvested keywords will be added as negative exact matches in the source auto campaign to prevent cannibalization.'
                            : 'Source auto campaign will continue bidding on harvested keywords (not recommended — causes self-competition).'}
                        </p>
                      </div>
                    </label>
                  </div>

                  {/* Thresholds */}
                  <div>
                    <label className="label flex items-center gap-2"><Filter size={14} className="text-amber-500" /> Harvest Thresholds</label>
                    <div className="grid grid-cols-3 gap-3">
                      <div>
                        <label className="text-xs text-slate-500 font-medium">Min Sales</label>
                        <input type="number" step="0.1" min="0" className="input mt-1" value={form.sales_threshold} onChange={(e) => setForm({ ...form, sales_threshold: e.target.value })} />
                      </div>
                      <div>
                        <label className="text-xs text-slate-500 font-medium">Max ACOS (%)</label>
                        <input type="number" step="0.1" min="0" className="input mt-1" placeholder="Optional" value={form.acos_threshold} onChange={(e) => setForm({ ...form, acos_threshold: e.target.value })} />
                      </div>
                      <div>
                        <label className="text-xs text-slate-500 font-medium">Min Clicks</label>
                        <input type="number" step="1" min="0" className="input mt-1" placeholder="Optional" value={form.clicks_threshold} onChange={(e) => setForm({ ...form, clicks_threshold: e.target.value })} />
                      </div>
                    </div>
                  </div>

                  {/* Advanced */}
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="label flex items-center gap-2"><Hash size={14} className="text-blue-500" /> Match Type</label>
                      <select className="input" value={form.match_type} onChange={(e) => setForm({ ...form, match_type: e.target.value })}>
                        <option value="">All match types</option>
                        <option value="exact">Exact</option>
                        <option value="phrase">Phrase</option>
                        <option value="broad">Broad</option>
                      </select>
                    </div>
                    <div>
                      <label className="label flex items-center gap-2"><Calendar size={14} className="text-emerald-500" /> Lookback Period</label>
                      <select className="input" value={form.lookback_days} onChange={(e) => setForm({ ...form, lookback_days: parseInt(e.target.value) })}>
                        <option value={7}>Last 7 days</option>
                        <option value={14}>Last 14 days</option>
                        <option value={30}>Last 30 days</option>
                        <option value={60}>Last 60 days</option>
                        <option value={90}>Last 90 days</option>
                      </select>
                    </div>
                  </div>
                </div>
              )}

              {/* Step 3: Review */}
              {createStep === 3 && (
                <div className="space-y-5">
                  {/* Flow diagram */}
                  <div className="bg-slate-50 rounded-xl p-5">
                    <p className="text-xs text-slate-400 font-semibold uppercase tracking-wider mb-3">Harvest Flow</p>
                    <div className="flex items-center gap-3 flex-wrap">
                      {/* Source */}
                      <div className="flex-1 min-w-[140px]">
                        <div className="bg-purple-100 border border-purple-200 rounded-lg p-3 text-center">
                          <p className="text-[10px] text-purple-500 uppercase font-semibold">Source (Auto)</p>
                          <p className="text-sm font-semibold text-purple-800 mt-1">
                            {form.source_campaigns.length} campaign{form.source_campaigns.length !== 1 ? 's' : ''}
                          </p>
                        </div>
                      </div>
                      {/* Arrow */}
                      <div className="flex flex-col items-center gap-0.5 shrink-0">
                        <ArrowRight size={20} className="text-brand-500" />
                        <span className="text-[9px] text-slate-400 font-medium">keywords</span>
                      </div>
                      {/* Target */}
                      <div className="flex-1 min-w-[140px]">
                        <div className="bg-blue-100 border border-blue-200 rounded-lg p-3 text-center">
                          <p className="text-[10px] text-blue-500 uppercase font-semibold">Target (Manual)</p>
                          <p className="text-sm font-semibold text-blue-800 mt-1">
                            {form.target_mode === 'new' ? 'New campaign' : (form.target_campaign?.campaign_name || 'Selected campaign')}
                          </p>
                        </div>
                      </div>
                    </div>
                    {form.negate_in_source && (
                      <div className="mt-3 flex items-center justify-center gap-2 text-xs text-red-600 font-medium bg-red-50 rounded-lg p-2">
                        <Ban size={12} />
                        Harvested keywords will also be negated in source auto campaign{form.source_campaigns.length !== 1 ? 's' : ''}
                      </div>
                    )}
                  </div>

                  {/* Settings summary */}
                  <div className="bg-white border border-slate-200 rounded-xl p-4">
                    <p className="text-xs text-slate-400 font-semibold uppercase tracking-wider mb-3">Configuration</p>
                    <div className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
                      <div>
                        <p className="text-xs text-slate-400">Rule Name</p>
                        <p className="font-medium text-slate-800">{form.name || 'Auto-generated'}</p>
                      </div>
                      <div>
                        <p className="text-xs text-slate-400">Target Mode</p>
                        <p className="font-medium text-slate-800">{form.target_mode === 'new' ? 'Amazon creates new manual campaign' : 'Add to existing manual campaign'}</p>
                      </div>
                      {form.target_mode === 'existing' && form.target_campaign && (
                        <div className="col-span-2">
                          <p className="text-xs text-slate-400">Target Campaign</p>
                          <p className="font-medium text-slate-800">{form.target_campaign.campaign_name} <span className="font-mono text-xs text-slate-400">({form.target_campaign.amazon_campaign_id})</span></p>
                        </div>
                      )}
                      <div>
                        <p className="text-xs text-slate-400">Sales Threshold</p>
                        <p className="font-medium text-slate-800">&ge; {form.sales_threshold}</p>
                      </div>
                      <div>
                        <p className="text-xs text-slate-400">ACOS Threshold</p>
                        <p className="font-medium text-slate-800">{form.acos_threshold ? `≤ ${form.acos_threshold}%` : 'No limit'}</p>
                      </div>
                      <div>
                        <p className="text-xs text-slate-400">Clicks Threshold</p>
                        <p className="font-medium text-slate-800">{form.clicks_threshold ? `≥ ${form.clicks_threshold}` : 'No minimum'}</p>
                      </div>
                      <div>
                        <p className="text-xs text-slate-400">Match Type</p>
                        <p className="font-medium text-slate-800 capitalize">{form.match_type || 'All types'}</p>
                      </div>
                      <div>
                        <p className="text-xs text-slate-400">Lookback</p>
                        <p className="font-medium text-slate-800">{form.lookback_days} days</p>
                      </div>
                      <div>
                        <p className="text-xs text-slate-400">Negate in Source</p>
                        <p className={`font-medium ${form.negate_in_source ? 'text-emerald-700' : 'text-amber-700'}`}>{form.negate_in_source ? 'Yes — prevents cannibalization' : 'No — source keeps bidding'}</p>
                      </div>
                    </div>
                  </div>

                  {/* Source campaigns list */}
                  <div>
                    <p className="text-xs text-slate-400 mb-2 font-medium uppercase tracking-wider">Source Campaigns ({form.source_campaigns.length})</p>
                    <div className="space-y-1.5 max-h-[180px] overflow-y-auto">
                      {form.source_campaigns.map((c) => (
                        <div key={c.amazon_campaign_id} className="flex items-center gap-3 bg-white border border-slate-200 rounded-lg p-2.5">
                          <div className={`w-2 h-2 rounded-full shrink-0 ${c.state === 'enabled' ? 'bg-emerald-500' : c.state === 'paused' ? 'bg-amber-500' : 'bg-slate-400'}`} />
                          <div className="flex-1 min-w-0">
                            <p className="text-sm font-medium text-slate-800 truncate">{c.campaign_name}</p>
                            <p className="text-[10px] font-mono text-slate-400">{c.amazon_campaign_id}</p>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* Approval notice */}
                  <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 flex items-start gap-3">
                    <Shield size={16} className="text-blue-600 mt-0.5 shrink-0" />
                    <div>
                      <p className="text-sm font-medium text-blue-900">Safe Execution</p>
                      <p className="text-xs text-blue-700 mt-0.5">When you run this harvest, changes will be sent to the <strong>Approval Queue</strong> for your review. Nothing will be pushed to Amazon Ads until you explicitly approve and apply.</p>
                    </div>
                  </div>
                </div>
              )}
            </div>

            {/* Footer */}
            <div className="px-6 py-4 border-t border-slate-100 bg-slate-50/50 flex items-center justify-between">
              <div>
                {createStep > 1 && (
                  <button type="button" onClick={() => { setCreateStep(createStep - 1); setError(null) }} className="btn-ghost text-sm">
                    <ChevronDown size={14} className="rotate-90" /> Back
                  </button>
                )}
              </div>
              <div className="flex items-center gap-3">
                <button type="button" onClick={() => setShowCreate(false)} className="btn-secondary">Cancel</button>
                {createStep < 3 ? (
                  <button type="button" onClick={nextStep} disabled={createStep === 1 && form.source_campaigns.length === 0} className="btn-primary">
                    Next <ArrowRight size={14} />
                  </button>
                ) : (
                  <button onClick={submitConfig} className="btn-primary">
                    <CheckCircle size={14} /> {editingConfig ? 'Update Rule' : 'Create Rule'}
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ═══ Config Cards ═══ */}
      {loading ? (
        <div className="flex items-center justify-center py-12"><Loader2 className="animate-spin text-slate-400" size={24} /></div>
      ) : configs.length === 0 ? (
        <EmptyState icon={Sparkles} title="No harvest rules configured"
          description={activeAccount ? `Create a harvest rule for ${activeAccount.account_name || activeAccount.name} to start migrating high-performing keywords.` : 'Select an account, then create a harvest rule.'}
          action={activeAccount && (<button onClick={openCreateModal} className="btn-primary"><Plus size={16} /> Create First Rule</button>)}
        />
      ) : (
        <div className="space-y-3">
          {configs.map((config) => {
            const isExpanded = expandedId === config.id
            const sourceCampaigns = config.source_campaigns || []
            const runs = runHistory[config.id] || []
            const targetMode = config.target_mode || 'new'
            const targetSel = config.target_campaign_selection

            return (
              <div key={config.id} className="card overflow-hidden">
                <div className="p-5">
                  <div className="flex items-start justify-between">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-3 flex-wrap">
                        <h3 className="text-sm font-semibold text-slate-900">{config.name}</h3>
                        <StatusBadge status={config.status} />
                        {config.status === 'pending_approval' && (
                          <span className="flex items-center gap-1 text-[10px] font-medium text-blue-600 bg-blue-50 px-2 py-0.5 rounded-full"><Shield size={10} /> In Approval Queue</span>
                        )}
                      </div>

                      {/* Flow: Source → Target */}
                      <div className="mt-2.5 flex items-center gap-2 flex-wrap">
                        <div className="flex items-center gap-1.5">
                          {sourceCampaigns.slice(0, 3).map((c) => (
                            <span key={c.amazon_campaign_id} className="inline-flex items-center gap-1 text-[11px] font-medium bg-purple-50 text-purple-700 px-2 py-0.5 rounded-md">
                              <div className={`w-1.5 h-1.5 rounded-full ${c.state === 'enabled' ? 'bg-emerald-500' : c.state === 'paused' ? 'bg-amber-500' : 'bg-slate-400'}`} />
                              {c.campaign_name || c.amazon_campaign_id}
                            </span>
                          ))}
                          {sourceCampaigns.length > 3 && <span className="text-[11px] text-slate-400">+{sourceCampaigns.length - 3}</span>}
                          {sourceCampaigns.length === 0 && (
                            <span className="inline-flex items-center gap-1 text-[11px] font-medium bg-purple-50 text-purple-700 px-2 py-0.5 rounded-md">
                              {config.source_campaign_name || config.source_campaign_id}
                            </span>
                          )}
                        </div>
                        <ArrowRight size={12} className="text-slate-400 shrink-0" />
                        <span className={`inline-flex items-center gap-1 text-[11px] font-medium px-2 py-0.5 rounded-md ${
                          targetMode === 'existing'
                            ? 'bg-blue-50 text-blue-700'
                            : config.target_campaign_id
                              ? 'bg-emerald-50 text-emerald-700'
                              : 'bg-slate-100 text-slate-600'
                        }`}>
                          {targetMode === 'existing' && targetSel
                            ? (targetSel.campaign_name || targetSel.amazon_campaign_id)
                            : config.target_campaign_id
                              ? `Manual: ${config.target_campaign_name || config.target_campaign_id}`
                              : 'New campaign (auto-created)'}
                        </span>
                        {(config.negate_in_source !== false) && (
                          <span className="inline-flex items-center gap-1 text-[10px] font-medium text-red-600 bg-red-50 px-1.5 py-0.5 rounded">
                            <Ban size={9} /> negation
                          </span>
                        )}
                      </div>

                      {/* Stats */}
                      <div className="mt-3 grid grid-cols-2 sm:grid-cols-5 gap-4 text-sm">
                        <div>
                          <p className="text-[10px] text-slate-400 uppercase tracking-wider">Campaigns</p>
                          <p className="font-semibold text-slate-700">{sourceCampaigns.length || 1}</p>
                        </div>
                        <div>
                          <p className="text-[10px] text-slate-400 uppercase tracking-wider">Sales Threshold</p>
                          <p className="font-semibold text-slate-700">&ge; {config.sales_threshold}</p>
                        </div>
                        <div>
                          <p className="text-[10px] text-slate-400 uppercase tracking-wider">ACOS Limit</p>
                          <p className="font-semibold text-slate-700">{config.acos_threshold ? `≤ ${config.acos_threshold}%` : '—'}</p>
                        </div>
                        <div>
                          <p className="text-[10px] text-slate-400 uppercase tracking-wider">Keywords Harvested</p>
                          <p className="font-semibold text-slate-700">{config.total_keywords_harvested || 0}</p>
                        </div>
                        <div>
                          <p className="text-[10px] text-slate-400 uppercase tracking-wider">Last Run</p>
                          <p className="font-semibold text-slate-700">{config.last_harvested_at ? new Date(config.last_harvested_at).toLocaleDateString() : 'Never'}</p>
                        </div>
                      </div>
                    </div>

                    {/* Actions */}
                    <div className="flex items-center gap-1.5 ml-4 shrink-0">
                      <button onClick={() => runHarvest(config.id)} disabled={runningId === config.id} className="btn-primary text-xs" title="Run harvest (sends to approval queue)">
                        {runningId === config.id ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />} Run & Review
                      </button>
                      <button onClick={() => openEditModal(config)} className="p-2 rounded-lg hover:bg-slate-100 text-slate-400 transition-colors" title="Edit"><Edit3 size={14} /></button>
                      <button onClick={() => toggleExpand(config.id)} className="p-2 rounded-lg hover:bg-slate-100 text-slate-400 transition-colors" title="Details">
                        {isExpanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                      </button>
                      <button onClick={() => deleteConfig(config.id)} className="p-2 rounded-lg hover:bg-red-50 text-red-400 transition-colors" title="Delete"><Trash2 size={14} /></button>
                    </div>
                  </div>
                </div>

                {/* Expanded */}
                {isExpanded && (
                  <div className="border-t border-slate-100 bg-slate-50/50 px-5 py-4 space-y-4">
                    {/* Target campaign info */}
                    <div>
                      <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2 flex items-center gap-2"><GitBranch size={12} /> Target Campaign</h4>
                      <div className="bg-white rounded-lg border border-slate-200 p-3">
                        {targetMode === 'existing' && targetSel ? (
                          <div>
                            <p className="text-sm font-medium text-slate-800">{targetSel.campaign_name || 'Unnamed'}</p>
                            <p className="text-[10px] font-mono text-slate-400 mt-0.5">{targetSel.amazon_campaign_id}</p>
                            <p className="text-xs text-blue-600 mt-1">Keywords will be added as targets to this existing manual campaign</p>
                          </div>
                        ) : config.target_campaign_id ? (
                          <div>
                            <p className="text-sm font-medium text-emerald-800">Campaign Created by Amazon</p>
                            <p className="text-[10px] font-mono text-slate-400 mt-0.5">ID: {config.target_campaign_id}</p>
                            {config.target_campaign_name && <p className="text-xs text-slate-600 mt-0.5">{config.target_campaign_name}</p>}
                            <p className="text-xs text-emerald-600 mt-1">Amazon created this manual campaign with continuous monitoring enabled</p>
                          </div>
                        ) : (
                          <div>
                            <p className="text-sm font-medium text-slate-600">New campaign (will be created on execution)</p>
                            <p className="text-xs text-slate-400 mt-0.5">Amazon will auto-create a Sponsored Products manual campaign when this harvest is approved and applied.</p>
                          </div>
                        )}
                      </div>
                    </div>

                    {/* Run history */}
                    <div>
                      <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2 flex items-center gap-2"><Clock size={12} /> Run History</h4>
                      {runs.length === 0 ? (
                        <p className="text-sm text-slate-400 py-2">No runs yet. Click "Run & Review" to start.</p>
                      ) : (
                        <div className="space-y-1.5">
                          {runs.slice(0, 5).map((run) => (
                            <div key={run.id} className="flex items-center gap-3 bg-white rounded-lg p-3 border border-slate-200">
                              <div className={`w-2 h-2 rounded-full shrink-0 ${run.status === 'completed' ? 'bg-emerald-500' : run.status === 'running' ? 'bg-blue-500 animate-pulse' : run.status === 'failed' ? 'bg-red-500' : 'bg-slate-400'}`} />
                              <div className="flex-1 min-w-0">
                                <div className="flex items-center gap-2">
                                  <span className="text-xs font-medium text-slate-700 capitalize">{run.status}</span>
                                  <span className="text-[10px] text-slate-400">{new Date(run.started_at).toLocaleString()}</span>
                                </div>
                                {run.error_message && <p className="text-[10px] text-red-500 mt-0.5 truncate">{run.error_message}</p>}
                              </div>
                              <div className="text-right shrink-0">
                                <p className="text-sm font-semibold text-slate-700">{run.keywords_harvested || 0}</p>
                                <p className="text-[10px] text-slate-400">keywords</p>
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>

                    {/* Config details */}
                    <div>
                      <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2 flex items-center gap-2"><Info size={12} /> Configuration</h4>
                      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
                        <div><p className="text-slate-400">Match Type</p><p className="font-medium text-slate-600 capitalize">{config.match_type || 'All types'}</p></div>
                        <div><p className="text-slate-400">Lookback</p><p className="font-medium text-slate-600">{config.lookback_days || 30} days</p></div>
                        <div><p className="text-slate-400">Clicks Threshold</p><p className="font-medium text-slate-600">{config.clicks_threshold || 'None'}</p></div>
                        <div><p className="text-slate-400">Negate in Source</p><p className={`font-medium ${config.negate_in_source !== false ? 'text-emerald-600' : 'text-amber-600'}`}>{config.negate_in_source !== false ? 'Yes' : 'No'}</p></div>
                      </div>
                    </div>
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
