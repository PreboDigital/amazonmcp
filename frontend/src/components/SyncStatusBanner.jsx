/**
 * SyncStatusBanner — Shows campaign and report sync progress/completion.
 * Renders in Layout so it persists across navigation.
 */
import { useEffect, useState } from 'react'
import { Loader2, Check, X, AlertTriangle } from 'lucide-react'
import clsx from 'clsx'
import { useSync } from '../lib/SyncContext'

export default function SyncStatusBanner() {
  const {
    campaignSync,
    dismissCampaignSync,
    reportSearchTermsSync,
    dismissReportSearchTermsSync,
    reportGenerateSync,
    dismissReportGenerateSync,
    COMPLETED_BANNER_DURATION_MS,
  } = useSync()

  const [now, setNow] = useState(Date.now())

  // Auto-dismiss completed banners after duration
  useEffect(() => {
    const completedAt = campaignSync.completedAt || reportSearchTermsSync.completedAt || reportGenerateSync.completedAt
    if (!completedAt) return
    const remaining = COMPLETED_BANNER_DURATION_MS - (now - completedAt)
    if (remaining <= 0) return
    const t = setTimeout(() => setNow(Date.now()), Math.min(remaining, 1000))
    return () => clearTimeout(t)
  }, [campaignSync.completedAt, reportSearchTermsSync.completedAt, reportGenerateSync.completedAt, now, COMPLETED_BANNER_DURATION_MS])

  const campaignCompletedAgo = campaignSync.completedAt ? now - campaignSync.completedAt : Infinity
  const reportStCompletedAgo = reportSearchTermsSync.completedAt ? now - reportSearchTermsSync.completedAt : Infinity
  const reportGenCompletedAgo = reportGenerateSync.completedAt ? now - reportGenerateSync.completedAt : Infinity

  const showCampaignCompleted = campaignSync.status === 'completed' && campaignCompletedAgo < COMPLETED_BANNER_DURATION_MS
  const showCampaignFailed = campaignSync.status === 'failed'
  const showReportStCompleted = reportSearchTermsSync.status === 'completed' && reportStCompletedAgo < COMPLETED_BANNER_DURATION_MS
  const showReportStFailed = reportSearchTermsSync.status === 'failed'
  const showReportGenCompleted = reportGenerateSync.status === 'completed' && reportGenCompletedAgo < COMPLETED_BANNER_DURATION_MS
  const showReportGenFailed = reportGenerateSync.status === 'failed'

  // Campaign sync banner
  if (campaignSync.status === 'running' || showCampaignCompleted || showCampaignFailed) {
    const isRunning = campaignSync.status === 'running'
    const isSuccess = campaignSync.status === 'completed'
    const isFailed = campaignSync.status === 'failed'

    return (
      <div
        className={clsx(
          'card px-5 py-4',
          isRunning && 'bg-blue-50 border-blue-200',
          isSuccess && 'bg-emerald-50 border-emerald-200',
          isFailed && 'bg-red-50 border-red-200'
        )}
      >
        <div className="flex items-center gap-3">
          {isRunning && <Loader2 size={18} className="animate-spin text-blue-600 shrink-0" />}
          {isSuccess && <Check size={18} className="text-emerald-600 shrink-0" />}
          {isFailed && <AlertTriangle size={18} className="text-red-600 shrink-0" />}
          <div className="flex-1 min-w-0">
            {isRunning && (
              <>
                <p className="text-sm font-medium text-blue-900">Syncing data from Amazon Ads...</p>
                <p className="text-xs text-blue-600 mt-0.5">{campaignSync.step}</p>
                <div className="mt-2 h-1.5 bg-blue-100 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-blue-500 transition-all duration-300"
                    style={{ width: `${campaignSync.progressPct}%` }}
                  />
                </div>
                <p className="text-[10px] text-blue-400 mt-1.5">
                  You can navigate away — you&apos;ll get a browser notification and email when sync completes.
                </p>
              </>
            )}
            {isSuccess && (
              <p className="text-sm font-medium text-emerald-800">
                Campaign sync complete: {campaignSync.stats?.campaigns || 0} campaigns, {campaignSync.stats?.ad_groups || 0} ad groups, {campaignSync.stats?.targets || 0} targets, {campaignSync.stats?.ads || 0} ads
              </p>
            )}
            {isFailed && (
              <p className="text-sm font-medium text-red-800">{campaignSync.error || 'Sync failed'}</p>
            )}
          </div>
          <button
            onClick={dismissCampaignSync}
            className="shrink-0 p-1 rounded hover:bg-black/5 text-slate-500 hover:text-slate-700"
            aria-label="Dismiss"
          >
            <X size={16} />
          </button>
        </div>
      </div>
    )
  }

  // Report search terms sync banner
  if (reportSearchTermsSync.status === 'running' || showReportStCompleted || showReportStFailed) {
    const isRunning = reportSearchTermsSync.status === 'running'
    const isSuccess = reportSearchTermsSync.status === 'completed'
    const isFailed = reportSearchTermsSync.status === 'failed'

    return (
      <div
        className={clsx(
          'card px-5 py-4',
          isRunning && 'bg-indigo-50 border-indigo-200',
          isSuccess && 'bg-emerald-50 border-emerald-200',
          isFailed && 'bg-red-50 border-red-200'
        )}
      >
        <div className="flex items-center gap-3">
          {isRunning && <Loader2 size={18} className="animate-spin text-indigo-600 shrink-0" />}
          {isSuccess && <Check size={18} className="text-emerald-600 shrink-0" />}
          {isFailed && <AlertTriangle size={18} className="text-red-600 shrink-0" />}
          <div className="flex-1 min-w-0">
            {isRunning && (
              <p className="text-sm font-medium text-indigo-900">
                Syncing search term report from Amazon... Report can take 5–10 minutes. You can navigate away.
              </p>
            )}
            {isSuccess && (
              <p className="text-sm font-medium text-emerald-800">Search terms sync complete</p>
            )}
            {isFailed && (
              <p className="text-sm font-medium text-red-800">{reportSearchTermsSync.error || 'Search terms sync failed'}</p>
            )}
          </div>
          <button
            onClick={dismissReportSearchTermsSync}
            className="shrink-0 p-1 rounded hover:bg-black/5 text-slate-500 hover:text-slate-700"
            aria-label="Dismiss"
          >
            <X size={16} />
          </button>
        </div>
      </div>
    )
  }

  // Report generate (report_pending) banner
  if (reportGenerateSync.status === 'running' || showReportGenCompleted || showReportGenFailed) {
    const isRunning = reportGenerateSync.status === 'running'
    const isSuccess = reportGenerateSync.status === 'completed'
    const isFailed = reportGenerateSync.status === 'failed'

    return (
      <div
        className={clsx(
          'card px-5 py-4',
          isRunning && 'bg-cyan-50 border-cyan-200',
          isSuccess && 'bg-emerald-50 border-emerald-200',
          isFailed && 'bg-red-50 border-red-200'
        )}
      >
        <div className="flex items-center gap-3">
          {isRunning && <Loader2 size={18} className="animate-spin text-cyan-600 shrink-0" />}
          {isSuccess && <Check size={18} className="text-emerald-600 shrink-0" />}
          {isFailed && <AlertTriangle size={18} className="text-red-600 shrink-0" />}
          <div className="flex-1 min-w-0">
            {isRunning && (
              <p className="text-sm font-medium text-cyan-900">
                Generating campaign performance report... Report is processing at Amazon. You can navigate away.
              </p>
            )}
            {isSuccess && (
              <p className="text-sm font-medium text-emerald-800">Report ready</p>
            )}
            {isFailed && (
              <p className="text-sm font-medium text-red-800">{reportGenerateSync.error || 'Report generation failed'}</p>
            )}
          </div>
          <button
            onClick={dismissReportGenerateSync}
            className="shrink-0 p-1 rounded hover:bg-black/5 text-slate-500 hover:text-slate-700"
            aria-label="Dismiss"
          >
            <X size={16} />
          </button>
        </div>
      </div>
    )
  }

  return null
}
