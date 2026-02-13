import clsx from 'clsx'

export default function MetricCard({ title, value, subtitle, icon: Icon, trend, color = 'brand' }) {
  const colorMap = {
    brand: 'bg-brand-50 text-brand-600',
    green: 'bg-emerald-50 text-emerald-600',
    amber: 'bg-amber-50 text-amber-600',
    red: 'bg-red-50 text-red-600',
    blue: 'bg-blue-50 text-blue-600',
  }

  return (
    <div className="card p-5">
      <div className="flex items-start justify-between">
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-slate-500 truncate">{title}</p>
          <p className="mt-2 text-2xl font-bold text-slate-900 tracking-tight">{value}</p>
          {subtitle && (
            <p className="mt-1 text-xs text-slate-400">{subtitle}</p>
          )}
          {trend && (
            <p className={clsx(
              'mt-1.5 text-xs font-medium',
              trend > 0 ? 'text-emerald-600' : trend < 0 ? 'text-red-600' : 'text-slate-400'
            )}>
              {trend > 0 ? '+' : ''}{trend}% vs last period
            </p>
          )}
        </div>
        {Icon && (
          <div className={clsx('flex items-center justify-center w-10 h-10 rounded-lg', colorMap[color])}>
            <Icon size={20} />
          </div>
        )}
      </div>
    </div>
  )
}
