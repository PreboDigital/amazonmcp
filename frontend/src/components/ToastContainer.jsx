/**
 * Toast container — renders in-app notification toasts.
 * Fixed position bottom-right, stacks vertically.
 */

import { CheckCircle, AlertTriangle, Info, X } from 'lucide-react'
import { useNotifications } from '../lib/NotificationContext'

const icons = {
  success: CheckCircle,
  error: AlertTriangle,
  info: Info,
}

const styles = {
  success: 'bg-emerald-50 border-emerald-200 text-emerald-800',
  error: 'bg-red-50 border-red-200 text-red-800',
  info: 'bg-blue-50 border-blue-200 text-blue-800',
}

export default function ToastContainer() {
  const { toasts, removeToast } = useNotifications()

  if (toasts.length === 0) return null

  return (
    <div
      className="fixed bottom-4 right-4 z-[9999] flex flex-col gap-2 max-w-sm w-full pointer-events-none"
      aria-live="polite"
      aria-label="Notifications"
    >
      {toasts.map((t) => {
        const Icon = icons[t.type] || Info
        const style = styles[t.type] || styles.info
        return (
          <div
            key={t.id}
            className={`pointer-events-auto flex items-start gap-3 p-4 rounded-xl border shadow-lg ${style}`}
            role="alert"
          >
            <Icon size={20} className="shrink-0 mt-0.5" />
            <div className="flex-1 min-w-0">
              {t.title && <p className="font-semibold text-sm">{t.title}</p>}
              {t.message && <p className="text-sm opacity-90 mt-0.5">{t.message}</p>}
            </div>
            <button
              onClick={() => removeToast(t.id)}
              className="p-1 rounded hover:bg-black/10 -m-1"
              aria-label="Dismiss"
            >
              <X size={16} />
            </button>
          </div>
        )
      })}
    </div>
  )
}
