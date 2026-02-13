/**
 * SyncContext — Persistent sync state across navigation.
 * Campaign sync and report sync progress persist when user navigates away.
 * Polling runs in the provider so it continues in the background.
 * Banner shows running/completed status in-session (in addition to web/app notifications).
 */
import { createContext, useContext, useState, useCallback, useEffect, useRef } from 'react'
import { campaignManager, reports } from './api'
import { useNotifications } from './NotificationContext'

const SyncContext = createContext(null)

const CAMPAIGN_POLL_INTERVAL = 2000
const REPORT_SEARCH_TERMS_POLL_INTERVAL = 10000
const REPORT_GENERATE_POLL_INTERVAL = 8000
const COMPLETED_BANNER_DURATION_MS = 60000 // Show completed banner for 60s or until dismiss

export function SyncProvider({ children }) {
  const { success: notifySuccess, error: notifyError, requestBrowserNotificationPermission, showBrowserNotification } = useNotifications()

  // Campaign sync
  const [campaignSync, setCampaignSync] = useState({
    jobId: null,
    status: null, // 'running' | 'completed' | 'failed'
    step: '',
    progressPct: 0,
    stats: null,
    error: null,
    credentialId: null,
    completedAt: null,
  })

  // Report search terms sync
  const [reportSearchTermsSync, setReportSearchTermsSync] = useState({
    pendingReportId: null,
    status: null, // 'running' | 'completed' | 'failed'
    credentialId: null,
    error: null,
    completedAt: null,
  })

  // Report generate (when report_pending from Amazon)
  const [reportGenerateSync, setReportGenerateSync] = useState({
    status: null, // 'running' | 'completed' | 'failed'
    credentialId: null,
    opts: null,
    error: null,
    completedAt: null,
  })

  const campaignPollRef = useRef(null)
  const reportStPollRef = useRef(null)
  const reportGenPollRef = useRef(null)
  const hasBrowserNotifRef = useRef(false)

  // ── Campaign sync ───────────────────────────────────────────────────
  const startCampaignSync = useCallback(async (credentialId) => {
    if (campaignSync.status === 'running') return
    setCampaignSync({
      jobId: null,
      status: 'running',
      step: 'Starting sync...',
      progressPct: 0,
      stats: null,
      error: null,
      credentialId,
      completedAt: null,
    })
    hasBrowserNotifRef.current = await requestBrowserNotificationPermission()

    try {
      const { job_id } = await campaignManager.syncStart(credentialId)
      setCampaignSync(prev => ({ ...prev, jobId: job_id }))
    } catch (err) {
      setCampaignSync(prev => ({
        ...prev,
        status: 'failed',
        error: err.message,
        completedAt: Date.now(),
      }))
      notifyError('Campaign sync failed', err.message)
      if (hasBrowserNotifRef.current) {
        showBrowserNotification('Campaign sync failed', { body: err.message })
      }
    }
  }, [campaignSync.status, notifyError, requestBrowserNotificationPermission, showBrowserNotification])

  const dismissCampaignSync = useCallback(() => {
    setCampaignSync({
      jobId: null,
      status: null,
      step: '',
      progressPct: 0,
      stats: null,
      error: null,
      credentialId: null,
      completedAt: null,
    })
  }, [])

  // Campaign sync polling
  useEffect(() => {
    if (campaignSync.status !== 'running' || !campaignSync.jobId) return

    const poll = async () => {
      try {
        const status = await campaignManager.syncStatus(campaignSync.jobId)
        setCampaignSync(prev => ({
          ...prev,
          step: status.step || status.status,
          progressPct: status.progress_pct ?? 0,
        }))

        if (status.status === 'completed') {
          const s = status.stats || {}
          setCampaignSync(prev => ({
            ...prev,
            status: 'completed',
            step: '',
            stats: s,
            completedAt: Date.now(),
          }))
          notifySuccess('Campaign sync complete', `Synced ${s.campaigns || 0} campaigns, ${s.ad_groups || 0} ad groups, ${s.targets || 0} targets, ${s.ads || 0} ads`)
          if (hasBrowserNotifRef.current) {
            showBrowserNotification('Campaign sync complete', {
              body: `Synced ${s.campaigns || 0} campaigns, ${s.ad_groups || 0} ad groups.`,
            })
          }
          return
        }

        if (status.status === 'failed') {
          setCampaignSync(prev => ({
            ...prev,
            status: 'failed',
            step: '',
            error: status.error_message || 'Sync failed',
            completedAt: Date.now(),
          }))
          notifyError('Campaign sync failed', status.error_message || 'Sync failed')
          if (hasBrowserNotifRef.current) {
            showBrowserNotification('Campaign sync failed', { body: status.error_message || 'Sync failed' })
          }
          return
        }

        campaignPollRef.current = setTimeout(poll, CAMPAIGN_POLL_INTERVAL)
      } catch (err) {
        setCampaignSync(prev => ({
          ...prev,
          status: 'failed',
          error: err.message,
          completedAt: Date.now(),
        }))
        notifyError('Campaign sync failed', err.message)
      }
    }

    poll()
    return () => {
      if (campaignPollRef.current) clearTimeout(campaignPollRef.current)
    }
  }, [campaignSync.status, campaignSync.jobId, notifySuccess, notifyError, showBrowserNotification])

  // ── Report search terms sync ────────────────────────────────────────
  const startReportSearchTermsSync = useCallback(async (credentialId, pendingReportId = null) => {
    if (reportSearchTermsSync.status === 'running') return
    setReportSearchTermsSync({
      pendingReportId: pendingReportId || null,
      status: 'running',
      credentialId,
      error: null,
      completedAt: null,
    })
    hasBrowserNotifRef.current = await requestBrowserNotificationPermission()

    try {
      const result = await reports.searchTermSync(credentialId, {
        pendingReportId: pendingReportId || undefined,
      })
      if (result.status === 'completed') {
        setReportSearchTermsSync(prev => ({
          ...prev,
          status: 'completed',
          pendingReportId: null,
          completedAt: Date.now(),
        }))
        notifySuccess('Search terms sync complete', 'Search term data has been synced.')
        if (hasBrowserNotifRef.current) {
          showBrowserNotification('Search terms sync complete', { body: 'Search term data has been synced.' })
        }
      } else if (result.status === 'pending' && result._pending_report_id) {
        setReportSearchTermsSync(prev => ({
          ...prev,
          pendingReportId: result._pending_report_id,
        }))
      } else if (result.status === 'error') {
        setReportSearchTermsSync(prev => ({
          ...prev,
          status: 'failed',
          error: result.message || 'Failed to sync search terms',
          completedAt: Date.now(),
        }))
        notifyError('Search terms sync failed', result.message || 'Failed to sync search terms')
      }
    } catch (err) {
      setReportSearchTermsSync(prev => ({
        ...prev,
        status: 'failed',
        error: err.message || 'Failed to sync search terms',
        completedAt: Date.now(),
      }))
      notifyError('Search terms sync failed', err.message)
    }
  }, [reportSearchTermsSync.status, notifySuccess, notifyError, requestBrowserNotificationPermission, showBrowserNotification])

  const dismissReportSearchTermsSync = useCallback(() => {
    setReportSearchTermsSync({
      pendingReportId: null,
      status: null,
      credentialId: null,
      error: null,
      completedAt: null,
    })
  }, [])

  // Report search terms polling
  useEffect(() => {
    if (reportSearchTermsSync.status !== 'running' || !reportSearchTermsSync.pendingReportId) return

    const poll = async () => {
      try {
        const result = await reports.searchTermSync(reportSearchTermsSync.credentialId, {
          pendingReportId: reportSearchTermsSync.pendingReportId,
        })
        if (result.status === 'completed') {
          setReportSearchTermsSync(prev => ({
            ...prev,
            status: 'completed',
            pendingReportId: null,
            completedAt: Date.now(),
          }))
          notifySuccess('Search terms sync complete', 'Search term data has been synced.')
          if (hasBrowserNotifRef.current) {
            showBrowserNotification('Search terms sync complete', { body: 'Search term data has been synced.' })
          }
          return
        }
        if (result.status === 'error') {
          setReportSearchTermsSync(prev => ({
            ...prev,
            status: 'failed',
            error: result.message || 'Failed to sync search terms',
            completedAt: Date.now(),
          }))
          notifyError('Search terms sync failed', result.message || 'Failed')
          return
        }
        reportStPollRef.current = setTimeout(poll, REPORT_SEARCH_TERMS_POLL_INTERVAL)
      } catch (err) {
        setReportSearchTermsSync(prev => ({
          ...prev,
          status: 'failed',
          error: err.message,
          completedAt: Date.now(),
        }))
        notifyError('Search terms sync failed', err.message)
      }
    }

    reportStPollRef.current = setTimeout(poll, REPORT_SEARCH_TERMS_POLL_INTERVAL)
    return () => {
      if (reportStPollRef.current) clearTimeout(reportStPollRef.current)
    }
  }, [reportSearchTermsSync.status, reportSearchTermsSync.pendingReportId, reportSearchTermsSync.credentialId, notifySuccess, notifyError, showBrowserNotification])

  // ── Report generate (when report_pending) ────────────────────────────
  const startReportGenerateSync = useCallback((credentialId, opts, onComplete) => {
    if (reportGenerateSync.status === 'running') return
    setReportGenerateSync({
      status: 'running',
      credentialId,
      opts,
      error: null,
      completedAt: null,
    })
    reportGenerateSyncOnCompleteRef.current = onComplete
  }, [reportGenerateSync.status])

  const reportGenerateSyncOnCompleteRef = useRef(null)

  const pollReportGenerate = useCallback(async (credentialId, opts) => {
    try {
      const data = await reports.generate(credentialId, opts)
      if (!data?.report_pending) {
        setReportGenerateSync(prev => ({
          ...prev,
          status: 'completed',
          completedAt: Date.now(),
        }))
        const cb = reportGenerateSyncOnCompleteRef.current
        if (cb) cb(data)
        reportGenerateSyncOnCompleteRef.current = null
        notifySuccess('Report ready', 'Campaign performance report has been generated.')
        if (hasBrowserNotifRef.current) {
          showBrowserNotification('Report ready', { body: 'Campaign performance report has been generated.' })
        }
        return true
      }
      return false
    } catch (err) {
      setReportGenerateSync(prev => ({
        ...prev,
        status: 'failed',
        error: err.message,
        completedAt: Date.now(),
      }))
      notifyError('Report generation failed', err.message)
      return true // stop polling
    }
  }, [notifySuccess, notifyError, showBrowserNotification])

  const dismissReportGenerateSync = useCallback(() => {
    setReportGenerateSync({
      status: null,
      credentialId: null,
      opts: null,
      error: null,
      completedAt: null,
    })
    reportGenerateSyncOnCompleteRef.current = null
  }, [])

  // Report generate polling
  useEffect(() => {
    if (reportGenerateSync.status !== 'running' || !reportGenerateSync.credentialId || !reportGenerateSync.opts) return

    const poll = async () => {
      const done = await pollReportGenerate(reportGenerateSync.credentialId, reportGenerateSync.opts)
      if (!done) {
        reportGenPollRef.current = setTimeout(poll, REPORT_GENERATE_POLL_INTERVAL)
      }
    }
    reportGenPollRef.current = setTimeout(poll, REPORT_GENERATE_POLL_INTERVAL)
    return () => {
      if (reportGenPollRef.current) clearTimeout(reportGenPollRef.current)
    }
  }, [reportGenerateSync.status, reportGenerateSync.credentialId, reportGenerateSync.opts, pollReportGenerate])

  // ── Resume on mount (check syncLatest for campaigns) ─────────────────
  const resumeCampaignSyncIfNeeded = useCallback(async (credentialId) => {
    if (!credentialId || campaignSync.status === 'running') return
    try {
      const { job } = await campaignManager.syncLatest(credentialId)
      if (job?.status === 'running' && job?.job_id) {
        setCampaignSync({
          jobId: job.job_id,
          status: 'running',
          step: job.step || 'Syncing...',
          progressPct: job.progress_pct ?? 0,
          stats: null,
          error: null,
          credentialId,
          completedAt: null,
        })
      }
    } catch { /* ignore */ }
  }, [campaignSync.status])

  return (
    <SyncContext.Provider
      value={{
        campaignSync,
        startCampaignSync,
        dismissCampaignSync,
        resumeCampaignSyncIfNeeded,

        reportSearchTermsSync,
        startReportSearchTermsSync,
        dismissReportSearchTermsSync,

        reportGenerateSync,
        startReportGenerateSync,
        pollReportGenerate,
        dismissReportGenerateSync,

        COMPLETED_BANNER_DURATION_MS,
      }}
    >
      {children}
    </SyncContext.Provider>
  )
}

export function useSync() {
  const ctx = useContext(SyncContext)
  if (!ctx) throw new Error('useSync must be used within SyncProvider')
  return ctx
}
