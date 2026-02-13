import { useState, useEffect, useMemo, useRef } from 'react'
import {
  BarChart3,
  TrendingUp,
  TrendingDown,
  DollarSign,
  Eye,
  MousePointerClick,
  ShoppingCart,
  Target,
  Percent,
  ArrowUpRight,
  ArrowDownRight,
  Minus,
  Loader2,
  RefreshCw,
  Calendar,
  GitCompareArrows,
  ChevronUp,
  ChevronDown,
  Trophy,
  AlertTriangle,
  Layers,
  PieChart,
  ArrowRight,
  Clock,
  Download,
  Search,
  X,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
} from 'lucide-react'
import {
  AreaChart, Area, BarChart, Bar, LineChart, Line,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend,
  PieChart as RePieChart, Pie, Cell,
} from 'recharts'
import clsx from 'clsx'
import { reports } from '../lib/api'
import { useAccount } from '../lib/AccountContext'
import { useSync } from '../lib/SyncContext'
import { FileSearch, Database, AlertCircle, Trash2, History } from 'lucide-react'
import DateRangePicker from '../components/DateRangePicker'


// ── Constants ────────────────────────────────────────────────────────

const CHART_COLORS = [
  '#4f46e5', '#06b6d4', '#8b5cf6', '#f59e0b',
  '#10b981', '#ef4444', '#ec4899', '#6366f1',
]

const PIE_COLORS = ['#4f46e5', '#06b6d4', '#8b5cf6', '#f59e0b', '#10b981', '#94a3b8']


// ── Utility ──────────────────────────────────────────────────────────

function formatDateRange(startDate, endDate) {
  if (!startDate || !endDate) return ''
  const opts = { month: 'short', day: 'numeric', year: 'numeric' }
  // Parse as local date (not UTC) by using YYYY-MM-DDT00:00:00 without Z suffix
  const s = new Date(startDate + 'T12:00:00')
  const e = new Date(endDate + 'T12:00:00')
  if (isNaN(s) || isNaN(e)) return `${startDate} — ${endDate}`
  if (s.getFullYear() === e.getFullYear()) {
    return `${s.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })} — ${e.toLocaleDateString(undefined, opts)}`
  }
  return `${s.toLocaleDateString(undefined, opts)} — ${e.toLocaleDateString(undefined, opts)}`
}

/** Compute date range for a preset entirely on the client side. */
function getPresetDateRange(preset) {
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  // Use local date components — NOT toISOString() which converts to UTC
  // and can shift the date backwards for timezones ahead of UTC
  const iso = (d) => {
    const y = d.getFullYear()
    const m = String(d.getMonth() + 1).padStart(2, '0')
    const day = String(d.getDate()).padStart(2, '0')
    return `${y}-${m}-${day}`
  }

  if (preset === 'today') {
    return { start: iso(today), end: iso(today), label: 'Today' }
  }
  if (preset === 'yesterday') {
    const d = new Date(today); d.setDate(d.getDate() - 1)
    return { start: iso(d), end: iso(d), label: 'Yesterday' }
  }
  if (preset === 'this_week') {
    const day = today.getDay()
    const mon = new Date(today); mon.setDate(mon.getDate() - ((day + 6) % 7))
    return { start: iso(mon), end: iso(today), label: 'This Week' }
  }
  if (preset === 'last_week') {
    const day = today.getDay()
    const mon = new Date(today); mon.setDate(mon.getDate() - ((day + 6) % 7) - 7)
    const sun = new Date(mon); sun.setDate(sun.getDate() + 6)
    return { start: iso(mon), end: iso(sun), label: 'Last Week' }
  }
  if (preset === 'this_month') {
    const first = new Date(today.getFullYear(), today.getMonth(), 1)
    return { start: iso(first), end: iso(today), label: 'This Month' }
  }
  if (preset === 'last_month') {
    const firstThis = new Date(today.getFullYear(), today.getMonth(), 1)
    const lastPrev = new Date(firstThis); lastPrev.setDate(lastPrev.getDate() - 1)
    const firstPrev = new Date(lastPrev.getFullYear(), lastPrev.getMonth(), 1)
    return { start: iso(firstPrev), end: iso(lastPrev), label: 'Last Month' }
  }
  if (preset === 'last_7_days') {
    const from = new Date(today); from.setDate(from.getDate() - 6)
    return { start: iso(from), end: iso(today), label: 'Last 7 days' }
  }
  if (preset === 'last_30_days') {
    const from = new Date(today); from.setDate(from.getDate() - 29)
    return { start: iso(from), end: iso(today), label: 'Last 30 days' }
  }
  if (preset === 'year_to_date') {
    const first = new Date(today.getFullYear(), 0, 1)
    return { start: iso(first), end: iso(today), label: 'Year-to-date' }
  }
  // fallback
  const weekAgo = new Date(today); weekAgo.setDate(weekAgo.getDate() - 7)
  return { start: iso(weekAgo), end: iso(today), label: preset?.replace('_', ' ') || 'Custom' }
}

function fmt(val, type = 'number', currencyCode = 'USD') {
  if (val === null || val === undefined) return '—'
  if (type === 'currency') {
    try {
      return new Intl.NumberFormat(undefined, {
        style: 'currency',
        currency: currencyCode || 'USD',
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      }).format(Number(val))
    } catch {
      // Fallback if currencyCode is invalid
      return `${Number(val).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
    }
  }
  if (type === 'percent') return `${Number(val).toFixed(1)}%`
  if (type === 'decimal') return Number(val).toFixed(2)
  if (type === 'compact') {
    if (val >= 1_000_000) return `${(val / 1_000_000).toFixed(1)}M`
    if (val >= 1_000) return `${(val / 1_000).toFixed(1)}K`
    return val.toLocaleString()
  }
  return Number(val).toLocaleString()
}

/** Format a currency value with the given currency symbol (short helper for inline use) */
function fmtCurr(val, currencyCode = 'USD') {
  return fmt(val, 'currency', currencyCode)
}


const PAGE_SIZE = 20

// ── Pagination ──────────────────────────────────────────────────────

function Pagination({ page, totalPages, totalItems, pageSize, onPageChange }) {
  if (totalPages <= 1) return null
  const start = page * pageSize + 1
  const end = Math.min((page + 1) * pageSize, totalItems)

  // Build visible page numbers: first, last, and a window around current
  const pages = []
  const addPage = (n) => { if (n >= 0 && n < totalPages && !pages.includes(n)) pages.push(n) }
  addPage(0)
  for (let i = Math.max(1, page - 1); i <= Math.min(totalPages - 2, page + 1); i++) addPage(i)
  addPage(totalPages - 1)
  pages.sort((a, b) => a - b)

  // Insert ellipsis markers (-1)
  const withGaps = []
  for (let i = 0; i < pages.length; i++) {
    if (i > 0 && pages[i] - pages[i - 1] > 1) withGaps.push(-1)
    withGaps.push(pages[i])
  }

  return (
    <div className="px-5 py-3 border-t border-slate-100 flex items-center justify-between gap-4">
      <p className="text-xs text-slate-400 tabular-nums whitespace-nowrap">
        {start}–{end} of {totalItems.toLocaleString()}
      </p>
      <div className="flex items-center gap-1">
        <button
          onClick={() => onPageChange(0)}
          disabled={page === 0}
          className="p-1.5 rounded-md text-slate-400 hover:text-slate-600 hover:bg-slate-100 disabled:opacity-30 disabled:pointer-events-none transition-colors"
          title="First page"
        >
          <ChevronsLeft size={14} />
        </button>
        <button
          onClick={() => onPageChange(page - 1)}
          disabled={page === 0}
          className="p-1.5 rounded-md text-slate-400 hover:text-slate-600 hover:bg-slate-100 disabled:opacity-30 disabled:pointer-events-none transition-colors"
          title="Previous page"
        >
          <ChevronLeft size={14} />
        </button>
        {withGaps.map((p, i) =>
          p === -1 ? (
            <span key={`gap-${i}`} className="px-1 text-xs text-slate-300">…</span>
          ) : (
            <button
              key={p}
              onClick={() => onPageChange(p)}
              className={clsx(
                'min-w-[28px] h-7 rounded-md text-xs font-medium transition-colors',
                p === page
                  ? 'bg-brand-600 text-white shadow-sm'
                  : 'text-slate-500 hover:bg-slate-100 hover:text-slate-700'
              )}
            >
              {p + 1}
            </button>
          )
        )}
        <button
          onClick={() => onPageChange(page + 1)}
          disabled={page >= totalPages - 1}
          className="p-1.5 rounded-md text-slate-400 hover:text-slate-600 hover:bg-slate-100 disabled:opacity-30 disabled:pointer-events-none transition-colors"
          title="Next page"
        >
          <ChevronRight size={14} />
        </button>
        <button
          onClick={() => onPageChange(totalPages - 1)}
          disabled={page >= totalPages - 1}
          className="p-1.5 rounded-md text-slate-400 hover:text-slate-600 hover:bg-slate-100 disabled:opacity-30 disabled:pointer-events-none transition-colors"
          title="Last page"
        >
          <ChevronsRight size={14} />
        </button>
      </div>
    </div>
  )
}


// ── Sub-components ───────────────────────────────────────────────────

function DeltaBadge({ value, invert = false }) {
  if (value === null || value === undefined || value === 0) {
    return (
      <span className="inline-flex items-center gap-0.5 text-xs font-medium text-slate-400">
        <Minus size={12} /> 0%
      </span>
    )
  }
  const isPositive = invert ? value < 0 : value > 0
  return (
    <span className={clsx(
      'inline-flex items-center gap-0.5 text-xs font-semibold',
      isPositive ? 'text-emerald-600' : 'text-red-600'
    )}>
      {isPositive ? <ArrowUpRight size={13} /> : <ArrowDownRight size={13} />}
      {Math.abs(value).toFixed(1)}%
    </span>
  )
}

function KpiCard({ title, value, format = 'number', icon: Icon, color = 'brand', delta, invertDelta = false, subtitle, currencyCode }) {
  const colorMap = {
    brand: 'bg-brand-50 text-brand-600',
    blue: 'bg-blue-50 text-blue-600',
    emerald: 'bg-emerald-50 text-emerald-600',
    amber: 'bg-amber-50 text-amber-600',
    red: 'bg-red-50 text-red-600',
    purple: 'bg-purple-50 text-purple-600',
    cyan: 'bg-cyan-50 text-cyan-600',
    pink: 'bg-pink-50 text-pink-600',
  }
  return (
    <div className="card p-5 hover:shadow-md transition-shadow">
      <div className="flex items-start justify-between">
        <div className="flex-1 min-w-0">
          <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider truncate">{title}</p>
          <p className="mt-2 text-2xl font-bold text-slate-900 tracking-tight">{fmt(value, format, currencyCode)}</p>
          <div className="mt-1.5 flex items-center gap-2">
            {delta !== undefined && <DeltaBadge value={delta} invert={invertDelta} />}
            {subtitle && <span className="text-[11px] text-slate-400">{subtitle}</span>}
          </div>
        </div>
        {Icon && (
          <div className={clsx('flex items-center justify-center w-10 h-10 rounded-lg shrink-0', colorMap[color])}>
            <Icon size={20} />
          </div>
        )}
      </div>
    </div>
  )
}

function ChartCard({ title, subtitle, children, className }) {
  return (
    <div className={clsx('card p-5', className)}>
      <div className="mb-4">
        <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
        {subtitle && <p className="text-xs text-slate-400 mt-0.5">{subtitle}</p>}
      </div>
      {children}
    </div>
  )
}

function CustomTooltip({ active, payload, label, formatter }) {
  if (!active || !payload?.length) return null
  return (
    <div className="bg-slate-900 text-white text-xs rounded-lg px-3 py-2 shadow-xl">
      <p className="font-medium text-slate-300 mb-1">{label}</p>
      {payload.map((p, i) => (
        <p key={i} className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full" style={{ background: p.color }} />
          <span className="text-slate-400">{p.name}:</span>
          <span className="font-semibold">{formatter ? formatter(p.value, p.name) : p.value?.toLocaleString()}</span>
        </p>
      ))}
    </div>
  )
}


// ── Campaign Table ───────────────────────────────────────────────────

const TABLE_COLUMNS = [
  { key: 'campaign_name', label: 'Campaign', sortable: true, sticky: true },
  { key: 'state', label: 'Status', sortable: true },
  { key: 'spend', label: 'Spend', sortable: true, format: 'currency' },
  { key: 'sales', label: 'Sales', sortable: true, format: 'currency' },
  { key: 'acos', label: 'ACOS', sortable: true, format: 'percent' },
  { key: 'roas', label: 'ROAS', sortable: true, format: 'decimal' },
  { key: 'impressions', label: 'Impressions', sortable: true, format: 'compact' },
  { key: 'clicks', label: 'Clicks', sortable: true, format: 'compact' },
  { key: 'ctr', label: 'CTR', sortable: true, format: 'percent' },
  { key: 'orders', label: 'Orders', sortable: true },
  { key: 'cpc', label: 'CPC', sortable: true, format: 'currency' },
  { key: 'cvr', label: 'CVR', sortable: true, format: 'percent' },
]

function CampaignTable({ campaigns, currencyCode }) {
  const [sortKey, setSortKey] = useState('spend')
  const [sortDir, setSortDir] = useState('desc')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(0)

  const filtered = useMemo(() => {
    let list = [...(campaigns || [])]
    if (search.trim()) {
      const q = search.toLowerCase()
      list = list.filter(c => (c.campaign_name || '').toLowerCase().includes(q))
    }
    list.sort((a, b) => {
      const aVal = a[sortKey] ?? 0
      const bVal = b[sortKey] ?? 0
      if (typeof aVal === 'string') return sortDir === 'asc' ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal)
      return sortDir === 'asc' ? aVal - bVal : bVal - aVal
    })
    return list
  }, [campaigns, sortKey, sortDir, search])

  // Reset to page 0 when filters/sort change
  useEffect(() => { setPage(0) }, [search, sortKey, sortDir])

  const totalPages = Math.ceil(filtered.length / PAGE_SIZE)
  const paged = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)

  function toggleSort(key) {
    if (sortKey === key) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortKey(key)
      setSortDir('desc')
    }
  }

  const stateColors = {
    enabled: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20',
    active: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20',
    paused: 'bg-amber-50 text-amber-700 ring-amber-600/20',
    archived: 'bg-slate-100 text-slate-500 ring-slate-400/20',
  }

  if (!campaigns?.length) {
    return (
      <div className="card p-8 text-center">
        <Layers size={28} className="mx-auto text-slate-300 mb-2" />
        <p className="text-sm text-slate-500">No campaign data available</p>
        <p className="text-xs text-slate-400 mt-1">Generate a report to see campaign-level performance</p>
      </div>
    )
  }

  return (
    <div className="card overflow-hidden">
      {/* Table header */}
      <div className="px-5 py-4 border-b border-slate-100 flex items-center justify-between gap-4">
        <div>
          <h3 className="text-sm font-semibold text-slate-900">Campaign Performance</h3>
          <p className="text-xs text-slate-400 mt-0.5">{filtered.length} campaign{filtered.length !== 1 ? 's' : ''}</p>
        </div>
        <div className="relative w-64">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <input
            type="text"
            placeholder="Search campaigns..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="w-full pl-8 pr-8 py-2 text-sm bg-slate-50 border border-slate-200 rounded-lg placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500"
          />
          {search && (
            <button onClick={() => setSearch('')} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600">
              <X size={14} />
            </button>
          )}
        </div>
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-slate-50/50">
              {TABLE_COLUMNS.map(col => (
                <th
                  key={col.key}
                  className={clsx(
                    'px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500 whitespace-nowrap',
                    col.sortable && 'cursor-pointer hover:text-slate-700 select-none',
                    col.sticky && 'sticky left-0 bg-slate-50/80 backdrop-blur-sm z-10'
                  )}
                  onClick={() => col.sortable && toggleSort(col.key)}
                >
                  <span className="inline-flex items-center gap-1">
                    {col.label}
                    {sortKey === col.key && (
                      sortDir === 'asc' ? <ChevronUp size={12} /> : <ChevronDown size={12} />
                    )}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-50">
            {paged.map((c, idx) => (
              <tr key={c.campaign_id || idx} className="hover:bg-slate-50/50 transition-colors">
                {TABLE_COLUMNS.map(col => (
                  <td
                    key={col.key}
                    className={clsx(
                      'px-4 py-3 whitespace-nowrap',
                      col.sticky && 'sticky left-0 bg-white z-10'
                    )}
                  >
                    {col.key === 'campaign_name' ? (
                      <div className="max-w-[240px]">
                        <p className="text-sm font-medium text-slate-900 truncate">{c.campaign_name}</p>
                        {c.targeting_type && (
                          <p className="text-[10px] text-slate-400 uppercase tracking-wider mt-0.5">{c.targeting_type}</p>
                        )}
                      </div>
                    ) : col.key === 'state' ? (
                      <span className={clsx(
                        'inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium ring-1 ring-inset capitalize',
                        stateColors[(c.state || '').toLowerCase()] || stateColors.archived
                      )}>
                        {c.state || 'unknown'}
                      </span>
                    ) : (
                      <span className={clsx(
                        'font-medium tabular-nums',
                        col.key === 'acos' && c.acos > 30 ? 'text-red-600' :
                        col.key === 'acos' && c.acos > 0 ? 'text-emerald-600' :
                        'text-slate-700'
                      )}>
                        {fmt(c[col.key], col.format, currencyCode)}
                      </span>
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      <Pagination
        page={page}
        totalPages={totalPages}
        totalItems={filtered.length}
        pageSize={PAGE_SIZE}
        onPageChange={setPage}
      />
    </div>
  )
}


// ── Top / Worst Performers ───────────────────────────────────────────

function PerformerCard({ title, icon: Icon, iconColor, campaigns, metric, metricFormat, metricLabel, currencyCode }) {
  return (
    <div className="card overflow-hidden">
      <div className="px-5 py-4 border-b border-slate-100 flex items-center gap-3">
        <div className={clsx('flex items-center justify-center w-8 h-8 rounded-lg', iconColor)}>
          <Icon size={16} />
        </div>
        <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
      </div>
      {campaigns?.length ? (
        <div className="divide-y divide-slate-50">
          {campaigns.map((c, idx) => (
            <div key={c.campaign_id || idx} className="px-5 py-3 flex items-center gap-3">
              <span className={clsx(
                'flex items-center justify-center w-6 h-6 rounded-full text-[10px] font-bold shrink-0',
                idx === 0 ? 'bg-brand-100 text-brand-700' :
                idx === 1 ? 'bg-slate-100 text-slate-600' :
                'bg-slate-50 text-slate-400'
              )}>
                {idx + 1}
              </span>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-slate-800 truncate">{c.campaign_name}</p>
                <p className="text-[10px] text-slate-400 mt-0.5">
                  {c.targeting_type && <span className="uppercase">{c.targeting_type}</span>}
                  {c.targeting_type && c.state && ' · '}
                  {c.state && <span className="capitalize">{c.state}</span>}
                </p>
              </div>
              <div className="text-right shrink-0">
                <p className="text-sm font-bold text-slate-900 tabular-nums">{fmt(c[metric], metricFormat, currencyCode)}</p>
                {metricLabel && <p className="text-[10px] text-slate-400">{metricLabel}</p>}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="p-6 text-center text-xs text-slate-400">No data</div>
      )}
    </div>
  )
}


// ── Search Terms Section ─────────────────────────────────────────────

const ST_COLUMNS = [
  { key: 'search_term', label: 'Search Term', sortable: true, sticky: true, minW: 'min-w-[200px]' },
  { key: 'keyword', label: 'Keyword / Target', sortable: true, minW: 'min-w-[140px]' },
  { key: 'match_type', label: 'Match', sortable: true },
  { key: 'impressions', label: 'Impr.', sortable: true, align: 'right' },
  { key: 'clicks', label: 'Clicks', sortable: true, align: 'right' },
  { key: 'cost', label: 'Spend', sortable: true, align: 'right' },
  { key: 'sales', label: 'Sales', sortable: true, align: 'right' },
  { key: 'purchases', label: 'Orders', sortable: true, align: 'right' },
  { key: 'acos', label: 'ACOS', sortable: true, align: 'right' },
  { key: 'campaign_name', label: 'Campaign', sortable: true, minW: 'min-w-[160px]' },
]

function SearchTermsSection({ accountId, syncing, data, error, filter, onSync, onFilterChange, onDismissError, currencyCode = 'USD' }) {
  const [stTerms, setStTerms] = useState(null)
  const [stLoading, setStLoading] = useState(false)
  const [sortKey, setSortKey] = useState('cost')
  const [sortDir, setSortDir] = useState('desc')
  const [expanded, setExpanded] = useState(true)
  const [stSearch, setStSearch] = useState('')
  const [page, setPage] = useState(0)

  // Load actual search term rows on first expand or filter change
  useEffect(() => {
    if (expanded && accountId) loadTerms()
  }, [expanded, accountId, filter])

  async function loadTerms() {
    setStLoading(true)
    try {
      const opts = {
        sortBy: 'cost',
        limit: 5000,
      }
      if (filter === 'non_converting') opts.nonConvertingOnly = true
      if (filter === 'converting') opts.minClicks = 1
      const result = await reports.searchTerms(accountId, opts)
      let terms = result?.search_terms || []
      // Additional client-side filter for converting
      if (filter === 'converting') {
        terms = terms.filter(t => (t.purchases || 0) > 0)
      }
      setStTerms(terms)
    } catch (err) {
      console.error('Failed to load search terms:', err)
    } finally {
      setStLoading(false)
    }
  }

  const filtered = useMemo(() => {
    if (!stTerms) return []
    let list = [...stTerms]
    // Text search
    if (stSearch.trim()) {
      const q = stSearch.toLowerCase()
      list = list.filter(t =>
        (t.search_term || '').toLowerCase().includes(q) ||
        (t.keyword || '').toLowerCase().includes(q) ||
        (t.campaign_name || '').toLowerCase().includes(q)
      )
    }
    // Sort
    list.sort((a, b) => {
      const aVal = a[sortKey] ?? (typeof a[sortKey] === 'string' ? '' : 0)
      const bVal = b[sortKey] ?? (typeof b[sortKey] === 'string' ? '' : 0)
      if (typeof aVal === 'string' && typeof bVal === 'string') {
        return sortDir === 'asc' ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal)
      }
      return sortDir === 'asc' ? (aVal || 0) - (bVal || 0) : (bVal || 0) - (aVal || 0)
    })
    return list
  }, [stTerms, stSearch, sortKey, sortDir])

  // Reset page when filters change
  useEffect(() => { setPage(0) }, [stSearch, sortKey, sortDir, filter])

  const totalPages = Math.ceil(filtered.length / PAGE_SIZE)
  const paged = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)

  function toggleSort(key) {
    if (sortKey === key) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortKey(key)
      setSortDir(key === 'search_term' || key === 'keyword' || key === 'match_type' || key === 'campaign_name' ? 'asc' : 'desc')
    }
  }

  const hasData = data && (data.total > 0 || data.has_data)
  const totalRows = data?.total || data?.summary?.total_search_terms || 0

  return (
    <div className="card overflow-hidden">
      {/* Header */}
      <div className="px-5 py-4 border-b border-slate-100 flex items-center justify-between gap-4">
        <button
          onClick={() => setExpanded(!expanded)}
          className="flex items-center gap-3 text-left flex-1 min-w-0"
        >
          <div className="flex items-center justify-center w-9 h-9 rounded-lg bg-indigo-50 text-indigo-600 shrink-0">
            <FileSearch size={18} />
          </div>
          <div className="min-w-0">
            <h3 className="text-sm font-semibold text-slate-900">Search Term Performance</h3>
            <p className="text-xs text-slate-400 mt-0.5">
              {hasData
                ? <>{totalRows.toLocaleString()} search terms synced &middot; {data?.date_range || 'Last 30 days'}</>
                : 'Sync search term data from Amazon to analyze actual customer queries'
              }
            </p>
          </div>
          {expanded ? <ChevronUp size={16} className="text-slate-400 shrink-0" /> : <ChevronDown size={16} className="text-slate-400 shrink-0" />}
        </button>

        <button
          onClick={onSync}
          disabled={syncing}
          className={clsx(
            'flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium transition-all border shrink-0',
            syncing
              ? 'bg-slate-50 text-slate-400 border-slate-200 cursor-not-allowed'
              : 'bg-indigo-600 text-white border-indigo-600 hover:bg-indigo-700 shadow-sm'
          )}
        >
          {syncing ? (
            <><Loader2 size={15} className="animate-spin" /> Syncing...</>
          ) : (
            <><RefreshCw size={15} /> Sync Search Terms</>
          )}
        </button>
      </div>

      {/* Error / status message */}
      {error && (
        <div className="px-5 py-3 bg-amber-50 border-b border-amber-100 flex items-center gap-3">
          <AlertCircle size={14} className="text-amber-500 shrink-0" />
          <p className="text-xs text-amber-700 flex-1">{error}</p>
          <button onClick={onDismissError} className="text-amber-400 hover:text-amber-600 text-xs font-medium">Dismiss</button>
        </div>
      )}

      {/* Expanded content */}
      {expanded && (
        <div>
          {/* No data state */}
          {!hasData && !stLoading && (
            <div className="px-5 py-12 text-center">
              <Database size={36} className="mx-auto text-slate-200 mb-3" />
              <p className="text-sm font-medium text-slate-600">No search term data synced yet</p>
              <p className="text-xs text-slate-400 mt-1 max-w-md mx-auto">
                Click "Sync Search Terms" to pull the last 30 days of search term reports from Amazon Ads.
                This shows the actual customer queries that triggered your ads.
              </p>
              <button
                onClick={onSync}
                disabled={syncing}
                className="mt-4 inline-flex items-center gap-2 px-5 py-2.5 rounded-lg text-sm font-medium bg-indigo-600 text-white hover:bg-indigo-700 transition-colors shadow-sm disabled:opacity-50"
              >
                {syncing ? <><Loader2 size={15} className="animate-spin" /> Syncing...</> : <><RefreshCw size={15} /> Sync Now</>}
              </button>
            </div>
          )}

          {/* Data present */}
          {(hasData || stLoading) && (
            <>
              {/* Filters bar */}
              <div className="px-5 py-3 bg-slate-50/50 border-b border-slate-100 flex flex-col sm:flex-row sm:items-center gap-3">
                <div className="flex flex-wrap gap-1.5">
                  {[
                    { key: 'all', label: 'All Terms' },
                    { key: 'converting', label: 'Converting' },
                    { key: 'non_converting', label: 'Non-Converting' },
                  ].map(f => (
                    <button
                      key={f.key}
                      onClick={() => onFilterChange(f.key)}
                      className={clsx(
                        'px-3 py-1.5 rounded-md text-xs font-medium transition-all',
                        filter === f.key
                          ? 'bg-indigo-600 text-white shadow-sm'
                          : 'bg-white text-slate-600 border border-slate-200 hover:bg-slate-100'
                      )}
                    >
                      {f.label}
                    </button>
                  ))}
                </div>
                <div className="flex items-center gap-2 flex-1">
                  <div className="relative flex-1 max-w-xs">
                    <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input
                      type="text"
                      placeholder="Search terms, keywords, campaigns..."
                      value={stSearch}
                      onChange={e => setStSearch(e.target.value)}
                      className="w-full pl-8 pr-3 py-1.5 text-xs bg-white border border-slate-200 rounded-md placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                    />
                    {stSearch && (
                      <button onClick={() => setStSearch('')} className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600">
                        <X size={12} />
                      </button>
                    )}
                  </div>
                  {filtered.length > 0 && (
                    <span className="text-[11px] text-slate-400 whitespace-nowrap tabular-nums">
                      {filtered.length.toLocaleString()} result{filtered.length !== 1 ? 's' : ''}
                    </span>
                  )}
                </div>
              </div>

              {/* Quick summary cards */}
              {data && (
                <div className="px-5 py-3 grid grid-cols-2 sm:grid-cols-4 gap-3">
                  <div className="bg-slate-50 rounded-lg p-3">
                    <p className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider">Total Terms</p>
                    <p className="text-lg font-bold text-slate-900 mt-1">{totalRows.toLocaleString()}</p>
                  </div>
                  <div className="bg-emerald-50 rounded-lg p-3">
                    <p className="text-[10px] font-semibold text-emerald-500 uppercase tracking-wider">Converting</p>
                    <p className="text-lg font-bold text-emerald-700 mt-1">{(data?.summary?.with_sales || data?.top_by_sales?.length || 0).toLocaleString()}</p>
                  </div>
                  <div className="bg-red-50 rounded-lg p-3">
                    <p className="text-[10px] font-semibold text-red-500 uppercase tracking-wider">Non-Converting</p>
                    <p className="text-lg font-bold text-red-700 mt-1">{(data?.summary?.non_converting || data?.top_non_converting?.length || 0).toLocaleString()}</p>
                  </div>
                  <div className="bg-amber-50 rounded-lg p-3">
                    <p className="text-[10px] font-semibold text-amber-500 uppercase tracking-wider">High ACOS (&gt;50%)</p>
                    <p className="text-lg font-bold text-amber-700 mt-1">{(data?.summary?.high_acos_count || data?.top_high_acos?.length || 0).toLocaleString()}</p>
                  </div>
                </div>
              )}

              {/* Table */}
              {stLoading ? (
                <div className="py-12 text-center">
                  <Loader2 size={24} className="animate-spin text-indigo-500 mx-auto" />
                  <p className="text-xs text-slate-400 mt-2">Loading search terms...</p>
                </div>
              ) : filtered.length > 0 ? (
                <>
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="bg-slate-50/50">
                          {ST_COLUMNS.map(col => (
                            <th
                              key={col.key}
                              className={clsx(
                                'px-3 py-2.5 text-[11px] font-semibold uppercase tracking-wider text-slate-500 whitespace-nowrap',
                                col.align === 'right' ? 'text-right' : 'text-left',
                                col.sticky && 'sticky left-0 bg-slate-50/80 backdrop-blur-sm z-10',
                                col.minW,
                                col.sortable && 'cursor-pointer hover:text-slate-700 select-none',
                              )}
                              onClick={() => col.sortable && toggleSort(col.key)}
                            >
                              <span className={clsx('inline-flex items-center gap-1', col.align === 'right' && 'justify-end')}>
                                {col.label}
                                {sortKey === col.key && (
                                  sortDir === 'asc' ? <ChevronUp size={11} /> : <ChevronDown size={11} />
                                )}
                              </span>
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-50">
                        {paged.map((t, idx) => (
                          <tr key={`${t.search_term}-${t.keyword}-${idx}`} className="hover:bg-slate-50/50 transition-colors">
                            <td className="px-3 py-2.5 sticky left-0 bg-white z-10">
                              <p className="text-sm font-medium text-slate-800 truncate max-w-[220px]" title={t.search_term}>{t.search_term}</p>
                            </td>
                            <td className="px-3 py-2.5">
                              <p className="text-xs text-slate-600 truncate max-w-[140px]" title={t.keyword}>{t.keyword || '—'}</p>
                            </td>
                            <td className="px-3 py-2.5">
                              <span className={clsx(
                                'inline-flex px-1.5 py-0.5 rounded text-[10px] font-medium uppercase',
                                t.match_type === 'EXACT' ? 'bg-blue-50 text-blue-600' :
                                t.match_type === 'PHRASE' ? 'bg-purple-50 text-purple-600' :
                                t.match_type === 'BROAD' ? 'bg-amber-50 text-amber-600' :
                                'bg-slate-100 text-slate-500'
                              )}>
                                {t.match_type || '—'}
                              </span>
                            </td>
                            <td className="px-3 py-2.5 text-right text-xs tabular-nums text-slate-600">{(t.impressions || 0).toLocaleString()}</td>
                            <td className="px-3 py-2.5 text-right text-xs tabular-nums font-medium text-slate-700">{(t.clicks || 0).toLocaleString()}</td>
                            <td className="px-3 py-2.5 text-right text-xs tabular-nums font-medium text-slate-700">{fmtCurr(t.cost || 0, currencyCode)}</td>
                            <td className="px-3 py-2.5 text-right text-xs tabular-nums font-medium text-emerald-600">{t.sales > 0 ? fmtCurr(t.sales, currencyCode) : '—'}</td>
                            <td className="px-3 py-2.5 text-right text-xs tabular-nums text-slate-700">{t.purchases > 0 ? t.purchases : '—'}</td>
                            <td className="px-3 py-2.5 text-right">
                              {t.purchases > 0 && t.acos != null ? (
                                <span className={clsx('text-xs tabular-nums font-medium', t.acos > 30 ? 'text-red-600' : 'text-emerald-600')}>
                                  {t.acos.toFixed(1)}%
                                </span>
                              ) : t.clicks > 0 ? (
                                <span className="text-xs text-red-400">No conv.</span>
                              ) : (
                                <span className="text-xs text-slate-300">—</span>
                              )}
                            </td>
                            <td className="px-3 py-2.5">
                              <p className="text-[11px] text-slate-500 truncate max-w-[160px]" title={t.campaign_name}>{t.campaign_name || '—'}</p>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <Pagination
                    page={page}
                    totalPages={totalPages}
                    totalItems={filtered.length}
                    pageSize={PAGE_SIZE}
                    onPageChange={setPage}
                  />
                </>
              ) : (
                <div className="py-8 text-center text-xs text-slate-400">
                  {stSearch ? 'No search terms match your search' : 'No search terms found with current filters'}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}


// ── Main Reports Page ────────────────────────────────────────────────

export default function Reports() {
  const { activeAccount, activeAccountId } = useAccount()

  // UI state
  const [dateRange, setDateRange] = useState({
    preset: 'this_month',
    start: getPresetDateRange('this_month').start,
    end: getPresetDateRange('this_month').end,
    label: 'This Month',
  })
  const [pickerOpen, setPickerOpen] = useState(false)
  const pickerRef = useRef(null)

  useEffect(() => {
    if (!pickerOpen) return
    function handleClickOutside(e) {
      if (pickerRef.current && !pickerRef.current.contains(e.target)) {
        setPickerOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [pickerOpen])

  const [compare, setCompare] = useState(false)
  const [loading, setLoading] = useState(true)
  const [generating, setGenerating] = useState(false)

  // Data
  const [reportData, setReportData] = useState(null)
  const [summaryData, setSummaryData] = useState(null)
  const [trendData, setTrendData] = useState([])
  const [error, setError] = useState(null)

  // Currency (driven by backend, based on active account's marketplace)
  const [currencyCode, setCurrencyCode] = useState('USD')

  // Search terms (sync state from SyncContext for persistence across navigation)
  const {
    reportSearchTermsSync,
    startReportSearchTermsSync,
    dismissReportSearchTermsSync,
    reportGenerateSync,
    startReportGenerateSync,
  } = useSync()
  const stSyncing = reportSearchTermsSync.status === 'running'
  const stError = reportSearchTermsSync.error
  const [stData, setStData] = useState(null)
  const [stFilter, setStFilter] = useState('all') // all | converting | non_converting

  const [reportHistory, setReportHistory] = useState([])
  const [reportHistoryLoading, setReportHistoryLoading] = useState(false)
  const [reportHistoryExpanded, setReportHistoryExpanded] = useState(false)
  const [deletingReportId, setDeletingReportId] = useState(null)

  const isInitialMount = useRef(true)
  const mountedRef = useRef(true)
  useEffect(() => () => { mountedRef.current = false }, [])

  // Load initial data on mount / account change
  // Use activeAccount?.id so we refetch when switching between profiles under the same credential
  useEffect(() => {
    isInitialMount.current = true
    setReportData(null)
    setSummaryData(null)
    setTrendData([])
    setStData(null)
    loadInitialData(dateRange)
    loadSearchTerms()
  }, [activeAccountId, activeAccount?.id])

  // Re-fetch when date range changes — shows cached data for the selected range
  useEffect(() => {
    if (isInitialMount.current) {
      isInitialMount.current = false
      return
    }
    refreshForPreset(dateRange)
  }, [dateRange.preset, dateRange.start, dateRange.end])

  function reportOpts() {
    const useCustom = dateRange.preset === 'custom' || (dateRange.start && dateRange.end && dateRange.preset !== 'this_month' && !['today', 'yesterday', 'this_week', 'last_week', 'last_7_days', 'last_30_days', 'this_month', 'last_month', 'year_to_date'].includes(dateRange.preset))
    if (useCustom && dateRange.start && dateRange.end) {
      return { startDate: dateRange.start, endDate: dateRange.end }
    }
    return { preset: dateRange.preset || 'this_month' }
  }

  async function loadInitialData(range) {
    setLoading(true)
    setError(null)
    const opts = (range?.preset === 'custom' && range?.start && range?.end)
      ? { startDate: range.start, endDate: range.end }
      : { preset: range?.preset || 'this_month' }
    try {
      const [summary, trends] = await Promise.allSettled([
        reports.summary(activeAccountId, opts),
        reports.trends(activeAccountId, 30, opts),
      ])
      if (summary.status === 'fulfilled') {
        setSummaryData(summary.value)
        if (summary.value?.currency_code) setCurrencyCode(summary.value.currency_code)
      }
      if (trends.status === 'fulfilled') {
        // trends API now returns { source, data }
        const tVal = trends.value
        setTrendData(tVal?.data || tVal || [])
      }
    } catch (err) {
      console.error('Load failed:', err)
    } finally {
      setLoading(false)
    }
  }

  async function refreshForPreset(range) {
    setReportData(null)
    const opts = range?.preset === 'custom' && range?.start && range?.end
      ? { startDate: range.start, endDate: range.end }
      : { preset: range?.preset || 'this_month' }
    try {
      const [summary, trends] = await Promise.allSettled([
        reports.summary(activeAccountId, opts),
        reports.trends(activeAccountId, 30, opts),
      ])
      if (summary.status === 'fulfilled') {
        setSummaryData(summary.value)
        if (summary.value?.currency_code) setCurrencyCode(summary.value.currency_code)
      }
      if (trends.status === 'fulfilled') {
        const tVal = trends.value
        setTrendData(tVal?.data || tVal || [])
      }
    } catch (err) {
      console.error('Preset refresh failed:', err)
    }
  }

  async function loadSearchTerms() {
    try {
      const data = await reports.searchTermsSummary(activeAccountId)
      setStData(data)
    } catch (err) { /* ignore — may not have data yet */ }
  }

  async function loadReportHistory() {
    setReportHistoryLoading(true)
    try {
      const data = await reports.history(activeAccountId, 20)
      setReportHistory(data || [])
    } catch { setReportHistory([]) }
    finally { setReportHistoryLoading(false) }
  }

  async function deleteReport(id) {
    setDeletingReportId(id)
    try {
      await reports.delete(id, activeAccountId)
      setReportHistory(prev => prev.filter(r => r.id !== id))
    } catch (err) { setError(err.message) }
    finally { setDeletingReportId(null) }
  }

  function syncSearchTerms() {
    startReportSearchTermsSync(activeAccountId, reportSearchTermsSync.pendingReportId)
  }

  // Refresh search terms when sync completes (persists across navigation)
  useEffect(() => {
    if (reportSearchTermsSync.status === 'completed' && reportSearchTermsSync.credentialId === activeAccountId) {
      loadSearchTerms()
    }
  }, [reportSearchTermsSync.status, reportSearchTermsSync.credentialId, activeAccountId])

  async function generateReport() {
    setGenerating(true)
    setError(null)
    const opts = (dateRange.preset === 'custom' || !dateRange.preset) && dateRange.start && dateRange.end
      ? { startDate: dateRange.start, endDate: dateRange.end, compare }
      : { preset: dateRange.preset || 'this_month', compare }
    try {
      const data = await reports.generate(activeAccountId, opts)
      setReportData(data)
      if (data?.currency_code) setCurrencyCode(data.currency_code)
      // Use daily_trend from the generate response if available
      if (data?.daily_trend?.length) {
        setTrendData(data.daily_trend)
      }
      // If report still pending at Amazon, start background polling (persists across navigation)
      if (data?.report_pending) {
        startReportGenerateSync(activeAccountId, opts, (updatedData) => {
          if (!mountedRef.current) return
          setReportData(updatedData)
          if (updatedData?.currency_code) setCurrencyCode(updatedData.currency_code)
          if (updatedData?.daily_trend?.length) setTrendData(updatedData.daily_trend)
        })
      }
    } catch (err) {
      setError(err.message || 'Failed to generate report')
    } finally {
      setGenerating(false)
    }
  }

  // Derived data — prefer generated report, fallback to summary
  const activeSummary = reportData?.summary || summaryData?.summary || {}
  const activeCampaigns = reportData?.campaigns || summaryData?.campaigns || []
  const topPerformers = reportData?.top_performers || summaryData?.top_performers || []
  const worstPerformers = reportData?.worst_performers || summaryData?.worst_performers || []
  const deltas = reportData?.comparison?.deltas || {}
  const compPeriod = reportData?.comparison?.period || null

  const period = reportData?.period || {
    start_date: dateRange.start,
    end_date: dateRange.end,
    label: dateRange.label,
    preset: dateRange.preset,
  }

  const hasComparison = compare && !!reportData?.comparison
  const hasData = activeSummary && Object.keys(activeSummary).length > 0
  const isEmptyApiResult = reportData?.report_source === 'amazon_ads_api' && (!reportData?.campaigns?.length)
  const isCacheData = reportData?.report_source === 'campaign_cache'

  // Chart data for campaign breakdown
  const topCampaignChart = useMemo(() => {
    return [...activeCampaigns]
      .sort((a, b) => (b.spend || 0) - (a.spend || 0))
      .slice(0, 8)
      .map(c => ({
        name: (c.campaign_name || 'Unknown').length > 20
          ? (c.campaign_name || 'Unknown').substring(0, 20) + '...'
          : (c.campaign_name || 'Unknown'),
        spend: c.spend || 0,
        sales: c.sales || 0,
      }))
  }, [activeCampaigns])

  // State breakdown pie data
  const stateData = useMemo(() => {
    if (!reportData?.state_breakdown && !summaryData) return []
    const breakdown = reportData?.state_breakdown || {}
    if (Object.keys(breakdown).length > 0) {
      return Object.entries(breakdown).map(([k, v]) => ({
        name: k.charAt(0).toUpperCase() + k.slice(1),
        value: v.count || 0,
        spend: v.spend || 0,
      }))
    }
    // Fallback to computed from campaigns
    const counts = {}
    activeCampaigns.forEach(c => {
      const s = (c.state || 'unknown').toLowerCase()
      counts[s] = (counts[s] || 0) + 1
    })
    return Object.entries(counts).map(([k, v]) => ({
      name: k.charAt(0).toUpperCase() + k.slice(1),
      value: v,
    }))
  }, [reportData, summaryData, activeCampaigns])

  // ── Loading state ──────────────────────────────────────────────────
  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="text-center">
          <Loader2 size={32} className="animate-spin text-brand-500 mx-auto" />
          <p className="mt-3 text-sm text-slate-500">Loading report data...</p>
        </div>
      </div>
    )
  }

  // ── Render ─────────────────────────────────────────────────────────
  return (
    <div className="space-y-6">
      {/* ── Page Header ─────────────────────────────────────────────── */}
      <div className="flex flex-col sm:flex-row sm:items-end gap-4 justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 tracking-tight">Reports</h1>
          <p className="mt-1 text-sm text-slate-500">
            {activeAccount
              ? <>Analyzing <span className="font-medium text-slate-700">{activeAccount.account_name || activeAccount.name}</span> &middot; {activeAccount.marketplace || activeAccount.region?.toUpperCase()}</>
              : 'Comprehensive campaign performance analytics'}
          </p>
        </div>
        {reportData && (
          <div className="flex items-center gap-2 text-xs text-slate-400">
            <Clock size={12} />
            <span>Generated {new Date(reportData.generated_at).toLocaleString()}</span>
            {reportData.report_source === 'amazon_ads_api' && (
              <span className="badge-blue">Live Data</span>
            )}
            {reportData.report_source === 'database' && (
              <span className="badge-gray">Cached Data</span>
            )}
          </div>
        )}
      </div>

      {/* ── Controls Bar ────────────────────────────────────────────── */}
      <div className="card p-4">
        <div className="flex flex-col lg:flex-row lg:items-center gap-4">
          {/* Date Range Picker */}
          <div className="flex-1 relative">
            <label className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider mb-2 block">Date Range</label>
            <div className="flex items-center gap-2 relative" ref={pickerRef}>
              <button
                type="button"
                onClick={() => setPickerOpen(o => !o)}
                className={clsx(
                  'flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium transition-all border',
                  pickerOpen
                    ? 'bg-brand-50 border-brand-300 ring-1 ring-brand-200'
                    : 'bg-white border-slate-200 hover:bg-slate-50'
                )}
              >
                <Calendar size={16} className="text-brand-500" />
                <span className="text-slate-700">{dateRange.label || 'Select date range'}</span>
                <span className="text-slate-500 text-xs">
                  {formatDateRange(dateRange.start, dateRange.end)}
                </span>
              </button>
              {pickerOpen && (
                <div className="absolute left-0 top-full mt-1 z-50">
                  <DateRangePicker
                    key={`${dateRange.start}-${dateRange.end}`}
                    value={dateRange}
                    onChange={(v) => {
                      setDateRange(v)
                      setPickerOpen(false)
                    }}
                    onClose={() => setPickerOpen(false)}
                  />
                </div>
              )}
            </div>
          </div>

          {/* Comparison Toggle */}
          <div className="flex items-center gap-3">
            <button
              onClick={() => setCompare(!compare)}
              className={clsx(
                'flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium transition-all border',
                compare
                  ? 'bg-purple-50 text-purple-700 border-purple-200 ring-1 ring-purple-200'
                  : 'bg-white text-slate-600 border-slate-200 hover:bg-slate-50'
              )}
            >
              <GitCompareArrows size={16} />
              Compare
            </button>

            {/* Generate Button */}
            <button
              onClick={generateReport}
              disabled={generating}
              className="btn-primary whitespace-nowrap"
            >
              {generating ? (
                <><Loader2 size={16} className="animate-spin" /> Fetching from Amazon...</>
              ) : (
                <><BarChart3 size={16} /> Generate Report</>
              )}
            </button>
          </div>
        </div>

        {/* Period info — always show resolved date range */}
        <div className="mt-3 pt-3 border-t border-slate-100 flex items-center gap-4 text-xs">
          {period ? (
            <span className="text-slate-500 flex items-center gap-1.5 font-medium">
              <Calendar size={13} className="text-brand-500" />
              {period.label || (period.preset || 'custom').replace('_', ' ').replace(/\b\w/g, l => l.toUpperCase())}:
              <span className="text-slate-700">{formatDateRange(period.start_date, period.end_date)}</span>
            </span>
          ) : (
            <span className="text-slate-400 flex items-center gap-1.5">
              <Calendar size={13} />
              Select a date range and generate a report
            </span>
          )}
          {hasComparison && compPeriod && (
            <span className="text-purple-500 flex items-center gap-1.5 font-medium">
              <GitCompareArrows size={13} />
              vs <span className="text-purple-700">{formatDateRange(compPeriod.start_date, compPeriod.end_date)}</span>
            </span>
          )}
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="card bg-red-50 border-red-200 p-4 flex items-center gap-3">
          <AlertTriangle size={16} className="text-red-500 shrink-0" />
          <p className="text-sm text-red-700 flex-1">{error}</p>
          <button onClick={() => setError(null)} className="text-red-400 hover:text-red-600 text-xs font-medium">Dismiss</button>
        </div>
      )}

      {/* Report still processing at Amazon */}
      {reportPending && (
        <div className="card bg-amber-50 border-amber-200 p-4 flex items-center gap-3">
          <Loader2 size={16} className="text-amber-500 shrink-0 animate-spin" />
          <div className="flex-1">
            <p className="text-sm font-medium text-amber-800">Report is still processing at Amazon</p>
            <p className="text-xs text-amber-600 mt-0.5">
              Amazon Ads reports can take 2-5 minutes to generate. Showing cached data in the meantime.
              Click "Generate Report" again to check if it's ready.
            </p>
          </div>
          <button
            onClick={generateReport}
            disabled={generating}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-amber-600 text-white hover:bg-amber-700 transition-colors shadow-sm disabled:opacity-50 shrink-0"
          >
            {generating ? (
              <><Loader2 size={13} className="animate-spin" /> Checking...</>
            ) : (
              <><RefreshCw size={13} /> Retry</>
            )}
          </button>
        </div>
      )}

      {/* No data for this date range */}
      {isEmptyApiResult && (
        <div className="card bg-blue-50 border-blue-200 p-4 flex items-center gap-3">
          <Calendar size={16} className="text-blue-500 shrink-0" />
          <p className="text-sm text-blue-700 flex-1">
            No campaign performance data available for <span className="font-semibold">{period?.label || 'this period'}</span>
            {period && <> ({formatDateRange(period.start_date, period.end_date)})</>}.
            {' '}Amazon Ads data may take up to 24 hours to become available.
          </p>
        </div>
      )}

      {/* ── KPI Cards ───────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-4 gap-4">
        <KpiCard
          title="Total Spend"
          value={activeSummary.spend}
          format="currency"
          icon={DollarSign}
          color="blue"
          delta={hasComparison ? deltas.spend : undefined}
          subtitle={hasComparison ? 'vs prev period' : undefined}
          currencyCode={currencyCode}
        />
        <KpiCard
          title="Total Sales"
          value={activeSummary.sales}
          format="currency"
          icon={ShoppingCart}
          color="emerald"
          delta={hasComparison ? deltas.sales : undefined}
          subtitle={hasComparison ? 'vs prev period' : undefined}
          currencyCode={currencyCode}
        />
        <KpiCard
          title="ACOS"
          value={activeSummary.acos}
          format="percent"
          icon={Target}
          color={activeSummary.acos > 30 ? 'red' : 'emerald'}
          delta={hasComparison ? deltas.acos : undefined}
          invertDelta={true}
          subtitle={hasComparison ? 'lower is better' : undefined}
        />
        <KpiCard
          title="ROAS"
          value={activeSummary.roas}
          format="decimal"
          icon={TrendingUp}
          color="brand"
          delta={hasComparison ? deltas.roas : undefined}
          subtitle={hasComparison ? 'vs prev period' : undefined}
        />
        <KpiCard
          title="Impressions"
          value={activeSummary.impressions}
          format="compact"
          icon={Eye}
          color="cyan"
          delta={hasComparison ? deltas.impressions : undefined}
          subtitle={hasComparison ? 'vs prev period' : undefined}
        />
        <KpiCard
          title="Clicks"
          value={activeSummary.clicks}
          format="compact"
          icon={MousePointerClick}
          color="purple"
          delta={hasComparison ? deltas.clicks : undefined}
          subtitle={hasComparison ? 'vs prev period' : undefined}
        />
        <KpiCard
          title="CTR"
          value={activeSummary.ctr}
          format="percent"
          icon={Percent}
          color="amber"
          delta={hasComparison ? deltas.ctr : undefined}
          subtitle={hasComparison ? 'vs prev period' : undefined}
        />
        <KpiCard
          title="Orders"
          value={activeSummary.orders}
          format="number"
          icon={ShoppingCart}
          color="pink"
          delta={hasComparison ? deltas.orders : undefined}
          subtitle={hasComparison ? 'vs prev period' : undefined}
        />
      </div>

      {/* ── Comparison Summary Bar ──────────────────────────────────── */}
      {hasComparison && reportData?.comparison?.summary && (
        <div className="card bg-gradient-to-r from-purple-50 to-brand-50 border-purple-200/50 p-5">
          <div className="flex items-center gap-3 mb-4">
            <GitCompareArrows size={18} className="text-purple-600" />
            <h3 className="text-sm font-semibold text-purple-900">Period Comparison</h3>
            <span className="text-xs text-purple-500">
              {period?.label} vs {compPeriod?.start_date} – {compPeriod?.end_date}
            </span>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-5 gap-4">
            {[
              { label: 'Spend', key: 'spend', fmt: 'currency' },
              { label: 'Sales', key: 'sales', fmt: 'currency' },
              { label: 'ACOS', key: 'acos', fmt: 'percent', invert: true },
              { label: 'ROAS', key: 'roas', fmt: 'decimal' },
              { label: 'Orders', key: 'orders', fmt: 'number' },
            ].map(m => (
              <div key={m.key} className="bg-white rounded-lg p-3 border border-purple-100">
                <p className="text-[10px] font-semibold text-purple-400 uppercase tracking-wider">{m.label}</p>
                <div className="mt-1 flex items-baseline gap-2">
                  <span className="text-lg font-bold text-slate-900">{fmt(activeSummary[m.key], m.fmt, currencyCode)}</span>
                  <span className="text-xs text-slate-400">vs {fmt(reportData.comparison.summary[m.key], m.fmt, currencyCode)}</span>
                </div>
                <DeltaBadge value={deltas[m.key]} invert={m.invert} />
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Charts Row 1 ────────────────────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Spend vs Sales Trend */}
        <ChartCard
          title="Spend vs Sales Trend"
          subtitle={trendData.length > 0 ? `${trendData.length} data point${trendData.length > 1 ? 's' : ''} — more audits build richer trends` : 'Run audits to build trend data'}
        >
          {trendData.length > 0 ? (
            <ResponsiveContainer width="100%" height={280}>
              <AreaChart data={trendData}>
                <defs>
                  <linearGradient id="gradSpend" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#4f46e5" stopOpacity={0.15} />
                    <stop offset="95%" stopColor="#4f46e5" stopOpacity={0} />
                  </linearGradient>
                  <linearGradient id="gradSales" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#10b981" stopOpacity={0.15} />
                    <stop offset="95%" stopColor="#10b981" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis dataKey="date" tick={{ fontSize: 11, fill: '#94a3b8' }} tickLine={false} axisLine={false} />
                <YAxis tick={{ fontSize: 11, fill: '#94a3b8' }} tickLine={false} axisLine={false} tickFormatter={v => fmtCurr(v, currencyCode)} />
                <Tooltip content={<CustomTooltip formatter={(v) => fmtCurr(v, currencyCode)} />} />
                <Legend iconType="circle" wrapperStyle={{ fontSize: 12, paddingTop: 8 }} />
                <Area type="monotone" dataKey="spend" name="Spend" stroke="#4f46e5" fill="url(#gradSpend)" strokeWidth={2} dot={{ r: 4 }} />
                <Area type="monotone" dataKey="sales" name="Sales" stroke="#10b981" fill="url(#gradSales)" strokeWidth={2} dot={{ r: 4 }} />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <div className="h-[280px] flex items-center justify-center text-sm text-slate-400">
              <div className="text-center">
                <BarChart3 size={32} className="mx-auto text-slate-300 mb-2" />
                <p>Run an audit or generate a report to build trend data</p>
              </div>
            </div>
          )}
        </ChartCard>

        {/* ACOS & ROAS Trend */}
        <ChartCard
          title="ACOS & ROAS Trend"
          subtitle="Efficiency metrics over time"
        >
          {trendData.length > 0 ? (
            <ResponsiveContainer width="100%" height={280}>
              <LineChart data={trendData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis dataKey="date" tick={{ fontSize: 11, fill: '#94a3b8' }} tickLine={false} axisLine={false} />
                <YAxis yAxisId="acos" tick={{ fontSize: 11, fill: '#94a3b8' }} tickLine={false} axisLine={false} tickFormatter={v => `${v}%`} />
                <YAxis yAxisId="roas" orientation="right" tick={{ fontSize: 11, fill: '#94a3b8' }} tickLine={false} axisLine={false} tickFormatter={v => `${v}x`} />
                <Tooltip content={<CustomTooltip formatter={(v, name) => name === 'ACOS' ? `${v.toFixed(1)}%` : `${v.toFixed(2)}x`} />} />
                <Legend iconType="circle" wrapperStyle={{ fontSize: 12, paddingTop: 8 }} />
                <Line yAxisId="acos" type="monotone" dataKey="acos" name="ACOS" stroke="#ef4444" strokeWidth={2} dot={{ r: 4 }} />
                <Line yAxisId="roas" type="monotone" dataKey="roas" name="ROAS" stroke="#8b5cf6" strokeWidth={2} dot={{ r: 4 }} />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <div className="h-[280px] flex items-center justify-center text-sm text-slate-400">
              <div className="text-center">
                <TrendingUp size={32} className="mx-auto text-slate-300 mb-2" />
                <p>Run an audit or generate a report to build trend data</p>
              </div>
            </div>
          )}
        </ChartCard>
      </div>

      {/* ── Charts Row 2 ────────────────────────────────────────────── */}
      {hasData && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Campaign Breakdown Bar Chart */}
          <ChartCard
            title="Top Campaigns by Spend"
            subtitle="Spend vs Sales comparison"
            className="lg:col-span-2"
          >
            {topCampaignChart.length > 0 ? (
              <ResponsiveContainer width="100%" height={320}>
                <BarChart data={topCampaignChart} layout="vertical" margin={{ left: 20 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" horizontal={false} />
                  <XAxis type="number" tick={{ fontSize: 11, fill: '#94a3b8' }} tickLine={false} axisLine={false} tickFormatter={v => fmtCurr(v, currencyCode)} />
                  <YAxis type="category" dataKey="name" width={140} tick={{ fontSize: 11, fill: '#64748b' }} tickLine={false} axisLine={false} />
                  <Tooltip content={<CustomTooltip formatter={(v) => fmtCurr(v, currencyCode)} />} />
                  <Legend iconType="circle" wrapperStyle={{ fontSize: 12, paddingTop: 8 }} />
                  <Bar dataKey="spend" name="Spend" fill="#4f46e5" radius={[0, 4, 4, 0]} barSize={16} />
                  <Bar dataKey="sales" name="Sales" fill="#10b981" radius={[0, 4, 4, 0]} barSize={16} />
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <div className="h-[320px] flex items-center justify-center text-sm text-slate-400">No data</div>
            )}
          </ChartCard>

          {/* Campaign Status Pie */}
          <ChartCard
            title="Campaign Status"
            subtitle="Distribution by state"
          >
            {stateData.length > 0 ? (
              <ResponsiveContainer width="100%" height={320}>
                <RePieChart>
                  <Pie
                    data={stateData}
                    cx="50%"
                    cy="50%"
                    innerRadius={60}
                    outerRadius={100}
                    paddingAngle={3}
                    dataKey="value"
                    label={({ name, percent }) => `${name} ${(percent * 100).toFixed(0)}%`}
                    labelLine={{ stroke: '#94a3b8', strokeWidth: 1 }}
                  >
                    {stateData.map((_, idx) => (
                      <Cell key={idx} fill={PIE_COLORS[idx % PIE_COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip formatter={(v) => v.toLocaleString()} />
                </RePieChart>
              </ResponsiveContainer>
            ) : (
              <div className="h-[320px] flex items-center justify-center text-sm text-slate-400">No data</div>
            )}
          </ChartCard>
        </div>
      )}

      {/* ── Performance Funnel ──────────────────────────────────────── */}
      {hasData && (
        <div className="card p-5">
          <h3 className="text-sm font-semibold text-slate-900 mb-1">Performance Funnel</h3>
          <p className="text-xs text-slate-400 mb-4">From impressions to conversions</p>
          <div className="flex items-center gap-2">
            {[
              { label: 'Impressions', value: activeSummary.impressions, format: 'compact', color: 'bg-cyan-500' },
              { label: 'Clicks', value: activeSummary.clicks, format: 'compact', color: 'bg-blue-500' },
              { label: 'Orders', value: activeSummary.orders, format: 'number', color: 'bg-purple-500' },
            ].map((step, idx) => (
              <div key={step.label} className="flex items-center gap-2 flex-1">
                <div className="flex-1 text-center">
                  <div className={clsx('mx-auto rounded-xl py-4 px-3', idx === 0 ? 'bg-cyan-50' : idx === 1 ? 'bg-blue-50' : 'bg-purple-50')}>
                    <p className="text-lg font-bold text-slate-900">{fmt(step.value, step.format)}</p>
                    <p className="text-xs text-slate-500 mt-0.5">{step.label}</p>
                  </div>
                  {idx < 2 && (
                    <p className="text-[10px] text-slate-400 mt-1">
                      {idx === 0 && activeSummary.ctr ? `${activeSummary.ctr}% CTR` : ''}
                      {idx === 1 && activeSummary.cvr ? `${activeSummary.cvr}% CVR` : ''}
                    </p>
                  )}
                </div>
                {idx < 2 && <ArrowRight size={16} className="text-slate-300 shrink-0" />}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Search Terms Section ──────────────────────────────────── */}
      <SearchTermsSection
        accountId={activeAccountId}
        syncing={stSyncing}
        data={stData}
        error={stError}
        filter={stFilter}
        onSync={syncSearchTerms}
        onFilterChange={setStFilter}
        onDismissError={dismissReportSearchTermsSync}
        currencyCode={currencyCode}
        key={activeAccount?.id || activeAccountId}
      />

      {/* ── Campaign Performance Table ──────────────────────────────── */}
      <CampaignTable campaigns={activeCampaigns} currencyCode={currencyCode} />

      {/* ── Top & Worst Performers ──────────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <PerformerCard
          title="Top Performers"
          icon={Trophy}
          iconColor="bg-emerald-50 text-emerald-600"
          campaigns={topPerformers}
          metric="sales"
          metricFormat="currency"
          metricLabel="Sales"
          currencyCode={currencyCode}
        />
        <PerformerCard
          title="Highest ACOS (Waste)"
          icon={AlertTriangle}
          iconColor="bg-red-50 text-red-600"
          campaigns={worstPerformers}
          metric="acos"
          metricFormat="percent"
          metricLabel="ACOS"
          currencyCode={currencyCode}
        />
      </div>

      {/* ── Additional Metrics Cards ────────────────────────────────── */}
      {hasData && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <KpiCard
            title="Avg CPC"
            value={activeSummary.cpc}
            format="currency"
            icon={MousePointerClick}
            color="blue"
            delta={hasComparison ? deltas.cpc : undefined}
            invertDelta={true}
            currencyCode={currencyCode}
          />
          <KpiCard
            title="Conversion Rate"
            value={activeSummary.cvr}
            format="percent"
            icon={Percent}
            color="emerald"
            delta={hasComparison ? deltas.cvr : undefined}
          />
          <KpiCard
            title="Active Campaigns"
            value={
              reportData?.state_breakdown?.enabled?.count
              || summaryData?.active_campaigns
              || activeCampaigns.filter(c => (c.state || '').toLowerCase() === 'enabled').length
            }
            format="number"
            icon={Layers}
            color="brand"
          />
          <KpiCard
            title="Total Campaigns"
            value={activeCampaigns.length}
            format="number"
            icon={BarChart3}
            color="amber"
          />
        </div>
      )}

      {/* ── Report History ─────────────────────────────────────────── */}
      <div className="card overflow-hidden">
        <button
          onClick={() => {
            setReportHistoryExpanded(e => !e)
            if (!reportHistoryExpanded && reportHistory.length === 0) loadReportHistory()
          }}
          className="w-full px-5 py-4 flex items-center justify-between hover:bg-slate-50 transition-colors"
        >
          <div className="flex items-center gap-2">
            <History size={18} className="text-slate-500" />
            <h3 className="text-sm font-semibold text-slate-900">Report History</h3>
          </div>
          <span className="text-xs text-slate-400">{reportHistory.length} saved</span>
        </button>
        {reportHistoryExpanded && (
          <div className="px-5 pb-4 border-t border-slate-100">
            {reportHistoryLoading ? (
              <div className="py-8 flex justify-center"><Loader2 size={24} className="animate-spin text-slate-400" /></div>
            ) : reportHistory.length === 0 ? (
              <p className="py-6 text-sm text-slate-500 text-center">No report history yet. Generate reports to see them here.</p>
            ) : (
              <ul className="divide-y divide-slate-100">
                {reportHistory.map((r) => (
                  <li key={r.id} className="py-3 flex items-center justify-between gap-4">
                    <div>
                      <p className="text-sm font-medium text-slate-800">{r.date_range_start} – {r.date_range_end}</p>
                      <p className="text-xs text-slate-500">{r.report_type} · {r.status} · {new Date(r.created_at).toLocaleString()}</p>
                    </div>
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => reports.detail(r.id).then(d => setReportData(d?.report_data || d))}
                        className="text-xs text-brand-600 hover:underline font-medium"
                      >
                        View
                      </button>
                      <button
                        onClick={() => deleteReport(r.id)}
                        disabled={deletingReportId === r.id}
                        className="p-1.5 rounded text-slate-400 hover:text-red-600 hover:bg-red-50 disabled:opacity-50"
                        title="Delete report"
                      >
                        {deletingReportId === r.id ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
