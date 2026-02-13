import { useState, useEffect, useCallback, useRef } from 'react'
import {
  Megaphone,
  Layers,
  Target,
  FileImage,
  ChevronRight,
  ChevronDown,
  ChevronUp,
  RefreshCw,
  Loader2,
  Plus,
  Pencil,
  Trash2,
  Pause,
  Play,
  Archive,
  DollarSign,
  Search,
  Filter,
  ArrowLeft,
  AlertTriangle,
  Check,
  X,
  Eye,
  Hash,
  Tag,
  Box,
  Calendar,
  Columns3,
  Settings,
  Globe,
  Rocket,
} from 'lucide-react'
import { Link } from 'react-router-dom'
import { useAccount } from '../lib/AccountContext'
import { useSync } from '../lib/SyncContext'
import { campaignManager, accounts } from '../lib/api'
import StatusBadge from '../components/StatusBadge'
import EmptyState from '../components/EmptyState'
import DateRangePicker, { getPresetRange } from '../components/DateRangePicker'

// ── Sortable header ──────────────────────────────────────────────────
function SortableHeader({ label, colKey, sortBy, sortDir, onSort, colSpan = 1, align = 'left' }) {
  const isActive = sortBy === colKey
  return (
    <button
      type="button"
      onClick={() => onSort(colKey)}
      className={`inline-flex items-center gap-1 hover:text-slate-700 transition-colors ${align === 'right' ? 'justify-end text-right' : ''}`}
      style={{ gridColumn: `span ${colSpan}` }}
    >
      {label}
      {isActive ? (
        sortDir === 'asc' ? <ChevronUp size={12} className="text-slate-600" /> : <ChevronDown size={12} className="text-slate-600" />
      ) : (
        <ChevronDown size={12} className="text-slate-300 opacity-50" />
      )}
    </button>
  )
}

// ── State Badge ──────────────────────────────────────────────────────
function StateBadge({ state }) {
  const s = (state || '').toLowerCase()
  const styles = {
    enabled: 'bg-emerald-50 text-emerald-700 border-emerald-200',
    paused: 'bg-amber-50 text-amber-700 border-amber-200',
    archived: 'bg-slate-100 text-slate-500 border-slate-200',
  }
  return (
    <span className={`inline-flex items-center px-2 py-0.5 text-xs font-medium rounded-full border ${styles[s] || 'bg-slate-50 text-slate-500 border-slate-200'}`}>
      {s === 'enabled' && <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 mr-1.5" />}
      {s === 'paused' && <span className="w-1.5 h-1.5 rounded-full bg-amber-500 mr-1.5" />}
      {s === 'archived' && <span className="w-1.5 h-1.5 rounded-full bg-slate-400 mr-1.5" />}
      {state || 'Unknown'}
    </span>
  )
}

// ── Quick Edit Modal ─────────────────────────────────────────────────
function QuickEditModal({ title, fields, onSave, onClose, saving, skipApprovalOption = false }) {
  const [values, setValues] = useState(() => {
    const init = {}
    fields.forEach(f => { init[f.key] = f.value ?? '' })
    return init
  })
  const [skipApproval, setSkipApproval] = useState(true)

  const handleSave = () => {
    if (skipApprovalOption) {
      onSave(values, skipApproval)
    } else {
      onSave(values)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm">
      <div className="bg-white rounded-xl shadow-2xl border border-slate-200 w-full max-w-md mx-4">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-100">
          <h3 className="text-base font-semibold text-slate-900">{title}</h3>
          <button onClick={onClose} className="p-1 rounded-lg hover:bg-slate-100 text-slate-400">
            <X size={18} />
          </button>
        </div>
        <div className="px-5 py-4 space-y-4">
          {fields.map(f => (
            <div key={f.key}>
              <label className="block text-xs font-semibold text-slate-700 uppercase tracking-wide mb-1.5">
                {f.label}
              </label>
              {f.type === 'select' ? (
                <select
                  value={values[f.key]}
                  onChange={e => setValues(v => ({ ...v, [f.key]: e.target.value }))}
                  className="input"
                >
                  {f.options.map(o => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </select>
              ) : (
                <input
                  type={f.type || 'text'}
                  value={values[f.key]}
                  onChange={e => setValues(v => ({ ...v, [f.key]: e.target.value }))}
                  placeholder={f.placeholder}
                  className="input"
                  step={f.type === 'number' ? '0.01' : undefined}
                />
              )}
              {f.hint && <p className="text-xs text-slate-400 mt-1">{f.hint}</p>}
            </div>
          ))}
          {skipApprovalOption && (
            <label className="flex items-center gap-2 cursor-pointer pt-2 border-t border-slate-100">
              <input
                type="checkbox"
                checked={skipApproval}
                onChange={e => setSkipApproval(e.target.checked)}
                className="rounded border-slate-300 text-brand-600 focus:ring-brand-500"
              />
              <span className="text-sm text-slate-600">Apply directly to Amazon (skip approval queue)</span>
            </label>
          )}
        </div>
        <div className="flex items-center justify-end gap-2 px-5 py-3 border-t border-slate-100 bg-slate-50 rounded-b-xl">
          <button onClick={onClose} className="btn-ghost text-sm">Cancel</button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="btn-primary text-sm"
          >
            {saving ? <><Loader2 size={14} className="animate-spin" /> Saving...</> : <><Check size={14} /> Save Changes</>}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Confirm Delete Modal ─────────────────────────────────────────────
function ConfirmModal({ title, message, onConfirm, onClose, confirming, skipApprovalOption = false, confirmLabel = 'Delete', confirmingLabel = 'Deleting...' }) {
  const [skipApproval, setSkipApproval] = useState(true)

  const handleConfirm = () => {
    if (skipApprovalOption) {
      onConfirm(skipApproval)
    } else {
      onConfirm()
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm">
      <div className="bg-white rounded-xl shadow-2xl border border-slate-200 w-full max-w-sm mx-4">
        <div className="px-5 py-5 text-center">
          <div className="flex items-center justify-center w-12 h-12 rounded-full bg-red-50 text-red-500 mx-auto mb-3">
            <AlertTriangle size={22} />
          </div>
          <h3 className="text-base font-semibold text-slate-900">{title}</h3>
          <p className="mt-2 text-sm text-slate-500">{message}</p>
          {skipApprovalOption && (
            <label className="flex items-center justify-center gap-2 cursor-pointer mt-4">
              <input
                type="checkbox"
                checked={skipApproval}
                onChange={e => setSkipApproval(e.target.checked)}
                className="rounded border-slate-300 text-brand-600 focus:ring-brand-500"
              />
              <span className="text-sm text-slate-600">Apply directly to Amazon (skip approval queue)</span>
            </label>
          )}
        </div>
        <div className="flex items-center justify-center gap-2 px-5 py-3 border-t border-slate-100 bg-slate-50 rounded-b-xl">
          <button onClick={onClose} className="btn-ghost text-sm">Cancel</button>
          <button
            onClick={handleConfirm}
            disabled={confirming}
            className="inline-flex items-center gap-1.5 px-4 py-2 text-sm font-medium text-white bg-red-600 rounded-lg hover:bg-red-700 transition-colors disabled:opacity-50"
          >
            {confirming ? <><Loader2 size={14} className="animate-spin" /> {confirmingLabel}</> : <><Trash2 size={14} /> {confirmLabel}</>}
          </button>
        </div>
      </div>
    </div>
  )
}


// ── Add Country Modal ───────────────────────────────────────────────
const COUNTRY_OPTIONS = [
  { code: 'US', label: 'United States' }, { code: 'CA', label: 'Canada' }, { code: 'MX', label: 'Mexico' }, { code: 'BR', label: 'Brazil' },
  { code: 'GB', label: 'United Kingdom' }, { code: 'DE', label: 'Germany' }, { code: 'FR', label: 'France' }, { code: 'IT', label: 'Italy' },
  { code: 'ES', label: 'Spain' }, { code: 'NL', label: 'Netherlands' }, { code: 'SE', label: 'Sweden' }, { code: 'PL', label: 'Poland' },
  { code: 'JP', label: 'Japan' }, { code: 'AU', label: 'Australia' }, { code: 'IN', label: 'India' }, { code: 'AE', label: 'UAE' },
]
function AddCountryModal({ campaign, onSave, onClose, saving }) {
  const [countries, setCountries] = useState([{ countryCode: 'GB', dailyBudget: 10 }])
  const [skipApproval, setSkipApproval] = useState(true)
  const addRow = () => setCountries(c => [...c, { countryCode: 'GB', dailyBudget: 10 }])
  const removeRow = (i) => setCountries(c => c.filter((_, idx) => idx !== i))
  const updateRow = (i, key, val) => setCountries(c => {
    const next = [...c]
    next[i] = { ...next[i], [key]: key === 'dailyBudget' ? parseFloat(val) || 0 : val }
    return next
  })
  const handleSubmit = () => {
    const payload = countries.map(({ countryCode, dailyBudget }) => ({ countryCode, dailyBudget }))
    onSave(payload, skipApproval)
  }
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm">
      <div className="bg-white rounded-xl shadow-2xl border border-slate-200 w-full max-w-lg mx-4">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-100">
          <h3 className="text-base font-semibold text-slate-900">Add countries to campaign</h3>
          <button onClick={onClose} className="p-1 rounded-lg hover:bg-slate-100 text-slate-400"><X size={18} /></button>
        </div>
        <div className="px-5 py-4 space-y-4">
          <p className="text-sm text-slate-600">{campaign?.campaign_name} — SP Manual only</p>
          {countries.map((row, i) => (
            <div key={i} className="flex gap-2 items-center">
              <select
                value={row.countryCode}
                onChange={e => updateRow(i, 'countryCode', e.target.value)}
                className="input flex-1"
              >
                {COUNTRY_OPTIONS.map(o => <option key={o.code} value={o.code}>{o.label}</option>)}
              </select>
              <input
                type="number"
                min="0"
                step="0.01"
                value={row.dailyBudget}
                onChange={e => updateRow(i, 'dailyBudget', e.target.value)}
                placeholder="Daily budget"
                className="input w-28"
              />
              <button onClick={() => removeRow(i)} className="p-1.5 text-red-500 hover:bg-red-50 rounded" disabled={countries.length <= 1}><Trash2 size={14} /></button>
            </div>
          ))}
          <button onClick={addRow} className="text-sm text-brand-600 hover:underline flex items-center gap-1"><Plus size={14} /> Add another country</button>
          <label className="flex items-center gap-2 cursor-pointer pt-2 border-t border-slate-100">
            <input type="checkbox" checked={skipApproval} onChange={e => setSkipApproval(e.target.checked)} className="rounded border-slate-300 text-brand-600" />
            <span className="text-sm text-slate-600">Apply directly to Amazon (skip approval queue)</span>
          </label>
        </div>
        <div className="flex justify-end gap-2 px-5 py-3 border-t border-slate-100 bg-slate-50 rounded-b-xl">
          <button onClick={onClose} className="btn-ghost text-sm">Cancel</button>
          <button onClick={handleSubmit} disabled={saving} className="btn-primary text-sm">
            {saving ? <><Loader2 size={14} className="animate-spin" /> Adding...</> : <><Globe size={14} /> Add countries</>}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Singleshot Modal ─────────────────────────────────────────────────
function SingleshotModal({ products = [], onSave, onClose, saving }) {
  const [name, setName] = useState('')
  const [countryBudgets, setCountryBudgets] = useState([{ countryCode: 'US', dailyBudget: 25 }])
  const [asinsByCountry, setAsinsByCountry] = useState({ US: [] })
  const [skipApproval, setSkipApproval] = useState(true)
  const addCountry = () => setCountryBudgets(c => [...c, { countryCode: 'GB', dailyBudget: 25 }])
  const removeCountry = (i) => setCountryBudgets(c => c.filter((_, idx) => idx !== i))
  const updateCountry = (i, key, val) => setCountryBudgets(c => {
    const next = [...c]
    next[i] = { ...next[i], [key]: key === 'dailyBudget' ? parseFloat(val) || 0 : val }
    return next
  })
  const handleSubmit = () => {
    const asins = {}
    countryBudgets.forEach(({ countryCode }) => { asins[countryCode] = asinsByCountry[countryCode] || [] })
    onSave({ campaign_name: name, country_budgets: countryBudgets, asins_by_country: asins }, skipApproval)
  }
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm">
      <div className="bg-white rounded-xl shadow-2xl border border-slate-200 w-full max-w-lg mx-4 max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-100 sticky top-0 bg-white">
          <h3 className="text-base font-semibold text-slate-900">Quick launch AUTO campaign</h3>
          <button onClick={onClose} className="p-1 rounded-lg hover:bg-slate-100 text-slate-400"><X size={18} /></button>
        </div>
        <div className="px-5 py-4 space-y-4">
          <div>
            <label className="block text-xs font-semibold text-slate-700 uppercase tracking-wide mb-1.5">Campaign name</label>
            <input value={name} onChange={e => setName(e.target.value)} placeholder="My AUTO Campaign" className="input" />
          </div>
          <div>
            <label className="block text-xs font-semibold text-slate-700 uppercase tracking-wide mb-1.5">Country budgets</label>
            {countryBudgets.map((row, i) => (
              <div key={i} className="flex gap-2 items-center mt-2">
                <select value={row.countryCode} onChange={e => updateCountry(i, 'countryCode', e.target.value)} className="input flex-1">
                  {COUNTRY_OPTIONS.map(o => <option key={o.code} value={o.code}>{o.label}</option>)}
                </select>
                <input type="number" min="0" step="0.01" value={row.dailyBudget} onChange={e => updateCountry(i, 'dailyBudget', e.target.value)} className="input w-28" />
                <button onClick={() => removeCountry(i)} className="p-1.5 text-red-500 hover:bg-red-50 rounded" disabled={countryBudgets.length <= 1}><Trash2 size={14} /></button>
              </div>
            ))}
            <button onClick={addCountry} className="text-sm text-brand-600 hover:underline mt-2 flex items-center gap-1"><Plus size={14} /> Add country</button>
          </div>
          <div>
            <label className="block text-xs font-semibold text-slate-700 uppercase tracking-wide mb-1.5">ASINs per country (optional)</label>
            <p className="text-xs text-slate-500 mb-2">Enter ASINs separated by comma, e.g. B08N5WRWNW, B09XYZ</p>
            {countryBudgets.map(({ countryCode }) => (
              <div key={countryCode} className="flex gap-2 items-center mt-1">
                <span className="text-sm text-slate-600 w-8">{countryCode}</span>
                <input
                  value={(asinsByCountry[countryCode] || []).join(', ')}
                  onChange={e => setAsinsByCountry(a => ({ ...a, [countryCode]: e.target.value.split(',').map(s => s.trim()).filter(Boolean) }))}
                  placeholder="ASINs"
                  className="input flex-1"
                />
              </div>
            ))}
          </div>
          <label className="flex items-center gap-2 cursor-pointer pt-2 border-t border-slate-100">
            <input type="checkbox" checked={skipApproval} onChange={e => setSkipApproval(e.target.checked)} className="rounded border-slate-300 text-brand-600" />
            <span className="text-sm text-slate-600">Apply directly to Amazon (skip approval queue)</span>
          </label>
        </div>
        <div className="flex justify-end gap-2 px-5 py-3 border-t border-slate-100 bg-slate-50 rounded-b-xl sticky bottom-0">
          <button onClick={onClose} className="btn-ghost text-sm">Cancel</button>
          <button onClick={handleSubmit} disabled={saving || !name.trim()} className="btn-primary text-sm">
            {saving ? <><Loader2 size={14} className="animate-spin" /> Creating...</> : <><Rocket size={14} /> Create campaign</>}
          </button>
        </div>
      </div>
    </div>
  )
}

// ══════════════════════════════════════════════════════════════════════
//  MAIN CAMPAIGN MANAGER PAGE
// ══════════════════════════════════════════════════════════════════════

export default function CampaignManager() {
  const { activeAccountId, activeAccount, loading: accountLoading } = useAccount()

  // Navigation state: which level are we viewing?
  const [view, setView] = useState('campaigns') // campaigns | ad-groups | targets | ads
  const [selectedCampaign, setSelectedCampaign] = useState(null)
  const [selectedAdGroup, setSelectedAdGroup] = useState(null)

  // Data
  const [stats, setStats] = useState(null)
  const [campaigns, setCampaigns] = useState([])
  const [adGroups, setAdGroups] = useState([])
  const [targets, setTargets] = useState([])
  const [ads, setAds] = useState([])

  // Pagination
  const [page, setPage] = useState(1)
  const [totalPages, setTotalPages] = useState(1)
  const [totalCampaigns, setTotalCampaigns] = useState(0)
  const PAGE_SIZE = 25

  // UI state
  const [loading, setLoading] = useState(false)
  const [searchTerm, setSearchTerm] = useState('')
  const [stateFilter, setStateFilter] = useState('')
  const [campaignTypeFilter, setCampaignTypeFilter] = useState('')
  const [targetingTypeFilter, setTargetingTypeFilter] = useState('')
  const [dateRange, setDateRange] = useState(() => {
    const { from, to } = getPresetRange('this_month')
    const iso = (d) => {
      const y = d.getFullYear()
      const m = String(d.getMonth() + 1).padStart(2, '0')
      const day = String(d.getDate()).padStart(2, '0')
      return `${y}-${m}-${day}`
    }
    return { preset: 'this_month', start: iso(from), end: iso(to), label: 'This month' }
  })
  const [pickerOpen, setPickerOpen] = useState(false)
  const pickerRef = useRef(null)
  const [filterOpen, setFilterOpen] = useState(false)
  const [columnsOpen, setColumnsOpen] = useState(false)
  const [visibleColumns, setVisibleColumns] = useState(new Set(['type', 'state', 'budget', 'spend', 'sales', 'acos', 'actions']))
  const [sortBy, setSortBy] = useState('campaign_name')
  const [sortDir, setSortDir] = useState('asc')
  const [error, setError] = useState(null)
  const [successMsg, setSuccessMsg] = useState(null)

  // Bulk selection
  const [selectedCampaignIds, setSelectedCampaignIds] = useState(new Set())
  const [selectedAdGroupIds, setSelectedAdGroupIds] = useState(new Set())
  const [selectedTargetIds, setSelectedTargetIds] = useState(new Set())
  const [selectedAdIds, setSelectedAdIds] = useState(new Set())
  const [bulkActionsOpen, setBulkActionsOpen] = useState(false)
  const [bulkProcessing, setBulkProcessing] = useState(false)

  // Modals
  const [editModal, setEditModal] = useState(null) // { title, fields, onSave }
  const [confirmModal, setConfirmModal] = useState(null) // { title, message, onConfirm }
  const [addCountryModal, setAddCountryModal] = useState(null) // campaign
  const [singleshotModal, setSingleshotModal] = useState(false)
  const [products, setProducts] = useState([])
  const [modalSaving, setModalSaving] = useState(false)

  // ── Load data based on current view ─────────────────────────────
  const loadStats = useCallback(async () => {
    if (!activeAccountId) return
    try {
      const data = await campaignManager.stats(activeAccountId)
      setStats(data)
    } catch { /* ignore */ }
  }, [activeAccountId])

  const loadCampaigns = useCallback(async (pageNum) => {
    if (!activeAccountId) {
      setLoading(false)
      setCampaigns([])
      setError(null)
      return
    }
    setLoading(true)
    setError(null)
    const currentPage = pageNum || page
    try {
      const opts = { page: currentPage, page_size: PAGE_SIZE, sort_by: sortBy, sort_dir: sortDir }
      if (stateFilter) opts.state = stateFilter
      if (campaignTypeFilter) opts.campaign_type = campaignTypeFilter
      if (targetingTypeFilter) opts.targeting_type = targetingTypeFilter
      if (searchTerm) opts.search = searchTerm
      const useCustom = dateRange.preset === 'custom' && dateRange.start && dateRange.end
      if (useCustom) {
        opts.date_from = dateRange.start
        opts.date_to = dateRange.end
      } else {
        opts.preset = dateRange.preset || 'this_month'
      }
      const data = await campaignManager.listCampaigns(activeAccountId, opts)
      setCampaigns(data.campaigns || [])
      setTotalPages(data.total_pages || 1)
      setTotalCampaigns(data.total || 0)
      setPage(data.page || currentPage)
    } catch (err) {
      const msg = err.message || ''
      if (msg.includes('502') || msg.toLowerCase().includes('failed to fetch') || msg.toLowerCase().includes('network')) {
        setError('Service temporarily unavailable. Please try again in a moment.')
      } else if (msg === 'Not Found' || msg.includes('404')) {
        setError('Could not load campaigns. Add API credentials in Settings, discover accounts on the Dashboard, and select an active profile.')
      } else {
        setError(msg)
      }
    } finally {
      setLoading(false)
    }
  }, [activeAccountId, stateFilter, campaignTypeFilter, targetingTypeFilter, searchTerm, dateRange.preset, dateRange.start, dateRange.end, sortBy, sortDir, page])

  const loadAdGroups = useCallback(async (campaignId) => {
    setLoading(true)
    setError(null)
    try {
      const data = await campaignManager.listAdGroups(campaignId, activeAccountId)
      setAdGroups(data.ad_groups || [])
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [activeAccountId])

  const loadTargets = useCallback(async (adGroupId) => {
    setLoading(true)
    setError(null)
    try {
      const data = await campaignManager.listTargets(adGroupId, activeAccountId)
      setTargets(data.targets || [])
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [activeAccountId])

  const loadAds = useCallback(async (adGroupId) => {
    try {
      const data = await campaignManager.listAds(adGroupId, activeAccountId)
      setAds(data.ads || [])
    } catch { /* ignore */ }
  }, [activeAccountId])

  useEffect(() => {
    if (!activeAccountId) {
      setLoading(false)
      setError(null)
      setCampaigns([])
      setStats(null)
      return
    }
    loadStats()
    loadCampaigns(1)
  }, [activeAccountId, stateFilter, campaignTypeFilter, targetingTypeFilter, searchTerm, dateRange.preset, dateRange.start, dateRange.end, sortBy, sortDir]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!pickerOpen) return
    function handleClickOutside(e) {
      if (pickerRef.current && !pickerRef.current.contains(e.target)) setPickerOpen(false)
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [pickerOpen])

  // ── Navigation helpers ──────────────────────────────────────────
  function openCampaign(campaign) {
    setSelectedCampaign(campaign)
    setView('ad-groups')
    setSelectedCampaignIds(new Set())
    loadAdGroups(campaign.amazon_campaign_id)
  }

  function openAdGroup(adGroup) {
    setSelectedAdGroup(adGroup)
    setView('targets')
    setSelectedAdGroupIds(new Set())
    loadTargets(adGroup.amazon_ad_group_id)
    loadAds(adGroup.amazon_ad_group_id)
  }

  function goBack() {
    if (view === 'targets' || view === 'ads') {
      setSelectedAdGroup(null)
      setView('ad-groups')
      if (selectedCampaign) loadAdGroups(selectedCampaign.amazon_campaign_id)
    } else if (view === 'ad-groups') {
      setSelectedCampaign(null)
      setView('campaigns')
    }
  }

  // ── Pagination helpers ───────────────────────────────────────────
  function goToPage(p) {
    if (p < 1 || p > totalPages) return
    setPage(p)
    loadCampaigns(p)
  }

  // ── Sync (persistent across navigation via SyncContext) ─
  const { campaignSync, startCampaignSync, resumeCampaignSyncIfNeeded } = useSync()
  const syncing = campaignSync.status === 'running'
  const prevCampaignSyncStatusRef = useRef(campaignSync.status)

  useEffect(() => {
    resumeCampaignSyncIfNeeded(activeAccountId)
  }, [activeAccountId, resumeCampaignSyncIfNeeded])

  // Refresh data when sync completes (user on this page or returns)
  useEffect(() => {
    if (prevCampaignSyncStatusRef.current !== 'completed' && campaignSync.status === 'completed' && campaignSync.credentialId === activeAccountId) {
      loadStats()
      loadCampaigns(1)
    }
    prevCampaignSyncStatusRef.current = campaignSync.status
  }, [campaignSync.status, campaignSync.credentialId, activeAccountId, loadStats, loadCampaigns])

  function handleSync() {
    startCampaignSync(activeAccountId)
  }

  // ── Campaign actions ────────────────────────────────────────────
  function handleEditCampaign(campaign) {
    setEditModal({
      title: `Edit Campaign — ${campaign.campaign_name}`,
      skipApprovalOption: true,
      fields: [
        {
          key: 'name',
          label: 'Campaign Name',
          type: 'text',
          value: campaign.campaign_name || '',
          placeholder: 'My Campaign',
        },
        {
          key: 'daily_budget',
          label: 'Daily Budget ($)',
          type: 'number',
          value: campaign.daily_budget || '',
          placeholder: '50.00',
          hint: 'The daily budget cap for this campaign',
        },
        {
          key: 'state',
          label: 'State',
          type: 'select',
          value: (campaign.state || 'enabled').toLowerCase(),
          options: [
            { value: 'enabled', label: 'Enabled' },
            { value: 'paused', label: 'Paused' },
            { value: 'archived', label: 'Archived' },
          ],
        },
      ],
      onSave: async (vals, skipApproval = false) => {
        setModalSaving(true)
        try {
          const updates = {}
          if (vals.name && vals.name !== campaign.campaign_name) updates.name = vals.name
          if (vals.daily_budget != null && vals.daily_budget !== '') updates.dailyBudget = parseFloat(vals.daily_budget)
          if (vals.state) updates.state = vals.state.toUpperCase()
          if (Object.keys(updates).length === 0) {
            setEditModal(null)
            return
          }
          await campaignManager.updateCampaign(campaign.amazon_campaign_id, updates, activeAccountId, skipApproval)
          setSuccessMsg(skipApproval ? 'Campaign updated directly on Amazon' : 'Campaign changes sent to approval queue')
          setTimeout(() => setSuccessMsg(null), 4000)
          setEditModal(null)
          loadCampaigns()
        } catch (err) {
          setError(err.message)
        } finally {
          setModalSaving(false)
        }
      },
    })
  }

  function handleEditBudget(campaign) {
    setEditModal({
      title: `Edit Budget — ${campaign.campaign_name}`,
      skipApprovalOption: true,
      fields: [{
        key: 'daily_budget',
        label: 'Daily Budget ($)',
        type: 'number',
        value: campaign.daily_budget || '',
        placeholder: '50.00',
        hint: 'The daily budget cap for this campaign',
      }],
      onSave: async (vals, skipApproval = false) => {
        setModalSaving(true)
        try {
          await campaignManager.updateCampaignBudget(
            campaign.amazon_campaign_id,
            parseFloat(vals.daily_budget),
            activeAccountId,
            skipApproval,
          )
          setSuccessMsg(skipApproval ? 'Budget updated directly on Amazon' : 'Budget change sent to approval queue')
          setTimeout(() => setSuccessMsg(null), 4000)
          setEditModal(null)
          loadCampaigns()
        } catch (err) {
          setError(err.message)
        } finally {
          setModalSaving(false)
        }
      },
    })
  }

  function handleChangeState(campaign, newState) {
    setConfirmModal({
      title: `${newState === 'enabled' ? 'Enable' : newState === 'paused' ? 'Pause' : 'Archive'} Campaign?`,
      message: `Change "${campaign.campaign_name}" state to ${newState}.`,
      skipApprovalOption: true,
      confirmLabel: newState === 'enabled' ? 'Enable' : newState === 'paused' ? 'Pause' : 'Archive',
      confirmingLabel: 'Updating...',
      onConfirm: async (skipApproval = false) => {
        setModalSaving(true)
        try {
          await campaignManager.updateCampaignState(campaign.amazon_campaign_id, newState, activeAccountId, skipApproval)
          setSuccessMsg(skipApproval ? `Campaign ${newState} directly on Amazon` : `State change sent to approval queue`)
          setTimeout(() => setSuccessMsg(null), 4000)
          setConfirmModal(null)
          loadCampaigns()
        } catch (err) {
          setError(err.message)
        } finally {
          setModalSaving(false)
        }
      },
    })
  }

  function handleDeleteCampaign(campaign) {
    setConfirmModal({
      title: 'Delete Campaign?',
      message: `This will delete "${campaign.campaign_name}" and all associated ad groups, ads, and targets.`,
      skipApprovalOption: true,
      onConfirm: async (skipApproval = false) => {
        setModalSaving(true)
        try {
          await campaignManager.deleteCampaign(campaign.amazon_campaign_id, activeAccountId, skipApproval)
          setSuccessMsg(skipApproval ? 'Campaign deleted directly on Amazon' : 'Campaign deletion sent to approval queue')
          setTimeout(() => setSuccessMsg(null), 4000)
          setConfirmModal(null)
          loadCampaigns()
        } catch (err) {
          setError(err.message)
        } finally {
          setModalSaving(false)
        }
      },
    })
  }

  async function handleAddCountrySave(countries, skipApproval) {
    if (!addCountryModal) return
    setModalSaving(true)
    try {
      await campaignManager.addCountry(addCountryModal.amazon_campaign_id, countries, activeAccountId, skipApproval)
      setSuccessMsg(skipApproval ? 'Countries added directly on Amazon' : 'Add country sent to approval queue')
      setTimeout(() => setSuccessMsg(null), 4000)
      setAddCountryModal(null)
      loadCampaigns()
    } catch (err) {
      setError(err.message)
    } finally {
      setModalSaving(false)
    }
  }

  async function handleSingleshotSave(data, skipApproval) {
    setModalSaving(true)
    try {
      await campaignManager.createSingleshot(data, activeAccountId, skipApproval)
      setSuccessMsg(skipApproval ? 'Campaign created directly on Amazon' : 'Campaign creation sent to approval queue')
      setTimeout(() => setSuccessMsg(null), 4000)
      setSingleshotModal(false)
      loadCampaigns()
    } catch (err) {
      setError(err.message)
    } finally {
      setModalSaving(false)
    }
  }

  async function openSingleshotModal() {
    try {
      const data = await accounts.products(activeAccountId)
      setProducts(data.products || [])
    } catch { /* ignore */ }
    setSingleshotModal(true)
  }

  // ── Ad Group actions ────────────────────────────────────────────
  function handleEditAdGroup(adGroup) {
    setEditModal({
      title: `Edit Ad Group — ${adGroup.ad_group_name}`,
      skipApprovalOption: true,
      fields: [
        {
          key: 'name',
          label: 'Name',
          type: 'text',
          value: adGroup.ad_group_name || '',
        },
        {
          key: 'defaultBid',
          label: 'Default Bid ($)',
          type: 'number',
          value: adGroup.default_bid || '',
          placeholder: '0.75',
        },
        {
          key: 'state',
          label: 'State',
          type: 'select',
          value: (adGroup.state || 'enabled').toLowerCase(),
          options: [
            { value: 'enabled', label: 'Enabled' },
            { value: 'paused', label: 'Paused' },
            { value: 'archived', label: 'Archived' },
          ],
        },
      ],
      onSave: async (vals, skipApproval = false) => {
        setModalSaving(true)
        try {
          const updates = {}
          if (vals.name && vals.name !== adGroup.ad_group_name) updates.name = vals.name
          if (vals.defaultBid) updates.defaultBid = parseFloat(vals.defaultBid)
          if (vals.state) updates.state = vals.state
          await campaignManager.updateAdGroup(adGroup.amazon_ad_group_id, updates, activeAccountId, skipApproval)
          setSuccessMsg(skipApproval ? 'Ad group updated directly on Amazon' : 'Ad group update sent to approval queue')
          setTimeout(() => setSuccessMsg(null), 4000)
          setEditModal(null)
          if (selectedCampaign) loadAdGroups(selectedCampaign.amazon_campaign_id)
        } catch (err) {
          setError(err.message)
        } finally {
          setModalSaving(false)
        }
      },
    })
  }

  function handleDeleteAdGroup(adGroup) {
    setConfirmModal({
      title: 'Delete Ad Group?',
      message: `This will delete "${adGroup.ad_group_name}" and all associated ads and targets.`,
      skipApprovalOption: true,
      onConfirm: async (skipApproval = false) => {
        setModalSaving(true)
        try {
          await campaignManager.deleteAdGroup(adGroup.amazon_ad_group_id, activeAccountId, skipApproval)
          setSuccessMsg(skipApproval ? 'Ad group deleted directly on Amazon' : 'Ad group deletion sent to approval queue')
          setTimeout(() => setSuccessMsg(null), 4000)
          setConfirmModal(null)
          if (selectedCampaign) loadAdGroups(selectedCampaign.amazon_campaign_id)
        } catch (err) {
          setError(err.message)
        } finally {
          setModalSaving(false)
        }
      },
    })
  }

  // ── Target actions ──────────────────────────────────────────────
  function handleEditTarget(target) {
    setEditModal({
      title: `Edit Target — ${target.expression_value || target.amazon_target_id}`,
      skipApprovalOption: true,
      fields: [
        {
          key: 'bid',
          label: 'Bid ($)',
          type: 'number',
          value: target.bid || '',
          placeholder: '0.50',
        },
        {
          key: 'state',
          label: 'State',
          type: 'select',
          value: (target.state || 'enabled').toLowerCase(),
          options: [
            { value: 'enabled', label: 'Enabled' },
            { value: 'paused', label: 'Paused' },
            { value: 'archived', label: 'Archived' },
          ],
        },
      ],
      onSave: async (vals, skipApproval = false) => {
        setModalSaving(true)
        try {
          const updates = {}
          if (vals.bid) updates.bid = parseFloat(vals.bid)
          if (vals.state) updates.state = vals.state
          await campaignManager.updateTarget(target.amazon_target_id, updates, activeAccountId, skipApproval)
          setSuccessMsg(skipApproval ? 'Target updated directly on Amazon' : 'Target update sent to approval queue')
          setTimeout(() => setSuccessMsg(null), 4000)
          setEditModal(null)
          if (selectedAdGroup) loadTargets(selectedAdGroup.amazon_ad_group_id)
        } catch (err) {
          setError(err.message)
        } finally {
          setModalSaving(false)
        }
      },
    })
  }

  function handleDeleteTarget(target) {
    setConfirmModal({
      title: 'Delete Target?',
      message: `This will delete target "${target.expression_value || target.amazon_target_id}".`,
      skipApprovalOption: true,
      onConfirm: async (skipApproval = false) => {
        setModalSaving(true)
        try {
          await campaignManager.deleteTarget(target.amazon_target_id, activeAccountId, skipApproval)
          setSuccessMsg(skipApproval ? 'Target deleted directly on Amazon' : 'Target deletion sent to approval queue')
          setTimeout(() => setSuccessMsg(null), 4000)
          setConfirmModal(null)
          if (selectedAdGroup) loadTargets(selectedAdGroup.amazon_ad_group_id)
        } catch (err) {
          setError(err.message)
        } finally {
          setModalSaving(false)
        }
      },
    })
  }

  // ── Ad actions ──────────────────────────────────────────────────
  function handleEditAd(ad) {
    setEditModal({
      title: `Edit Ad — ${ad.ad_name || ad.asin || (ad.amazon_ad_id ? `Ad #${ad.amazon_ad_id.slice(-8)}` : 'Ad')}`,
      skipApprovalOption: true,
      fields: [
        {
          key: 'name',
          label: 'Ad Name',
          type: 'text',
          value: ad.ad_name || '',
          placeholder: ad.asin || 'Ad name',
        },
        {
          key: 'state',
          label: 'State',
          type: 'select',
          value: (ad.state || 'enabled').toLowerCase(),
          options: [
            { value: 'enabled', label: 'Enabled' },
            { value: 'paused', label: 'Paused' },
            { value: 'archived', label: 'Archived' },
          ],
        },
      ],
      onSave: async (vals, skipApproval = false) => {
        setModalSaving(true)
        try {
          const updates = {}
          if (vals.name && vals.name !== ad.ad_name) updates.name = vals.name
          if (vals.state) updates.state = vals.state
          await campaignManager.updateAd(ad.amazon_ad_id, updates, activeAccountId, skipApproval)
          setSuccessMsg(skipApproval ? 'Ad updated directly on Amazon' : 'Ad update sent to approval queue')
          setTimeout(() => setSuccessMsg(null), 4000)
          setEditModal(null)
          if (selectedAdGroup) loadAds(selectedAdGroup.amazon_ad_group_id)
        } catch (err) {
          setError(err.message)
        } finally {
          setModalSaving(false)
        }
      },
    })
  }

  function handleDeleteAd(ad) {
    setConfirmModal({
      title: 'Delete Ad?',
      message: `This will delete ad "${ad.ad_name || ad.asin || (ad.amazon_ad_id ? `Ad #${ad.amazon_ad_id.slice(-8)}` : 'this ad')}".`,
      skipApprovalOption: true,
      onConfirm: async (skipApproval = false) => {
        setModalSaving(true)
        try {
          await campaignManager.deleteAd(ad.amazon_ad_id, activeAccountId, skipApproval)
          setSuccessMsg(skipApproval ? 'Ad deleted directly on Amazon' : 'Ad deletion sent to approval queue')
          setTimeout(() => setSuccessMsg(null), 4000)
          setConfirmModal(null)
          if (selectedAdGroup) loadAds(selectedAdGroup.amazon_ad_group_id)
        } catch (err) {
          setError(err.message)
        } finally {
          setModalSaving(false)
        }
      },
    })
  }

  // ── Add New Target ──────────────────────────────────────────────
  function handleAddTarget() {
    setEditModal({
      title: 'Add Keyword / Target',
      skipApprovalOption: true,
      fields: [
        { key: 'keyword', label: 'Keyword', type: 'text', placeholder: 'e.g. wireless earbuds' },
        {
          key: 'matchType', label: 'Match Type', type: 'select', value: 'BROAD',
          options: [
            { value: 'BROAD', label: 'Broad' },
            { value: 'PHRASE', label: 'Phrase' },
            { value: 'EXACT', label: 'Exact' },
          ],
        },
        { key: 'bid', label: 'Bid ($)', type: 'number', placeholder: '0.75' },
      ],
      onSave: async (vals, skipApproval = false) => {
        setModalSaving(true)
        try {
          await campaignManager.createTarget(
            selectedAdGroup.amazon_ad_group_id,
            { keyword: vals.keyword, matchType: vals.matchType, bid: parseFloat(vals.bid) || undefined },
            activeAccountId,
            skipApproval,
          )
          setSuccessMsg(skipApproval ? 'Target created directly on Amazon' : 'Target creation sent to approval queue')
          setTimeout(() => setSuccessMsg(null), 4000)
          setEditModal(null)
          loadTargets(selectedAdGroup.amazon_ad_group_id)
        } catch (err) {
          setError(err.message)
        } finally {
          setModalSaving(false)
        }
      },
    })
  }

  // ── Bulk actions ─────────────────────────────────────────────────
  async function handleBulkCampaignState(newState) {
    if (selectedCampaignIds.size === 0) return
    setBulkProcessing(true)
    setError(null)
    try {
      const ids = [...selectedCampaignIds]
      for (const id of ids) {
        await campaignManager.updateCampaignState(id, newState, activeAccountId, true)
      }
      setSuccessMsg(`${ids.length} campaign(s) ${newState} directly on Amazon`)
      setTimeout(() => setSuccessMsg(null), 4000)
      setSelectedCampaignIds(new Set())
      loadCampaigns()
    } catch (err) {
      setError(err.message)
    } finally {
      setBulkProcessing(false)
      setBulkActionsOpen(false)
    }
  }

  async function handleBulkAdGroupState(newState) {
    if (selectedAdGroupIds.size === 0) return
    setBulkProcessing(true)
    setError(null)
    try {
      const ids = [...selectedAdGroupIds]
      for (const id of ids) {
        await campaignManager.updateAdGroup(id, { state: newState }, activeAccountId, true)
      }
      setSuccessMsg(`${ids.length} ad group(s) ${newState} directly on Amazon`)
      setTimeout(() => setSuccessMsg(null), 4000)
      setSelectedAdGroupIds(new Set())
      if (selectedCampaign) loadAdGroups(selectedCampaign.amazon_campaign_id)
    } catch (err) {
      setError(err.message)
    } finally {
      setBulkProcessing(false)
      setBulkActionsOpen(false)
    }
  }

  async function handleBulkTargetState(newState) {
    if (selectedTargetIds.size === 0) return
    setBulkProcessing(true)
    setError(null)
    try {
      const ids = [...selectedTargetIds]
      for (const id of ids) {
        await campaignManager.updateTarget(id, { state: newState }, activeAccountId, true)
      }
      setSuccessMsg(`${ids.length} target(s) ${newState} directly on Amazon`)
      setTimeout(() => setSuccessMsg(null), 4000)
      setSelectedTargetIds(new Set())
      if (selectedAdGroup) loadTargets(selectedAdGroup.amazon_ad_group_id)
    } catch (err) {
      setError(err.message)
    } finally {
      setBulkProcessing(false)
      setBulkActionsOpen(false)
    }
  }

  async function handleBulkAdState(newState) {
    if (selectedAdIds.size === 0) return
    setBulkProcessing(true)
    setError(null)
    try {
      const ids = [...selectedAdIds]
      for (const id of ids) {
        await campaignManager.updateAd(id, { state: newState }, activeAccountId, true)
      }
      setSuccessMsg(`${ids.length} ad(s) ${newState} directly on Amazon`)
      setTimeout(() => setSuccessMsg(null), 4000)
      setSelectedAdIds(new Set())
      if (selectedAdGroup) loadAds(selectedAdGroup.amazon_ad_group_id)
    } catch (err) {
      setError(err.message)
    } finally {
      setBulkProcessing(false)
      setBulkActionsOpen(false)
    }
  }

  function toggleSelectAllCampaigns(checked) {
    setSelectedCampaignIds(checked ? new Set(campaigns.map(c => c.amazon_campaign_id)) : new Set())
  }

  function toggleSelectAllAdGroups(checked) {
    setSelectedAdGroupIds(checked ? new Set(adGroups.map(g => g.amazon_ad_group_id)) : new Set())
  }

  function toggleSelectAllTargets(checked) {
    setSelectedTargetIds(checked ? new Set(targets.map(t => t.amazon_target_id)) : new Set())
  }

  function toggleSelectAllAds(checked) {
    setSelectedAdIds(checked ? new Set(ads.map(a => a.amazon_ad_id)) : new Set())
  }

  // ── Breadcrumb ──────────────────────────────────────────────────
  function Breadcrumb() {
    return (
      <div className="flex items-center gap-1.5 text-sm">
        <button
          onClick={() => { setSelectedCampaign(null); setSelectedAdGroup(null); setView('campaigns') }}
          className={`font-medium transition-colors ${view === 'campaigns' ? 'text-slate-900' : 'text-brand-600 hover:text-brand-700'}`}
        >
          Campaigns
        </button>
        {selectedCampaign && (
          <>
            <ChevronRight size={14} className="text-slate-300" />
            <button
              onClick={() => { setSelectedAdGroup(null); setView('ad-groups'); loadAdGroups(selectedCampaign.amazon_campaign_id) }}
              className={`font-medium truncate max-w-[200px] transition-colors ${view === 'ad-groups' ? 'text-slate-900' : 'text-brand-600 hover:text-brand-700'}`}
            >
              {selectedCampaign.campaign_name}
            </button>
          </>
        )}
        {selectedAdGroup && (
          <>
            <ChevronRight size={14} className="text-slate-300" />
            <span className="font-medium text-slate-900 truncate max-w-[200px]">
              {selectedAdGroup.ad_group_name}
            </span>
          </>
        )}
      </div>
    )
  }

  // ══════════════════════════════════════════════════════════════════
  //  RENDER
  // ══════════════════════════════════════════════════════════════════

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 tracking-tight">Campaign Manager</h1>
          <p className="mt-1 text-sm text-slate-500">
            {activeAccount
              ? <>Managing <span className="font-medium text-slate-700">{activeAccount.account_name || activeAccount.name}</span> &middot; {activeAccount.marketplace || activeAccount.region?.toUpperCase()}</>
              : 'Select an account in the header to view campaigns'}
          </p>
          <div className="mt-1"><Breadcrumb /></div>
        </div>
        <div className="flex items-center gap-2">
          {view !== 'campaigns' && (
            <button onClick={goBack} className="btn-ghost text-sm">
              <ArrowLeft size={16} /> Back
            </button>
          )}
          <button onClick={handleSync} disabled={syncing || !activeAccountId} className="btn-secondary text-sm">
            {syncing ? <><Loader2 size={14} className="animate-spin" /> Syncing...</> : <><RefreshCw size={14} /> Sync Data</>}
          </button>
        </div>
      </div>

      {/* No account selected — show setup prompt instead of errors */}
      {!accountLoading && !activeAccountId && (
        <EmptyState
          icon={Megaphone}
          title="Select an account to view campaigns"
          description="Add API credentials in Settings, then discover accounts on the Dashboard and select an active profile from the header."
          action={
            <div className="flex flex-wrap justify-center gap-3">
              <Link to="/" className="btn-primary text-sm">Go to Dashboard</Link>
              <Link to="/settings" className="btn-secondary text-sm">Settings</Link>
            </div>
          }
        />
      )}

      {/* Stats bar — only when we have an account */}
      {activeAccountId && stats && view === 'campaigns' && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <div className="card px-4 py-3">
            <div className="flex items-center gap-2.5">
              <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-brand-50 text-brand-600">
                <Megaphone size={16} />
              </div>
              <div>
                <p className="text-xs text-slate-500">Campaigns</p>
                <p className="text-lg font-bold text-slate-900">{stats.campaigns.total}</p>
              </div>
            </div>
            <div className="mt-1.5 flex items-center gap-2 text-[10px]">
              <span className="text-emerald-600">{stats.campaigns.enabled} active</span>
              <span className="text-slate-300">&middot;</span>
              <span className="text-amber-600">{stats.campaigns.paused} paused</span>
            </div>
          </div>
          <div className="card px-4 py-3">
            <div className="flex items-center gap-2.5">
              <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-blue-50 text-blue-600">
                <Layers size={16} />
              </div>
              <div>
                <p className="text-xs text-slate-500">Ad Groups</p>
                <p className="text-lg font-bold text-slate-900">{stats.ad_groups.total}</p>
              </div>
            </div>
          </div>
          <div className="card px-4 py-3">
            <div className="flex items-center gap-2.5">
              <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-purple-50 text-purple-600">
                <Target size={16} />
              </div>
              <div>
                <p className="text-xs text-slate-500">Targets</p>
                <p className="text-lg font-bold text-slate-900">{stats.targets.total}</p>
              </div>
            </div>
          </div>
          <div className="card px-4 py-3">
            <div className="flex items-center gap-2.5">
              <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-emerald-50 text-emerald-600">
                <FileImage size={16} />
              </div>
              <div>
                <p className="text-xs text-slate-500">Ads</p>
                <p className="text-lg font-bold text-slate-900">{stats.ads.total}</p>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Success / Error — only when we have an account (sync success in global banner) */}
      {activeAccountId && successMsg && (
        <div className="card bg-emerald-50 border-emerald-200 px-4 py-3 text-sm text-emerald-700 flex items-center gap-2">
          <Check size={16} /> {successMsg}
          <button onClick={() => setSuccessMsg(null)} className="ml-auto text-emerald-400 hover:text-emerald-600"><X size={14} /></button>
        </div>
      )}
      {activeAccountId && error && (
        <div className="card bg-red-50 border-red-200 px-4 py-3 text-sm text-red-700 flex items-center gap-2">
          <AlertTriangle size={16} /> {error}
          <button onClick={() => setError(null)} className="ml-auto text-red-400 hover:text-red-600"><X size={14} /></button>
        </div>
      )}

      {/* ── Campaigns List ───────────────────────────────────────── */}
      {activeAccountId && view === 'campaigns' && (
        <>
          {/* Amazon-style rich filter bar */}
          <div className="flex flex-wrap items-center gap-2 sm:gap-3">
            <h2 className="text-lg font-semibold text-slate-900 mr-2">Campaigns</h2>
            <div className="relative flex-1 min-w-[200px] max-w-md">
              <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none" />
              <input
                type="text"
                placeholder="Find a campaign"
                value={searchTerm}
                onChange={e => { setSearchTerm(e.target.value); setPage(1) }}
                className="input pl-9 w-full"
              />
            </div>
            {/* Filter by dropdown */}
            <div className="relative">
              <button
                onClick={() => { setFilterOpen(!filterOpen); setColumnsOpen(false) }}
                className={`inline-flex items-center gap-1.5 px-3 py-2 text-sm font-medium rounded-lg border transition-colors ${filterOpen || stateFilter || campaignTypeFilter || targetingTypeFilter ? 'border-brand-300 bg-brand-50 text-brand-700' : 'border-slate-200 text-slate-600 hover:bg-slate-50'}`}
              >
                <Filter size={14} />
                Filter by
                {(stateFilter || campaignTypeFilter || targetingTypeFilter) && (
                  <span className="ml-0.5 w-1.5 h-1.5 rounded-full bg-brand-500" />
                )}
                <ChevronDown size={14} className={`transition-transform ${filterOpen ? 'rotate-180' : ''}`} />
              </button>
              {filterOpen && (
                <>
                  <div className="fixed inset-0 z-10" onClick={() => setFilterOpen(false)} />
                  <div className="absolute right-0 mt-1 z-20 w-64 p-3 bg-white rounded-xl shadow-lg border border-slate-200">
                    <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">Filters</p>
                    <div className="space-y-3">
                      <div>
                        <label className="block text-xs font-medium text-slate-600 mb-1">State</label>
                        <select
                          value={stateFilter}
                          onChange={e => { setStateFilter(e.target.value); setPage(1) }}
                          className="input w-full text-sm"
                        >
                          <option value="">All states</option>
                          <option value="enabled">Enabled</option>
                          <option value="paused">Paused</option>
                          <option value="archived">Archived</option>
                        </select>
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-slate-600 mb-1">Campaign type</label>
                        <select
                          value={campaignTypeFilter}
                          onChange={e => { setCampaignTypeFilter(e.target.value); setPage(1) }}
                          className="input w-full text-sm"
                        >
                          <option value="">All types</option>
                          <option value="SPONSORED_PRODUCTS">SP Products</option>
                          <option value="SPONSORED_BRANDS">SP Brands</option>
                          <option value="SPONSORED_DISPLAY">SP Display</option>
                        </select>
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-slate-600 mb-1">Targeting</label>
                        <select
                          value={targetingTypeFilter}
                          onChange={e => { setTargetingTypeFilter(e.target.value); setPage(1) }}
                          className="input w-full text-sm"
                        >
                          <option value="">All</option>
                          <option value="auto">Auto</option>
                          <option value="manual">Manual</option>
                        </select>
                      </div>
                    </div>
                  </div>
                </>
              )}
            </div>
            {/* Date range filter — same DateRangePicker as Reports */}
            <div className="relative" ref={pickerRef}>
              <button
                type="button"
                onClick={() => { setPickerOpen(o => !o); setPage(1) }}
                className="inline-flex items-center gap-1.5 px-3 py-2 text-sm font-medium rounded-lg border border-slate-200 text-slate-600 hover:bg-slate-50 transition-colors"
              >
                <Calendar size={14} className="text-slate-400 shrink-0" />
                <span>{dateRange.label || 'Date range'}</span>
                <span className="text-slate-400 text-xs">
                  {dateRange.start && dateRange.end ? `${dateRange.start} – ${dateRange.end}` : ''}
                </span>
                <ChevronDown size={14} className={pickerOpen ? 'rotate-180' : ''} />
              </button>
              {pickerOpen && (
                <div className="absolute left-0 top-full mt-1 z-50">
                  <DateRangePicker
                    key={`${dateRange.start}-${dateRange.end}`}
                    value={dateRange}
                    onChange={(v) => { setDateRange(v); setPickerOpen(false); setPage(1) }}
                    onClose={() => setPickerOpen(false)}
                  />
                </div>
              )}
            </div>
            {/* Columns dropdown */}
            <div className="relative">
              <button
                onClick={() => { setColumnsOpen(!columnsOpen); setFilterOpen(false) }}
                className="inline-flex items-center gap-1.5 px-3 py-2 text-sm font-medium rounded-lg border border-slate-200 text-slate-600 hover:bg-slate-50 transition-colors"
              >
                <Columns3 size={14} />
                Columns
                <ChevronDown size={14} className={`transition-transform ${columnsOpen ? 'rotate-180' : ''}`} />
              </button>
              {columnsOpen && (
                <>
                  <div className="fixed inset-0 z-10" onClick={() => setColumnsOpen(false)} />
                  <div className="absolute right-0 mt-1 z-20 w-52 p-3 bg-white rounded-xl shadow-lg border border-slate-200">
                    <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">Show columns</p>
                    <div className="space-y-2">
                      {[
                        { key: 'type', label: 'Type' },
                        { key: 'state', label: 'State' },
                        { key: 'budget', label: 'Budget' },
                        { key: 'spend', label: 'Spend' },
                        { key: 'sales', label: 'Sales' },
                        { key: 'acos', label: 'ACOS' },
                        { key: 'actions', label: 'Actions' },
                      ].map(({ key, label }) => (
                        <label key={key} className="flex items-center gap-2 cursor-pointer">
                          <input
                            type="checkbox"
                            checked={visibleColumns.has(key)}
                            onChange={() => {
                              setVisibleColumns(prev => {
                                const next = new Set(prev)
                                if (next.has(key)) next.delete(key)
                                else next.add(key)
                                return next
                              })
                            }}
                            className="rounded border-slate-300 text-brand-600 focus:ring-brand-500"
                          />
                          <span className="text-sm text-slate-700">{label}</span>
                        </label>
                      ))}
                    </div>
                  </div>
                </>
              )}
            </div>
            {totalCampaigns > 0 && (
              <div className="flex items-center text-xs text-slate-500 shrink-0">
                {totalCampaigns} campaigns
              </div>
            )}
            {view === 'campaigns' && (
              <button
                onClick={openSingleshotModal}
                className="inline-flex items-center gap-1.5 px-3 py-2 text-sm font-medium rounded-lg border border-brand-200 bg-brand-50 text-brand-700 hover:bg-brand-100 transition-colors"
              >
                <Rocket size={14} /> Quick launch AUTO
              </button>
            )}
            {campaigns.length > 0 && (
              <div className="relative">
                <button
                  onClick={() => { setBulkActionsOpen(!bulkActionsOpen) }}
                  disabled={selectedCampaignIds.size === 0 || bulkProcessing}
                  className={`inline-flex items-center gap-1.5 px-3 py-2 text-sm font-medium rounded-lg border transition-colors ${selectedCampaignIds.size > 0 ? 'border-brand-300 bg-brand-50 text-brand-700' : 'border-slate-200 text-slate-400'}`}
                >
                  Bulk actions
                  {selectedCampaignIds.size > 0 && <span className="text-xs">({selectedCampaignIds.size})</span>}
                  <ChevronDown size={14} className={bulkActionsOpen ? 'rotate-180' : ''} />
                </button>
                {bulkActionsOpen && selectedCampaignIds.size > 0 && (
                  <>
                    <div className="fixed inset-0 z-10" onClick={() => setBulkActionsOpen(false)} />
                    <div className="absolute right-0 mt-1 z-20 w-48 py-1 bg-white rounded-lg shadow-lg border border-slate-200">
                      <button onClick={() => handleBulkCampaignState('enabled')} className="w-full px-3 py-2 text-left text-sm text-slate-700 hover:bg-slate-50 flex items-center gap-2">
                        <Play size={14} /> Enable
                      </button>
                      <button onClick={() => handleBulkCampaignState('paused')} className="w-full px-3 py-2 text-left text-sm text-slate-700 hover:bg-slate-50 flex items-center gap-2">
                        <Pause size={14} /> Pause
                      </button>
                    </div>
                  </>
                )}
              </div>
            )}
          </div>

          {loading ? (
            <div className="flex items-center justify-center py-20">
              <Loader2 className="animate-spin text-slate-400" size={28} />
            </div>
          ) : campaigns.length === 0 ? (
            <EmptyState
              icon={Megaphone}
              title="No campaigns found"
              description={searchTerm || stateFilter || campaignTypeFilter || targetingTypeFilter || dateRange.preset !== 'this_month' ? 'Try adjusting your filters' : 'Sync your data to see campaigns here'}
            />
          ) : (
            <div className="card divide-y divide-slate-100 overflow-hidden">
              {/* Table header — sortable */}
              <div className="hidden sm:grid sm:grid-cols-12 gap-3 px-5 py-2.5 bg-slate-50 text-xs font-semibold text-slate-500 uppercase tracking-wider">
                <div className="col-span-4 flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={campaigns.length > 0 && selectedCampaignIds.size === campaigns.length}
                    onChange={e => toggleSelectAllCampaigns(e.target.checked)}
                    className="rounded border-slate-300 text-brand-600 focus:ring-brand-500 shrink-0"
                  />
                  <SortableHeader
                  label="Campaign"
                  colKey="campaign_name"
                  sortBy={sortBy}
                  sortDir={sortDir}
                  onSort={(k) => { setSortBy(k); setSortDir(d => (sortBy === k ? (d === 'asc' ? 'desc' : 'asc') : 'asc')); setPage(1) }}
                  colSpan={1}
                  align="left"
                />
                </div>
                {visibleColumns.has('type') && (
                  <SortableHeader
                    label="Type"
                    colKey="campaign_type"
                    sortBy={sortBy}
                    sortDir={sortDir}
                    onSort={(k) => { setSortBy(k); setSortDir(d => (sortBy === k ? (d === 'asc' ? 'desc' : 'asc') : 'asc')); setPage(1) }}
                    colSpan={1}
                    align="left"
                  />
                )}
                {visibleColumns.has('state') && (
                  <SortableHeader
                    label="State"
                    colKey="state"
                    sortBy={sortBy}
                    sortDir={sortDir}
                    onSort={(k) => { setSortBy(k); setSortDir(d => (sortBy === k ? (d === 'asc' ? 'desc' : 'asc') : 'asc')); setPage(1) }}
                    colSpan={1}
                    align="left"
                  />
                )}
                {visibleColumns.has('budget') && (
                  <SortableHeader
                    label="Budget"
                    colKey="daily_budget"
                    sortBy={sortBy}
                    sortDir={sortDir}
                    onSort={(k) => { setSortBy(k); setSortDir(d => (sortBy === k ? (d === 'asc' ? 'desc' : 'asc') : 'asc')); setPage(1) }}
                    colSpan={1}
                    align="right"
                  />
                )}
                {visibleColumns.has('spend') && (
                  <SortableHeader
                    label="Spend"
                    colKey="spend"
                    sortBy={sortBy}
                    sortDir={sortDir}
                    onSort={(k) => { setSortBy(k); setSortDir(d => (sortBy === k ? (d === 'asc' ? 'desc' : 'asc') : 'desc')); setPage(1) }}
                    colSpan={1}
                    align="right"
                  />
                )}
                {visibleColumns.has('sales') && (
                  <SortableHeader
                    label="Sales"
                    colKey="sales"
                    sortBy={sortBy}
                    sortDir={sortDir}
                    onSort={(k) => { setSortBy(k); setSortDir(d => (sortBy === k ? (d === 'asc' ? 'desc' : 'asc') : 'desc')); setPage(1) }}
                    colSpan={1}
                    align="right"
                  />
                )}
                {visibleColumns.has('acos') && (
                  <SortableHeader
                    label="ACOS"
                    colKey="acos"
                    sortBy={sortBy}
                    sortDir={sortDir}
                    onSort={(k) => { setSortBy(k); setSortDir(d => (sortBy === k ? (d === 'asc' ? 'desc' : 'asc') : 'asc')); setPage(1) }}
                    colSpan={1}
                    align="right"
                  />
                )}
                {visibleColumns.has('actions') && (
                  <div className="text-right" style={{ gridColumn: 'span 2' }}>Actions</div>
                )}
              </div>

              {campaigns.map(c => (
                <div
                  key={c.id}
                  className="grid grid-cols-1 sm:grid-cols-12 gap-2 sm:gap-3 px-5 py-3.5 hover:bg-slate-50/50 transition-colors items-center"
                >
                  {/* Name + checkbox */}
                  <div className="sm:col-span-4 flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={selectedCampaignIds.has(c.amazon_campaign_id)}
                      onChange={e => {
                        setSelectedCampaignIds(prev => {
                          const next = new Set(prev)
                          if (e.target.checked) next.add(c.amazon_campaign_id)
                          else next.delete(c.amazon_campaign_id)
                          return next
                        })
                      }}
                      className="rounded border-slate-300 text-brand-600 focus:ring-brand-500 shrink-0 hidden sm:block"
                    />
                    <button
                      onClick={() => openCampaign(c)}
                      className="text-left group flex-1 min-w-0"
                    >
                      <p className="text-sm font-medium text-slate-900 group-hover:text-brand-600 transition-colors flex items-center gap-1.5">
                        {c.campaign_name || 'Untitled'}
                        <ChevronRight size={14} className="text-slate-300 group-hover:text-brand-400 transition-colors" />
                      </p>
                      <p className="text-xs text-slate-400 mt-0.5">
                        {c.targeting_type || '—'} targeting &middot; ID: {c.amazon_campaign_id?.slice(-8)}
                      </p>
                    </button>
                  </div>

                  {/* Type */}
                  <div className={`sm:col-span-1 ${!visibleColumns.has('type') ? 'hidden' : ''}`}>
                    <span className="text-xs text-slate-500 font-medium">
                      {(c.campaign_type || '').replace('SPONSORED_', 'SP ').replace('PRODUCTS', 'Products').replace('BRANDS', 'Brands').replace('DISPLAY', 'Display') || '—'}
                    </span>
                  </div>

                  {/* State */}
                  <div className={`sm:col-span-1 ${!visibleColumns.has('state') ? 'hidden' : ''}`}>
                    <StateBadge state={c.state} />
                  </div>

                  {/* Budget */}
                  <div className={`sm:col-span-1 text-right ${!visibleColumns.has('budget') ? 'hidden' : ''}`}>
                    <span className="text-sm font-medium text-slate-700">
                      {c.daily_budget ? `$${c.daily_budget.toFixed(2)}` : '—'}
                    </span>
                  </div>

                  {/* Spend */}
                  <div className={`sm:col-span-1 text-right ${!visibleColumns.has('spend') ? 'hidden' : ''}`}>
                    <span className="text-sm text-slate-600">
                      {c.spend != null ? `$${c.spend.toLocaleString()}` : '—'}
                    </span>
                  </div>

                  {/* Sales */}
                  <div className={`sm:col-span-1 text-right ${!visibleColumns.has('sales') ? 'hidden' : ''}`}>
                    <span className="text-sm text-slate-600">
                      {c.sales != null ? `$${c.sales.toLocaleString()}` : '—'}
                    </span>
                  </div>

                  {/* ACOS */}
                  <div className={`sm:col-span-1 text-right ${!visibleColumns.has('acos') ? 'hidden' : ''}`}>
                    <span className={`text-sm font-medium ${c.acos != null ? (c.acos > 30 ? 'text-red-600' : 'text-emerald-600') : 'text-slate-400'}`}>
                      {c.acos != null ? `${c.acos.toFixed(1)}%` : '—'}
                    </span>
                  </div>

                  {/* Actions */}
                  <div className={`sm:col-span-2 flex items-center justify-end gap-1 ${!visibleColumns.has('actions') ? 'hidden' : ''}`}>
                    <button
                      onClick={() => handleEditCampaign(c)}
                      className="p-1.5 rounded-lg hover:bg-slate-100 text-slate-400 hover:text-blue-600 transition-colors"
                      title="Edit campaign"
                    >
                      <Pencil size={14} />
                    </button>
                    <button
                      onClick={() => handleEditBudget(c)}
                      className="p-1.5 rounded-lg hover:bg-slate-100 text-slate-400 hover:text-blue-600 transition-colors"
                      title="Edit budget"
                    >
                      <DollarSign size={14} />
                    </button>
                    {(c.campaign_type || '').includes('PRODUCTS') && (c.targeting_type || '').toLowerCase() === 'manual' && (
                      <button
                        onClick={() => setAddCountryModal(c)}
                        className="p-1.5 rounded-lg hover:bg-slate-100 text-slate-400 hover:text-brand-600 transition-colors"
                        title="Add countries"
                      >
                        <Globe size={14} />
                      </button>
                    )}
                    {(c.state || '').toLowerCase() === 'enabled' ? (
                      <button
                        onClick={() => handleChangeState(c, 'paused')}
                        className="p-1.5 rounded-lg hover:bg-slate-100 text-slate-400 hover:text-amber-600 transition-colors"
                        title="Pause campaign"
                      >
                        <Pause size={14} />
                      </button>
                    ) : (
                      <button
                        onClick={() => handleChangeState(c, 'enabled')}
                        className="p-1.5 rounded-lg hover:bg-slate-100 text-slate-400 hover:text-emerald-600 transition-colors"
                        title="Enable campaign"
                      >
                        <Play size={14} />
                      </button>
                    )}
                    <button
                      onClick={() => handleDeleteCampaign(c)}
                      className="p-1.5 rounded-lg hover:bg-red-50 text-slate-400 hover:text-red-600 transition-colors"
                      title="Delete campaign"
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Pagination */}
          {!loading && totalPages > 1 && (
            <div className="flex items-center justify-between">
              <p className="text-sm text-slate-500">
                Showing {((page - 1) * PAGE_SIZE) + 1}–{Math.min(page * PAGE_SIZE, totalCampaigns)} of {totalCampaigns} campaigns
              </p>
              <div className="flex items-center gap-1">
                <button
                  onClick={() => goToPage(1)}
                  disabled={page <= 1}
                  className="px-2.5 py-1.5 text-xs font-medium rounded-lg border border-slate-200 text-slate-600 hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  First
                </button>
                <button
                  onClick={() => goToPage(page - 1)}
                  disabled={page <= 1}
                  className="px-2.5 py-1.5 text-xs font-medium rounded-lg border border-slate-200 text-slate-600 hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  Prev
                </button>
                {/* Page numbers */}
                {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
                  let p
                  if (totalPages <= 5) {
                    p = i + 1
                  } else if (page <= 3) {
                    p = i + 1
                  } else if (page >= totalPages - 2) {
                    p = totalPages - 4 + i
                  } else {
                    p = page - 2 + i
                  }
                  return (
                    <button
                      key={p}
                      onClick={() => goToPage(p)}
                      className={`px-3 py-1.5 text-xs font-medium rounded-lg border transition-colors ${
                        p === page
                          ? 'bg-brand-600 text-white border-brand-600'
                          : 'border-slate-200 text-slate-600 hover:bg-slate-50'
                      }`}
                    >
                      {p}
                    </button>
                  )
                })}
                <button
                  onClick={() => goToPage(page + 1)}
                  disabled={page >= totalPages}
                  className="px-2.5 py-1.5 text-xs font-medium rounded-lg border border-slate-200 text-slate-600 hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  Next
                </button>
                <button
                  onClick={() => goToPage(totalPages)}
                  disabled={page >= totalPages}
                  className="px-2.5 py-1.5 text-xs font-medium rounded-lg border border-slate-200 text-slate-600 hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  Last
                </button>
              </div>
            </div>
          )}
        </>
      )}

      {/* ── Ad Groups List ───────────────────────────────────────── */}
      {activeAccountId && view === 'ad-groups' && (
        <>
          {/* Campaign context header */}
          {selectedCampaign && (
            <div className="card px-5 py-4 bg-gradient-to-r from-slate-50 to-white">
              <div className="flex items-center justify-between flex-wrap gap-3">
                <div>
                  <p className="text-sm font-semibold text-slate-900">{selectedCampaign.campaign_name}</p>
                  <p className="text-xs text-slate-500 mt-0.5">
                    {selectedCampaign.campaign_type} &middot; {selectedCampaign.targeting_type} &middot; <StateBadge state={selectedCampaign.state} />
                  </p>
                </div>
                <div className="flex items-center gap-3">
                  <div className="text-right text-xs text-slate-500">
                    <p>Budget: <span className="font-medium text-slate-700">${selectedCampaign.daily_budget?.toFixed(2) || '—'}/day</span></p>
                  </div>
                  <button
                    onClick={() => handleEditCampaign(selectedCampaign)}
                    className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium text-slate-600 hover:text-brand-600 hover:bg-slate-100 rounded-lg transition-colors"
                    title="Campaign settings"
                  >
                    <Settings size={14} /> Campaign settings
                  </button>
                </div>
              </div>
            </div>
          )}

          {loading ? (
            <div className="flex items-center justify-center py-20">
              <Loader2 className="animate-spin text-slate-400" size={28} />
            </div>
          ) : adGroups.length === 0 ? (
            <EmptyState
              icon={Layers}
              title="No ad groups found"
              description="This campaign has no ad groups yet, or they haven't been synced."
            />
          ) : (
            <div className="card divide-y divide-slate-100 overflow-hidden">
              <div className="hidden sm:grid sm:grid-cols-12 gap-3 px-5 py-2.5 bg-slate-50 text-xs font-semibold text-slate-500 uppercase tracking-wider items-center">
                <div className="col-span-4 flex items-center gap-2">
                  <input type="checkbox" checked={adGroups.length > 0 && selectedAdGroupIds.size === adGroups.length} onChange={e => toggleSelectAllAdGroups(e.target.checked)} className="rounded border-slate-300 text-brand-600" />
                  <span>Ad Group</span>
                </div>
                <div className="col-span-1">State</div>
                <div className="col-span-1 text-right">Bid</div>
                <div className="col-span-1 text-right">Spend</div>
                <div className="col-span-1 text-right">Sales</div>
                <div className="col-span-1 text-right">ACOS</div>
                <div className="col-span-3 text-right flex items-center justify-end gap-2">
                  {selectedAdGroupIds.size > 0 && (
                    <div className="relative">
                      <button onClick={() => setBulkActionsOpen(!bulkActionsOpen)} className="text-xs font-medium text-brand-600 hover:text-brand-700">
                        Bulk ({selectedAdGroupIds.size})
                      </button>
                      {bulkActionsOpen && (
                        <>
                          <div className="fixed inset-0 z-10" onClick={() => setBulkActionsOpen(false)} />
                          <div className="absolute right-0 mt-1 z-20 w-40 py-1 bg-white rounded-lg shadow-lg border border-slate-200">
                            <button onClick={() => handleBulkAdGroupState('enabled')} className="w-full px-3 py-2 text-left text-sm hover:bg-slate-50 flex items-center gap-2"><Play size={14} /> Enable</button>
                            <button onClick={() => handleBulkAdGroupState('paused')} className="w-full px-3 py-2 text-left text-sm hover:bg-slate-50 flex items-center gap-2"><Pause size={14} /> Pause</button>
                          </div>
                        </>
                      )}
                    </div>
                  )}
                  <span>Actions</span>
                </div>
              </div>
              {adGroups.map(g => (
                <div
                  key={g.id}
                  className="grid grid-cols-1 sm:grid-cols-12 gap-2 sm:gap-3 px-5 py-3.5 hover:bg-slate-50/50 transition-colors items-center"
                >
                  <div className="sm:col-span-4 flex items-center gap-2">
                    <input type="checkbox" checked={selectedAdGroupIds.has(g.amazon_ad_group_id)} onChange={e => { setSelectedAdGroupIds(prev => { const n = new Set(prev); e.target.checked ? n.add(g.amazon_ad_group_id) : n.delete(g.amazon_ad_group_id); return n }) }} className="rounded border-slate-300 text-brand-600 shrink-0" />
                    <button onClick={() => openAdGroup(g)} className="text-left group flex-1 min-w-0">
                      <p className="text-sm font-medium text-slate-900 group-hover:text-brand-600 transition-colors flex items-center gap-1.5">
                        <Layers size={14} className="text-slate-400 shrink-0" />
                        {g.ad_group_name || 'Untitled'}
                        <ChevronRight size={14} className="text-slate-300 group-hover:text-brand-400" />
                      </p>
                      <p className="text-xs text-slate-400 mt-0.5 pl-5">
                        ID: {g.amazon_ad_group_id?.slice(-8)}
                      </p>
                    </button>
                  </div>
                  <div className="sm:col-span-1">
                    <StateBadge state={g.state} />
                  </div>
                  <div className="sm:col-span-1 text-right">
                    <span className="text-sm font-medium text-slate-700">
                      {g.default_bid ? `$${g.default_bid.toFixed(2)}` : '—'}
                    </span>
                  </div>
                  <div className="sm:col-span-1 text-right text-sm text-slate-600">{g.spend != null ? `$${g.spend.toFixed(2)}` : '—'}</div>
                  <div className="sm:col-span-1 text-right text-sm text-slate-600">{g.sales != null ? `$${g.sales.toFixed(2)}` : '—'}</div>
                  <div className="sm:col-span-1 text-right">
                    <span className={`text-sm font-medium ${g.acos != null ? (g.acos > 30 ? 'text-red-600' : 'text-emerald-600') : 'text-slate-400'}`}>
                      {g.acos != null ? `${g.acos.toFixed(1)}%` : '—'}
                    </span>
                  </div>
                  <div className="sm:col-span-3 flex items-center justify-end gap-1">
                    <button onClick={() => handleEditAdGroup(g)} className="p-1.5 rounded-lg hover:bg-slate-100 text-slate-400 hover:text-blue-600" title="Edit">
                      <Pencil size={14} />
                    </button>
                    <button onClick={() => handleDeleteAdGroup(g)} className="p-1.5 rounded-lg hover:bg-red-50 text-slate-400 hover:text-red-600" title="Delete">
                      <Trash2 size={14} />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {/* ── Targets & Ads (within Ad Group) ─────────────────────── */}
      {activeAccountId && view === 'targets' && selectedAdGroup && (
        <>
          {/* Ad Group context header */}
          <div className="card px-5 py-4 bg-gradient-to-r from-slate-50 to-white">
            <div className="flex items-center justify-between flex-wrap gap-3">
              <div>
                <p className="text-sm font-semibold text-slate-900">{selectedAdGroup.ad_group_name}</p>
                <p className="text-xs text-slate-500 mt-0.5">
                  Default bid: ${selectedAdGroup.default_bid?.toFixed(2) || '—'} &middot; <StateBadge state={selectedAdGroup.state} />
                </p>
              </div>
              <div className="flex items-center gap-3">
                {(selectedAdGroup.spend != null || selectedAdGroup.sales != null) && (
                  <div className="flex items-center gap-4 text-sm">
                    {selectedAdGroup.spend != null && (
                      <span className="text-slate-600">Spend: <span className="font-medium text-slate-900">${selectedAdGroup.spend.toFixed(2)}</span></span>
                    )}
                    {selectedAdGroup.sales != null && (
                      <span className="text-slate-600">Sales: <span className="font-medium text-slate-900">${selectedAdGroup.sales.toFixed(2)}</span></span>
                    )}
                    {selectedAdGroup.acos != null && (
                      <span className={`font-medium ${selectedAdGroup.acos > 30 ? 'text-red-600' : 'text-emerald-600'}`}>ACOS: {selectedAdGroup.acos.toFixed(1)}%</span>
                    )}
                  </div>
                )}
                <button
                  onClick={() => handleEditAdGroup(selectedAdGroup)}
                  className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium text-slate-600 hover:text-brand-600 hover:bg-slate-100 rounded-lg transition-colors"
                  title="Ad group settings"
                >
                  <Settings size={14} /> Ad group settings
                </button>
              </div>
            </div>
          </div>

          {/* Targets section */}
          <div>
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-semibold text-slate-900 flex items-center gap-2">
                <Target size={16} className="text-purple-500" />
                Targets / Keywords ({targets.length})
              </h2>
              <button onClick={handleAddTarget} className="btn-primary text-xs py-1.5 px-3">
                <Plus size={14} /> Add Target
              </button>
            </div>

            {loading ? (
              <div className="flex items-center justify-center py-12">
                <Loader2 className="animate-spin text-slate-400" size={24} />
              </div>
            ) : targets.length === 0 ? (
              <EmptyState
                icon={Target}
                title="No targets found"
                description="Add keywords or targets to this ad group to start advertising."
              />
            ) : (
              <div className="card divide-y divide-slate-100 overflow-hidden">
                <div className="hidden sm:grid sm:grid-cols-12 gap-3 px-5 py-2.5 bg-slate-50 text-xs font-semibold text-slate-500 uppercase tracking-wider items-center">
                  <div className="col-span-3 flex items-center gap-2">
                    <input type="checkbox" checked={targets.length > 0 && selectedTargetIds.size === targets.length} onChange={e => toggleSelectAllTargets(e.target.checked)} className="rounded border-slate-300 text-brand-600" />
                    <span>Target / Keyword</span>
                  </div>
                  <div className="col-span-1">Match</div>
                  <div className="col-span-1">State</div>
                  <div className="col-span-1 text-right">Bid</div>
                  <div className="col-span-1 text-right">Clicks</div>
                  <div className="col-span-1 text-right">Spend</div>
                  <div className="col-span-1 text-right">Sales</div>
                  <div className="col-span-1 text-right">ACOS</div>
                  <div className="col-span-1 text-right flex items-center justify-end gap-2">
                    {selectedTargetIds.size > 0 && (
                      <div className="relative">
                        <button onClick={() => setBulkActionsOpen(!bulkActionsOpen)} className="text-xs font-medium text-brand-600">Bulk ({selectedTargetIds.size})</button>
                        {bulkActionsOpen && (
                          <>
                            <div className="fixed inset-0 z-10" onClick={() => setBulkActionsOpen(false)} />
                            <div className="absolute right-0 mt-1 z-20 w-40 py-1 bg-white rounded-lg shadow-lg border border-slate-200">
                              <button onClick={() => handleBulkTargetState('enabled')} className="w-full px-3 py-2 text-left text-sm hover:bg-slate-50 flex items-center gap-2"><Play size={14} /> Enable</button>
                              <button onClick={() => handleBulkTargetState('paused')} className="w-full px-3 py-2 text-left text-sm hover:bg-slate-50 flex items-center gap-2"><Pause size={14} /> Pause</button>
                            </div>
                          </>
                        )}
                      </div>
                    )}
                    <span>Actions</span>
                  </div>
                </div>
                {targets.map(t => (
                  <div
                    key={t.id}
                    className="grid grid-cols-1 sm:grid-cols-12 gap-2 sm:gap-3 px-5 py-3 hover:bg-slate-50/50 transition-colors items-center"
                  >
                    <div className="sm:col-span-3 flex items-center gap-2">
                      <input type="checkbox" checked={selectedTargetIds.has(t.amazon_target_id)} onChange={e => { setSelectedTargetIds(prev => { const n = new Set(prev); e.target.checked ? n.add(t.amazon_target_id) : n.delete(t.amazon_target_id); return n }) }} className="rounded border-slate-300 text-brand-600 shrink-0" />
                      <div className="min-w-0">
                        <p className="text-sm font-medium text-slate-900 truncate">{t.expression_value || t.amazon_target_id}</p>
                        <p className="text-xs text-slate-400">{t.target_type || '—'}</p>
                      </div>
                    </div>
                    <div className="sm:col-span-1">
                      <span className="text-xs text-slate-500">{t.match_type || '—'}</span>
                    </div>
                    <div className="sm:col-span-1">
                      <StateBadge state={t.state} />
                    </div>
                    <div className="sm:col-span-1 text-right">
                      <span className="text-sm font-medium text-slate-700">{t.bid ? `$${t.bid.toFixed(2)}` : '—'}</span>
                    </div>
                    <div className="sm:col-span-1 text-right text-sm text-slate-600">{t.clicks ?? '—'}</div>
                    <div className="sm:col-span-1 text-right text-sm text-slate-600">{t.spend != null ? `$${t.spend.toFixed(2)}` : '—'}</div>
                    <div className="sm:col-span-1 text-right text-sm text-slate-600">{t.sales != null ? `$${t.sales.toFixed(2)}` : '—'}</div>
                    <div className="sm:col-span-1 text-right">
                      <span className={`text-sm font-medium ${t.acos != null ? (t.acos > 30 ? 'text-red-600' : 'text-emerald-600') : 'text-slate-400'}`}>
                        {t.acos != null ? `${t.acos.toFixed(1)}%` : '—'}
                      </span>
                    </div>
                    <div className="sm:col-span-1 flex items-center justify-end gap-1">
                      <button onClick={() => handleEditTarget(t)} className="p-1.5 rounded-lg hover:bg-slate-100 text-slate-400 hover:text-blue-600" title="Edit">
                        <Pencil size={14} />
                      </button>
                      <button onClick={() => handleDeleteTarget(t)} className="p-1.5 rounded-lg hover:bg-red-50 text-slate-400 hover:text-red-600" title="Delete">
                        <Trash2 size={14} />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Ads section */}
          <div>
            <h2 className="text-sm font-semibold text-slate-900 flex items-center gap-2 mb-3">
              <FileImage size={16} className="text-emerald-500" />
              Ads ({ads.length})
            </h2>
            {ads.length === 0 ? (
              <EmptyState
                icon={FileImage}
                title="No ads found"
                description="This ad group doesn't have any ads synced yet."
              />
            ) : (
              <div className="card divide-y divide-slate-100 overflow-hidden">
                <div className="hidden sm:grid sm:grid-cols-12 gap-3 px-5 py-2.5 bg-slate-50 text-xs font-semibold text-slate-500 uppercase tracking-wider items-center">
                  <div className="col-span-3 flex items-center gap-2">
                    <input type="checkbox" checked={ads.length > 0 && selectedAdIds.size === ads.length} onChange={e => toggleSelectAllAds(e.target.checked)} className="rounded border-slate-300 text-brand-600" />
                    <span>Ad / Creative</span>
                  </div>
                  <div className="col-span-2">ASIN / SKU</div>
                  <div className="col-span-1">Type</div>
                  <div className="col-span-1">State</div>
                  <div className="col-span-1 text-right">Spend</div>
                  <div className="col-span-1 text-right">Sales</div>
                  <div className="col-span-1 text-right">ACOS</div>
                  <div className="col-span-2 text-right flex items-center justify-end gap-2">
                    {selectedAdIds.size > 0 && (
                      <div className="relative">
                        <button onClick={() => setBulkActionsOpen(!bulkActionsOpen)} className="text-xs font-medium text-brand-600">Bulk ({selectedAdIds.size})</button>
                        {bulkActionsOpen && (
                          <>
                            <div className="fixed inset-0 z-10" onClick={() => setBulkActionsOpen(false)} />
                            <div className="absolute right-0 mt-1 z-20 w-40 py-1 bg-white rounded-lg shadow-lg border border-slate-200">
                              <button onClick={() => handleBulkAdState('enabled')} className="w-full px-3 py-2 text-left text-sm hover:bg-slate-50 flex items-center gap-2"><Play size={14} /> Enable</button>
                              <button onClick={() => handleBulkAdState('paused')} className="w-full px-3 py-2 text-left text-sm hover:bg-slate-50 flex items-center gap-2"><Pause size={14} /> Pause</button>
                            </div>
                          </>
                        )}
                      </div>
                    )}
                    <span>Actions</span>
                  </div>
                </div>
                {ads.map(a => (
                  <div
                    key={a.id}
                    className="grid grid-cols-1 sm:grid-cols-12 gap-2 sm:gap-3 px-5 py-3 hover:bg-slate-50/50 transition-colors items-center"
                  >
                    <div className="sm:col-span-3 flex items-center gap-2">
                      <input type="checkbox" checked={selectedAdIds.has(a.amazon_ad_id)} onChange={e => { setSelectedAdIds(prev => { const n = new Set(prev); e.target.checked ? n.add(a.amazon_ad_id) : n.delete(a.amazon_ad_id); return n }) }} className="rounded border-slate-300 text-brand-600 shrink-0" />
                      <div className="flex items-center gap-2 flex-1 min-w-0">
                        {(a.image_url || a.asin) && (
                          <div className="shrink-0 w-10 h-10 rounded-lg border border-slate-200 overflow-hidden bg-slate-50">
                            {a.image_url ? (
                              <img src={a.image_url} alt="" className="w-full h-full object-cover" />
                            ) : (
                              <a
                                href={a.product_url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="w-full h-full flex items-center justify-center text-slate-400 hover:text-brand-600"
                                title="View product"
                              >
                                <FileImage size={16} />
                              </a>
                            )}
                          </div>
                        )}
                        <div className="min-w-0">
                          <p className="text-sm font-medium text-slate-900 truncate">
                            {a.ad_name || a.asin || (a.amazon_ad_id ? `Ad #${a.amazon_ad_id.slice(-8)}` : '—')}
                          </p>
                          {a.amazon_ad_id && (
                            <p className="text-xs text-slate-400">ID: {a.amazon_ad_id.slice(-8)}</p>
                          )}
                          {a.product_url && (
                            <a href={a.product_url} target="_blank" rel="noopener noreferrer" className="text-xs text-brand-600 hover:underline truncate block">
                              View product
                            </a>
                          )}
                        </div>
                      </div>
                    </div>
                    <div className="sm:col-span-2">
                      <div className="flex flex-col gap-0.5">
                        {a.asin && <span className="text-xs text-slate-600"><Tag size={10} className="inline mr-1" />{a.asin}</span>}
                        {a.sku && <span className="text-xs text-slate-400"><Box size={10} className="inline mr-1" />{a.sku}</span>}
                        {!a.asin && !a.sku && <span className="text-xs text-slate-400">—</span>}
                      </div>
                    </div>
                    <div className="sm:col-span-1">
                      <span className="text-xs text-slate-500">{a.ad_type || '—'}</span>
                    </div>
                    <div className="sm:col-span-1">
                      <StateBadge state={a.state} />
                    </div>
                    <div className="sm:col-span-1 text-right text-sm text-slate-600">{a.spend != null ? `$${a.spend.toFixed(2)}` : '—'}</div>
                    <div className="sm:col-span-1 text-right text-sm text-slate-600">{a.sales != null ? `$${a.sales.toFixed(2)}` : '—'}</div>
                    <div className="sm:col-span-1 text-right">
                      <span className={`text-sm font-medium ${a.acos != null ? (a.acos > 30 ? 'text-red-600' : 'text-emerald-600') : 'text-slate-400'}`}>
                        {a.acos != null ? `${a.acos.toFixed(1)}%` : '—'}
                      </span>
                    </div>
                    <div className="sm:col-span-2 flex items-center justify-end gap-1">
                      <button onClick={() => handleEditAd(a)} className="p-1.5 rounded-lg hover:bg-slate-100 text-slate-400 hover:text-blue-600" title="Edit">
                        <Pencil size={14} />
                      </button>
                      <button onClick={() => handleDeleteAd(a)} className="p-1.5 rounded-lg hover:bg-red-50 text-slate-400 hover:text-red-600" title="Delete">
                        <Trash2 size={14} />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      )}

      {/* Modals */}
      {editModal && (
          <QuickEditModal
          title={editModal.title}
          fields={editModal.fields}
          onSave={editModal.onSave}
          onClose={() => setEditModal(null)}
          saving={modalSaving}
          skipApprovalOption={editModal.skipApprovalOption}
        />
      )}
      {addCountryModal && (
        <AddCountryModal
          campaign={addCountryModal}
          onSave={handleAddCountrySave}
          onClose={() => setAddCountryModal(null)}
          saving={modalSaving}
        />
      )}
      {singleshotModal && (
        <SingleshotModal
          products={products}
          onSave={handleSingleshotSave}
          onClose={() => setSingleshotModal(false)}
          saving={modalSaving}
        />
      )}
      {confirmModal && (
        <ConfirmModal
          title={confirmModal.title}
          message={confirmModal.message}
          onConfirm={confirmModal.onConfirm}
          onClose={() => setConfirmModal(null)}
          confirming={modalSaving}
          skipApprovalOption={confirmModal.skipApprovalOption}
          confirmLabel={confirmModal.confirmLabel}
          confirmingLabel={confirmModal.confirmingLabel}
        />
      )}
    </div>
  )
}
