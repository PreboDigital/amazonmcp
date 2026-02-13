/**
 * Notification context — in-app toasts and browser notifications.
 * Use for sync completion, errors, and other user feedback.
 */

import { createContext, useContext, useState, useCallback } from 'react'

const NotificationContext = createContext(null)

export function NotificationProvider({ children }) {
  const [toasts, setToasts] = useState([])

  const addToast = useCallback(({ type = 'info', title, message, duration = 5000 }) => {
    const id = Date.now() + Math.random()
    setToasts((prev) => [...prev, { id, type, title, message }])
    if (duration > 0) {
      setTimeout(() => {
        setToasts((prev) => prev.filter((t) => t.id !== id))
      }, duration)
    }
    return id
  }, [])

  const removeToast = useCallback((id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id))
  }, [])

  const success = useCallback((title, message) => addToast({ type: 'success', title, message }), [addToast])
  const error = useCallback((title, message) => addToast({ type: 'error', title, message, duration: 8000 }), [addToast])
  const info = useCallback((title, message) => addToast({ type: 'info', title, message }), [addToast])

  const requestBrowserNotificationPermission = useCallback(async () => {
    if (!('Notification' in window)) return false
    if (Notification.permission === 'granted') return true
    if (Notification.permission === 'denied') return false
    const perm = await Notification.requestPermission()
    return perm === 'granted'
  }, [])

  const showBrowserNotification = useCallback((title, options = {}) => {
    if (!('Notification' in window) || Notification.permission !== 'granted') return
    try {
      const n = new Notification(title, {
        icon: '/favicon.ico',
        badge: '/favicon.ico',
        ...options,
      })
      n.onclick = () => {
        window.focus()
        n.close()
      }
    } catch (e) {
      console.warn('Browser notification failed:', e)
    }
  }, [])

  return (
    <NotificationContext.Provider
      value={{
        toasts,
        addToast,
        removeToast,
        success,
        error,
        info,
        requestBrowserNotificationPermission,
        showBrowserNotification,
      }}
    >
      {children}
    </NotificationContext.Provider>
  )
}

export function useNotifications() {
  const ctx = useContext(NotificationContext)
  if (!ctx) throw new Error('useNotifications must be used within NotificationProvider')
  return ctx
}
