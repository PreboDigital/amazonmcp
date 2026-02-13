/**
 * Date range picker — Amazon Ads–style with presets + calendar.
 * Supports presets and custom date range selection.
 */

import { useState } from 'react'
import { DayPicker } from 'react-day-picker'
import 'react-day-picker/style.css'
import clsx from 'clsx'

const PRESETS = [
  { key: 'today', label: 'Today' },
  { key: 'yesterday', label: 'Yesterday' },
  { key: 'last_7_days', label: 'Last 7 days' },
  { key: 'this_week', label: 'This week' },
  { key: 'last_week', label: 'Last week' },
  { key: 'last_30_days', label: 'Last 30 days' },
  { key: 'this_month', label: 'This month' },
  { key: 'last_month', label: 'Last month' },
  { key: 'year_to_date', label: 'Year-to-date' },
]

function getPresetRange(preset) {
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const iso = (d) => {
    const y = d.getFullYear()
    const m = String(d.getMonth() + 1).padStart(2, '0')
    const day = String(d.getDate()).padStart(2, '0')
    return `${y}-${m}-${day}`
  }

  if (preset === 'today') return { from: today, to: today }
  if (preset === 'yesterday') {
    const d = new Date(today); d.setDate(d.getDate() - 1)
    return { from: d, to: d }
  }
  if (preset === 'last_7_days') {
    const from = new Date(today); from.setDate(from.getDate() - 6)
    return { from, to: today }
  }
  if (preset === 'this_week') {
    const day = today.getDay()
    const mon = new Date(today); mon.setDate(mon.getDate() - ((day + 6) % 7))
    return { from: mon, to: today }
  }
  if (preset === 'last_week') {
    const day = today.getDay()
    const mon = new Date(today); mon.setDate(mon.getDate() - ((day + 6) % 7) - 7)
    const sun = new Date(mon); sun.setDate(sun.getDate() + 6)
    return { from: mon, to: sun }
  }
  if (preset === 'last_30_days') {
    // Match Amazon Ads dashboard: 31 days (today - 30 through today)
    const from = new Date(today); from.setDate(from.getDate() - 30)
    return { from, to: today }
  }
  if (preset === 'this_month') {
    const first = new Date(today.getFullYear(), today.getMonth(), 1)
    return { from: first, to: today }
  }
  if (preset === 'last_month') {
    const firstThis = new Date(today.getFullYear(), today.getMonth(), 1)
    const lastPrev = new Date(firstThis); lastPrev.setDate(lastPrev.getDate() - 1)
    const firstPrev = new Date(lastPrev.getFullYear(), lastPrev.getMonth(), 1)
    return { from: firstPrev, to: lastPrev }
  }
  if (preset === 'year_to_date') {
    const first = new Date(today.getFullYear(), 0, 1)
    return { from: first, to: today }
  }
  const weekAgo = new Date(today); weekAgo.setDate(weekAgo.getDate() - 7)
  return { from: weekAgo, to: today }
}

export default function DateRangePicker({
  value,
  onChange,
  onClose,
  className,
  anchorRef,
}) {
  const [mode, setMode] = useState('presets') // 'presets' | 'custom'
  const [preset, setPreset] = useState(
    value?.preset && PRESETS.some(p => p.key === value.preset) ? value.preset : null
  )
  const [range, setRange] = useState(() => {
    if (value?.start && value?.end) {
      const from = new Date(value.start + 'T12:00:00')
      const to = new Date(value.end + 'T12:00:00')
      return { from, to }
    }
    if (preset) {
      return getPresetRange(preset)
    }
    const today = new Date()
    today.setHours(0, 0, 0, 0)
    const first = new Date(today.getFullYear(), today.getMonth(), 1)
    return { from: first, to: today }
  })

  const iso = (d) => {
    if (!d) return ''
    const y = d.getFullYear()
    const m = String(d.getMonth() + 1).padStart(2, '0')
    const day = String(d.getDate()).padStart(2, '0')
    return `${y}-${m}-${day}`
  }

  function handlePresetClick(p) {
    setPreset(p)
    const { from, to } = getPresetRange(p)
    setRange({ from, to })
    setMode('presets')
  }

  function handleApply() {
    if (range.from) {
      const endDate = range.to || range.from
      const start = iso(range.from)
      const end = iso(endDate)
      if (start && end) {
        onChange({
          preset: preset || 'custom',
          start,
          end,
          label: preset ? PRESETS.find(p => p.key === preset)?.label : `Custom (${start} – ${end})`,
        })
      }
    }
    onClose?.()
  }

  function handleCancel() {
    onClose?.()
  }

  function handleRangeSelect(selected) {
    setRange(selected)
    setPreset(null)
  }

  return (
    <div
      className={clsx(
        'absolute z-50 mt-1 rounded-xl border border-slate-200 bg-white shadow-xl',
        'flex overflow-hidden',
        className
      )}
      style={anchorRef ? { minWidth: anchorRef.offsetWidth } : {}}
    >
      {/* Left: Presets */}
      <div className="w-44 flex-shrink-0 border-r border-slate-100 bg-slate-50/50 py-3">
        <div className="space-y-0.5">
          {PRESETS.map(p => (
            <button
              key={p.key}
              type="button"
              onClick={() => handlePresetClick(p.key)}
              className={clsx(
                'w-full px-4 py-2 text-left text-sm transition-colors',
                preset === p.key
                  ? 'bg-brand-600 text-white font-medium'
                  : 'text-slate-700 hover:bg-slate-100'
              )}
            >
              {p.label}
            </button>
          ))}
        </div>
        <p className="mt-3 px-4 text-[10px] text-slate-400">
          Dates are based on local time
        </p>
      </div>

      {/* Right: Calendar */}
      <div className="flex flex-col p-4">
        <div className="flex items-center justify-between mb-3">
          <span className="text-sm font-medium text-slate-700">Custom range</span>
        </div>
        <DayPicker
          mode="range"
          selected={range}
          onSelect={handleRangeSelect}
          numberOfMonths={2}
          disabled={{ after: new Date() }}
          className="rdp-range border-0 [&_.rdp-day_selected]:bg-brand-600 [&_.rdp-day_today]:font-semibold [&_.rdp-day_today]:text-brand-600"
        />
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={handleCancel}
            className="px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-100 rounded-lg transition-colors"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleApply}
            className="px-4 py-2 text-sm font-medium bg-brand-600 text-white rounded-lg hover:bg-brand-700 transition-colors shadow-sm"
          >
            Apply
          </button>
        </div>
      </div>
    </div>
  )
}

export { PRESETS as DATE_PRESETS, getPresetRange }
