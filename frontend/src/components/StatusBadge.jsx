import clsx from 'clsx'

const statusStyles = {
  active: 'badge-green',
  connected: 'badge-green',
  completed: 'badge-green',
  success: 'badge-green',
  enabled: 'badge-green',
  running: 'badge-blue',
  in_progress: 'badge-blue',
  pending: 'badge-yellow',
  paused: 'badge-yellow',
  warning: 'badge-yellow',
  expired: 'badge-red',
  error: 'badge-red',
  failed: 'badge-red',
  archived: 'badge-gray',
  disabled: 'badge-gray',
}

export default function StatusBadge({ status }) {
  const style = statusStyles[status?.toLowerCase()] || 'badge-gray'

  return (
    <span className={style}>
      {status}
    </span>
  )
}
