import { useState, useEffect } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  BarChart3,
  TrendingUp,
  Sparkles,
  AlertTriangle,
  ArrowRight,
  Activity,
  DollarSign,
  Target,
  ShoppingCart,
  Brain,
  Shield,
  Loader2,
  Clock,
  CheckCircle,
  Zap,
  Bot,
  Download,
  Search,
  Play,
  Rocket,
  CircleDot,
  Check,
} from 'lucide-react'
import MetricCard from '../components/MetricCard'
import StatusBadge from '../components/StatusBadge'
import { audit, optimizer, approvals, accounts as accountsApi, credentials } from '../lib/api'
import { useAccount } from '../lib/AccountContext'


// ── Onboarding Wizard ─────────────────────────────────────────────────
function OnboardingWizard({ activeAccountId, activeAccount, onComplete }) {
  const navigate = useNavigate()
  const { refreshAccounts } = useAccount()
  const [step, setStep] = useState(1)
  const [syncing, setSyncing] = useState(false)
  const [profiles, setProfiles] = useState([])

  const [syncProgress, setSyncProgress] = useState('')
  const [auditing, setAuditing] = useState(false)
  const [error, setError] = useState(null)
  // Derive region from the active account's marketplace or credential region
  // EU = EMEA (Europe, Middle East, Africa) — includes South Africa (ZA), Saudi Arabia (SA), etc.
  const EU_MARKETS = ['GB', 'DE', 'FR', 'IT', 'ES', 'NL', 'SE', 'PL', 'BE', 'TR', 'AE', 'SA', 'EG', 'ZA']
  const FE_MARKETS = ['JP', 'AU', 'SG', 'IN']
  function guessRegion(mp) {
    if (!mp) return 'na'
    const code = mp.toUpperCase()
    if (EU_MARKETS.includes(code)) return 'eu'
    if (FE_MARKETS.includes(code)) return 'fe'
    return 'na'
  }
  const [region, setRegion] = useState(
    activeAccount?.region || guessRegion(activeAccount?.marketplace) || 'na'
  )
  const [changingRegion, setChangingRegion] = useState(false)
  const [chosenProfileIdx, setChosenProfileIdx] = useState(null)

  async function updateRegion(newRegion) {
    setRegion(newRegion)
    if (activeAccountId && newRegion !== activeAccount?.region) {
      setChangingRegion(true)
      try {
        await credentials.update(activeAccountId, { region: newRegion })
        await refreshAccounts()
      } catch { /* ignore */ }
      setChangingRegion(false)
    }
  }

  async function discoverAccounts() {
    setSyncing(true)
    setError(null)
    try {
      const data = await accountsApi.discover(activeAccountId)
      const found = data?.accounts || []
      setProfiles(found)
      setChosenProfileIdx(found.length > 0 ? 0 : null) // Auto-select first profile
      setStep(2)
    } catch (err) {
      setError(err.message)
    } finally {
      setSyncing(false)
    }
  }

  async function activateAndSync() {
    if (chosenProfileIdx === null) {
      setError('Please select an account to sync')
      return
    }
    setSyncing(true)
    setError(null)
    try {
      const chosen = profiles[chosenProfileIdx]

      // First, we need to find the stored account ID for this profile
      // Refresh the stored accounts list to get the DB IDs
      setSyncProgress('Activating account...')
      const storedAccounts = await accountsApi.stored()
      const match = storedAccounts.find(a =>
        a.profile_id === chosen.profile_id ||
        a.amazon_account_id === chosen.amazon_account_id
      )

      if (match) {
        // Set this profile as active — this updates credential's profile_id
        await accountsApi.setActive(match.id)
      }

      // Now sync campaigns with the profile_id set
      setSyncProgress('Syncing campaigns...')
      await accountsApi.campaigns(activeAccountId)
      setSyncProgress('Syncing ad groups...')
      await accountsApi.adGroups(activeAccountId)
      setSyncProgress('Syncing targets...')
      await accountsApi.targets(activeAccountId)
      setSyncProgress('')
      await refreshAccounts()
      setStep(3)
    } catch (err) {
      setError(err.message)
      setSyncProgress('')
    } finally {
      setSyncing(false)
    }
  }

  async function runFirstAudit() {
    setAuditing(true)
    setError(null)
    try {
      await audit.run(activeAccountId)
      setStep(4)
    } catch (err) {
      setError(err.message)
    } finally {
      setAuditing(false)
    }
  }

  const steps = [
    { num: 1, label: 'Discover' },
    { num: 2, label: 'Select & Sync' },
    { num: 3, label: 'Audit' },
    { num: 4, label: 'Ready!' },
  ]

  return (
    <div className="space-y-6">
      {/* Welcome header */}
      <div className="card bg-gradient-to-br from-brand-600 via-brand-700 to-purple-700 p-8 text-white">
        <div className="flex items-start gap-5">
          <div className="flex items-center justify-center w-14 h-14 rounded-2xl bg-white/15 backdrop-blur-sm">
            <Rocket size={28} />
          </div>
          <div className="flex-1">
            <h2 className="text-xl font-bold">Welcome! Let's set up your account</h2>
            <p className="mt-2 text-brand-100 text-sm leading-relaxed">
              Your API credentials are connected. Now let's pull in your Amazon Ads data
              so you can start optimizing. This takes about 30 seconds.
            </p>
          </div>
        </div>

        {/* Progress steps */}
        <div className="mt-8 flex items-center gap-2">
          {steps.map((s, i) => (
            <div key={s.num} className="flex items-center gap-2 flex-1">
              <div className={`flex items-center justify-center w-8 h-8 rounded-full text-xs font-bold shrink-0 transition-all ${
                step > s.num ? 'bg-white text-brand-700' :
                step === s.num ? 'bg-white/25 text-white ring-2 ring-white' :
                'bg-white/10 text-white/40'
              }`}>
                {step > s.num ? <Check size={14} /> : s.num}
              </div>
              <span className={`text-xs font-medium hidden sm:block ${step >= s.num ? 'text-white' : 'text-white/40'}`}>
                {s.label}
              </span>
              {i < steps.length - 1 && (
                <div className={`flex-1 h-px ${step > s.num ? 'bg-white/50' : 'bg-white/10'}`} />
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="card bg-red-50 border-red-200 p-4 text-sm text-red-700 flex items-center gap-3">
          <AlertTriangle size={16} className="shrink-0" />
          <span>{error}</span>
          <button onClick={() => setError(null)} className="ml-auto text-red-400 hover:text-red-600 text-xs font-medium">Dismiss</button>
        </div>
      )}

      {/* Step 1: Discover */}
      {step === 1 && (
        <div className="card p-6">
          <div className="flex items-start gap-4">
            <div className="flex items-center justify-center w-12 h-12 rounded-xl bg-blue-50 text-blue-600 shrink-0">
              <Search size={22} />
            </div>
            <div className="flex-1">
              <h3 className="text-base font-semibold text-slate-900">Step 1: Discover your ad accounts</h3>
              <p className="mt-1 text-sm text-slate-500">
                Select your region, then we'll connect to Amazon Ads and find all advertising profiles.
              </p>

              {/* Region picker */}
              <div className="mt-4 mb-4">
                <label className="text-xs font-semibold text-slate-700 uppercase tracking-wide mb-2 block">Region</label>
                <div className="flex gap-2">
                  {[
                    { value: 'na', label: 'North America', flag: '🇺🇸', desc: 'US, CA, MX, BR' },
                    { value: 'eu', label: 'Europe (EMEA)', flag: '🇪🇺', desc: 'UK, DE, FR, IT, ES, ZA, AE...' },
                    { value: 'fe', label: 'Far East', flag: '🇯🇵', desc: 'JP, AU, SG, IN' },
                  ].map((r) => (
                    <button
                      key={r.value}
                      type="button"
                      onClick={() => updateRegion(r.value)}
                      disabled={changingRegion}
                      className={`flex-1 p-3 rounded-lg border-2 text-left transition-all ${
                        region === r.value
                          ? 'border-brand-500 bg-brand-50'
                          : 'border-slate-200 hover:border-slate-300 bg-white'
                      }`}
                    >
                      <div className="flex items-center gap-2">
                        <span className="text-lg">{r.flag}</span>
                        <div>
                          <p className={`text-sm font-medium ${region === r.value ? 'text-brand-700' : 'text-slate-700'}`}>
                            {r.label}
                          </p>
                          <p className="text-[10px] text-slate-400">{r.desc}</p>
                        </div>
                      </div>
                    </button>
                  ))}
                </div>
                <p className="mt-2 text-[11px] text-slate-400">
                  Region selects which Amazon Ads API endpoint to use. Discovery returns all accounts your credentials can access in that region.
                </p>
              </div>

              <button
                onClick={discoverAccounts}
                disabled={syncing || changingRegion}
                className="btn-primary"
              >
                {syncing ? (
                  <><Loader2 size={16} className="animate-spin" /> Discovering accounts...</>
                ) : changingRegion ? (
                  <><Loader2 size={16} className="animate-spin" /> Updating region...</>
                ) : (
                  <><Search size={16} /> Discover Accounts ({region.toUpperCase()})</>
                )}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Step 2: Select ONE profile & sync */}
      {step === 2 && (
        <div className="card p-6 space-y-5">
          <div className="flex items-start gap-4">
            <div className="flex items-center justify-center w-12 h-12 rounded-xl bg-emerald-50 text-emerald-600 shrink-0">
              <Download size={22} />
            </div>
            <div className="flex-1">
              <h3 className="text-base font-semibold text-slate-900">
                {profiles.length} profile{profiles.length !== 1 ? 's' : ''} found
              </h3>
              <p className="mt-1 text-sm text-slate-500">
                Choose the account you want to start with. You can switch accounts later from the sidebar.
              </p>
            </div>
          </div>

          {/* Profile list — single-select */}
          {profiles.length > 0 ? (
            <div className="space-y-2">
              <span className="text-xs text-slate-400 px-1">Pick one account to activate &amp; sync</span>
              <div className="border border-slate-200 rounded-lg divide-y divide-slate-100 max-h-80 overflow-y-auto">
                {profiles.map((profile, idx) => {
                  const isChosen = chosenProfileIdx === idx
                  return (
                    <label
                      key={idx}
                      className={`flex items-center gap-4 px-4 py-3 cursor-pointer transition-colors ${
                        isChosen ? 'bg-brand-50/60 ring-1 ring-inset ring-brand-300' : 'hover:bg-slate-50'
                      }`}
                    >
                      <input
                        type="radio"
                        name="onboard-profile"
                        checked={isChosen}
                        onChange={() => setChosenProfileIdx(idx)}
                        className="w-4 h-4 border-slate-300 text-brand-600 focus:ring-brand-500"
                      />
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-slate-900">
                          {profile.account_name || profile.name || `Profile ${idx + 1}`}
                        </p>
                        <p className="text-xs text-slate-400 mt-0.5">
                          {[
                            profile.account_type,
                            profile.marketplace,
                            profile.amazon_account_id && `ID: ${profile.amazon_account_id}`,
                          ].filter(Boolean).join(' · ') || 'Amazon Ads profile'}
                        </p>
                      </div>
                      <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${
                        profile.status === 'active' || profile.status === 'ACTIVE' || !profile.status
                          ? 'bg-emerald-50 text-emerald-700'
                          : 'bg-slate-100 text-slate-500'
                      }`}>
                        {profile.status || 'active'}
                      </span>
                    </label>
                  )
                })}
              </div>
            </div>
          ) : (
            <div className="text-center py-6">
              <p className="text-sm text-slate-500">No profiles found. Your account may not have any advertising profiles yet.</p>
              <button onClick={() => setStep(3)} className="btn-secondary mt-3">Skip & Continue</button>
            </div>
          )}

          {profiles.length > 0 && (
            <div className="flex items-center gap-3">
              <button
                onClick={activateAndSync}
                disabled={syncing || chosenProfileIdx === null}
                className="btn-primary"
              >
                {syncing ? (
                  <><Loader2 size={16} className="animate-spin" /> {syncProgress || 'Activating...'}</>
                ) : (
                  <><Download size={16} /> Activate &amp; Sync{chosenProfileIdx !== null ? ` — ${profiles[chosenProfileIdx]?.account_name || profiles[chosenProfileIdx]?.name || 'Account'}` : ''}</>
                )}
              </button>
              <button onClick={() => setStep(1)} className="btn-ghost text-xs">
                Re-discover
              </button>
            </div>
          )}
        </div>
      )}

      {/* Step 3: First Audit */}
      {step === 3 && (
        <div className="card p-6">
          <div className="flex items-start gap-4">
            <div className="flex items-center justify-center w-12 h-12 rounded-xl bg-purple-50 text-purple-600 shrink-0">
              <BarChart3 size={22} />
            </div>
            <div className="flex-1">
              <h3 className="text-base font-semibold text-slate-900">Campaigns synced!</h3>
              <p className="mt-1 text-sm text-slate-500">
                One last step — let's run your first campaign audit. This analyzes performance,
                identifies wasted spend, and finds optimization opportunities.
              </p>
              <div className="flex items-center gap-3 mt-4">
                <button
                  onClick={runFirstAudit}
                  disabled={auditing}
                  className="btn-primary"
                >
                  {auditing ? (
                    <><Loader2 size={16} className="animate-spin" /> Running audit...</>
                  ) : (
                    <><Play size={16} /> Run First Audit</>
                  )}
                </button>
                <button
                  onClick={() => setStep(4)}
                  className="btn-secondary"
                >
                  Skip for now
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Step 4: Done */}
      {step === 4 && (
        <div className="card p-6 bg-gradient-to-r from-emerald-50 to-teal-50 border-emerald-200">
          <div className="flex items-start gap-4">
            <div className="flex items-center justify-center w-12 h-12 rounded-xl bg-emerald-100 text-emerald-600 shrink-0">
              <Zap size={22} />
            </div>
            <div className="flex-1">
              <h3 className="text-base font-semibold text-emerald-900">You're all set!</h3>
              <p className="mt-1 text-sm text-emerald-700">
                Your account is synced and ready. You can add more accounts anytime from the account switcher in the sidebar.
              </p>
              <div className="mt-4 grid grid-cols-1 sm:grid-cols-3 gap-3">
                <Link to="/ai" className="flex items-center gap-3 p-3 bg-white rounded-lg border border-emerald-200 hover:border-brand-300 hover:shadow-md transition-all group">
                  <Brain size={18} className="text-brand-600" />
                  <div>
                    <p className="text-sm font-medium text-slate-900">Ask AI</p>
                    <p className="text-xs text-slate-500">Get insights & recommendations</p>
                  </div>
                </Link>
                <Link to="/audit" className="flex items-center gap-3 p-3 bg-white rounded-lg border border-emerald-200 hover:border-brand-300 hover:shadow-md transition-all group">
                  <BarChart3 size={18} className="text-blue-600" />
                  <div>
                    <p className="text-sm font-medium text-slate-900">View Audit</p>
                    <p className="text-xs text-slate-500">See performance analysis</p>
                  </div>
                </Link>
                <Link to="/optimizer" className="flex items-center gap-3 p-3 bg-white rounded-lg border border-emerald-200 hover:border-brand-300 hover:shadow-md transition-all group">
                  <TrendingUp size={18} className="text-emerald-600" />
                  <div>
                    <p className="text-sm font-medium text-slate-900">Optimize</p>
                    <p className="text-xs text-slate-500">Set up bid rules</p>
                  </div>
                </Link>
              </div>
              <button onClick={onComplete} className="btn-primary mt-4">
                <Rocket size={16} /> Go to Dashboard
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}


// ── Main Dashboard ────────────────────────────────────────────────────
export default function Dashboard() {
  const navigate = useNavigate()
  const { activeAccount, activeAccountId, activeProfileId, accounts, refreshAccounts } = useAccount()
  const [loading, setLoading] = useState(true)
  const [snapshots, setSnapshots] = useState([])
  const [activity, setActivity] = useState([])
  const [approvalSummary, setApprovalSummary] = useState(null)
  // null = not yet determined, true/false = determined
  const [showOnboarding, setShowOnboarding] = useState(null)
  const [hasCreds, setHasCreds] = useState(false)

  useEffect(() => {
    loadDashboard()
  }, [activeAccountId, activeProfileId])

  async function loadDashboard() {
    setLoading(true)
    try {
      const [snapshotsData, activityData, approvalsData, credsData] = await Promise.allSettled([
        audit.snapshots(activeAccountId),
        optimizer.activity(activeAccountId, 10),
        approvals.summary(activeAccountId, activeProfileId),
        credentials.list(),
      ])
      const snaps = snapshotsData.status === 'fulfilled' ? snapshotsData.value : []
      const acts = activityData.status === 'fulfilled' ? activityData.value : []
      const creds = credsData.status === 'fulfilled' ? credsData.value : []
      setSnapshots(snaps)
      setActivity(acts)
      setApprovalSummary(approvalsData.status === 'fulfilled' ? approvalsData.value : null)
      setHasCreds(creds.length > 0)

      // Show onboarding if credentials exist but no meaningful data yet
      // An audit with 0 campaigns means the profile wasn't set — treat as incomplete
      const hasRealData = snaps.some(s => (s.campaigns_count || 0) > 0)
      const hasCredentials = creds.length > 0
      setShowOnboarding(hasCredentials && !hasRealData)
    } catch (err) {
      // Ignore
    } finally {
      setLoading(false)
    }
  }

  const latestSnapshot = snapshots[0]
  const hasCredentials = hasCreds || accounts.length > 0
  const pendingCount = approvalSummary?.total_pending || 0

  // ── Loading state — show nothing until we know which view ───────────
  if (loading || showOnboarding === null) {
    return (
      <div className="flex items-center justify-center py-24">
        <Loader2 className="animate-spin text-slate-400" size={28} />
      </div>
    )
  }

  // ── Onboarding view ─────────────────────────────────────────────────
  if (showOnboarding) {
    return (
      <div className="space-y-8">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 tracking-tight">Overview</h1>
          <p className="mt-1 text-sm text-slate-500">
            {activeAccount
              ? <>Viewing <span className="font-medium text-slate-700">{activeAccount.account_name || activeAccount.name}</span> &middot; {activeAccount.marketplace || activeAccount.region?.toUpperCase()}</>
              : 'Account setup and optimization status'}
          </p>
        </div>
        <OnboardingWizard
          activeAccountId={activeAccountId}
          activeAccount={activeAccount}
          onComplete={async () => {
            await refreshAccounts()
            setShowOnboarding(false)
            navigate('/')
          }}
        />
      </div>
    )
  }

  // ── Regular dashboard view ──────────────────────────────────────────
  return (
    <div className="space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-slate-900 tracking-tight">Overview</h1>
        <p className="mt-1 text-sm text-slate-500">
          {activeAccount
            ? <>Viewing <span className="font-medium text-slate-700">{activeAccount.account_name || activeAccount.name}</span> &middot; {activeAccount.marketplace || activeAccount.region?.toUpperCase()}</>
            : 'Quick actions, metrics, and recent activity'}
        </p>
      </div>

      {/* Setup prompt if no credentials */}
      {!loading && !hasCredentials && (
        <div className="card bg-gradient-to-r from-brand-600 to-brand-700 p-6 text-white">
          <div className="flex items-start gap-4">
            <div className="flex items-center justify-center w-10 h-10 rounded-lg bg-white/20">
              <AlertTriangle size={20} />
            </div>
            <div className="flex-1">
              <h3 className="text-base font-semibold">Get Started</h3>
              <p className="mt-1 text-sm text-brand-100">
                Add your Amazon Ads API credentials to start optimizing your campaigns.
              </p>
              <Link
                to="/settings"
                className="inline-flex items-center gap-1.5 mt-3 text-sm font-medium text-white hover:text-brand-100 transition-colors"
              >
                Go to Settings <ArrowRight size={14} />
              </Link>
            </div>
          </div>
        </div>
      )}

      {/* Pending Approvals Alert */}
      {pendingCount > 0 && (
        <Link to="/approvals" className="block">
          <div className="card bg-gradient-to-r from-amber-50 to-orange-50 border-amber-200 p-5 hover:shadow-md transition-all group">
            <div className="flex items-center gap-4">
              <div className="flex items-center justify-center w-12 h-12 rounded-xl bg-amber-100 text-amber-600">
                <Shield size={22} />
              </div>
              <div className="flex-1">
                <h3 className="text-base font-semibold text-amber-900">
                  {pendingCount} Change{pendingCount !== 1 ? 's' : ''} Awaiting Review
                </h3>
                <p className="text-sm text-amber-700 mt-0.5">
                  Review and approve before pushing to Amazon Ads Manager
                </p>
              </div>
              <ArrowRight size={18} className="text-amber-400 group-hover:text-amber-600 transition-colors" />
            </div>
          </div>
        </Link>
      )}

      {/* Metric cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <MetricCard
          title="Total Campaigns"
          value={latestSnapshot?.campaigns_count ?? '—'}
          subtitle="From last audit"
          icon={BarChart3}
          color="brand"
        />
        <MetricCard
          title="Total Spend"
          value={latestSnapshot ? `$${(latestSnapshot.total_spend ?? 0).toLocaleString()}` : '—'}
          subtitle="Reported period"
          icon={DollarSign}
          color="blue"
        />
        <MetricCard
          title="Avg. ACOS"
          value={latestSnapshot ? `${(latestSnapshot.avg_acos ?? 0).toFixed(1)}%` : '—'}
          subtitle="Lower is better"
          icon={Target}
          color={(latestSnapshot?.avg_acos ?? 0) > 30 ? 'red' : 'green'}
        />
        <MetricCard
          title="Waste Identified"
          value={latestSnapshot ? `$${(latestSnapshot.waste_identified ?? 0).toLocaleString()}` : '—'}
          subtitle="Potential savings"
          icon={ShoppingCart}
          color="amber"
        />
      </div>

      {/* Quick actions + Activity feed */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Quick actions */}
        <div className="lg:col-span-1 space-y-3">
          <h2 className="text-sm font-semibold text-slate-900">Quick Actions</h2>

          <Link to="/ai" className="card p-4 flex items-center gap-4 hover:border-brand-200 hover:shadow-md transition-all group">
            <div className="flex items-center justify-center w-10 h-10 rounded-lg bg-gradient-to-br from-brand-50 to-purple-50 text-brand-600 group-hover:from-brand-100 group-hover:to-purple-100 transition-colors">
              <Brain size={20} />
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-slate-900">AI Assistant</p>
              <p className="text-xs text-slate-500">Get AI-powered insights & recommendations</p>
            </div>
            <ArrowRight size={16} className="text-slate-300 group-hover:text-brand-500 transition-colors" />
          </Link>

          <Link to="/approvals" className="card p-4 flex items-center gap-4 hover:border-brand-200 hover:shadow-md transition-all group">
            <div className="flex items-center justify-center w-10 h-10 rounded-lg bg-amber-50 text-amber-600 group-hover:bg-amber-100 transition-colors">
              <Shield size={20} />
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-slate-900">
                Review Changes
                {pendingCount > 0 && (
                  <span className="ml-2 inline-flex items-center justify-center w-5 h-5 text-[10px] font-bold bg-amber-500 text-white rounded-full">
                    {pendingCount}
                  </span>
                )}
              </p>
              <p className="text-xs text-slate-500">Approve changes before pushing to Ads Manager</p>
            </div>
            <ArrowRight size={16} className="text-slate-300 group-hover:text-brand-500 transition-colors" />
          </Link>

          <Link to="/audit" className="card p-4 flex items-center gap-4 hover:border-brand-200 hover:shadow-md transition-all group">
            <div className="flex items-center justify-center w-10 h-10 rounded-lg bg-blue-50 text-blue-600 group-hover:bg-blue-100 transition-colors">
              <BarChart3 size={20} />
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-slate-900">Run Campaign Audit</p>
              <p className="text-xs text-slate-500">Analyze performance & find waste</p>
            </div>
            <ArrowRight size={16} className="text-slate-300 group-hover:text-brand-500 transition-colors" />
          </Link>

          <Link to="/harvester" className="card p-4 flex items-center gap-4 hover:border-brand-200 hover:shadow-md transition-all group">
            <div className="flex items-center justify-center w-10 h-10 rounded-lg bg-purple-50 text-purple-600 group-hover:bg-purple-100 transition-colors">
              <Sparkles size={20} />
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-slate-900">Harvest Keywords</p>
              <p className="text-xs text-slate-500">Auto to manual keyword migration</p>
            </div>
            <ArrowRight size={16} className="text-slate-300 group-hover:text-brand-500 transition-colors" />
          </Link>

          <Link to="/optimizer" className="card p-4 flex items-center gap-4 hover:border-brand-200 hover:shadow-md transition-all group">
            <div className="flex items-center justify-center w-10 h-10 rounded-lg bg-emerald-50 text-emerald-600 group-hover:bg-emerald-100 transition-colors">
              <TrendingUp size={20} />
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-slate-900">Optimize Bids</p>
              <p className="text-xs text-slate-500">Hit your ACOS targets automatically</p>
            </div>
            <ArrowRight size={16} className="text-slate-300 group-hover:text-brand-500 transition-colors" />
          </Link>
        </div>

        {/* Activity feed */}
        <div className="lg:col-span-2">
          <h2 className="text-sm font-semibold text-slate-900 mb-3">Recent Activity</h2>
          <div className="card divide-y divide-slate-100">
            {activity.length === 0 ? (
              <div className="p-8 text-center">
                <Activity size={24} className="mx-auto text-slate-300 mb-2" />
                <p className="text-sm text-slate-500">No activity yet</p>
                <p className="text-xs text-slate-400 mt-1">
                  {activeAccount
                    ? `Run your first audit for ${activeAccount.account_name || activeAccount.name}`
                    : 'Select an account and run your first audit'}
                </p>
              </div>
            ) : (
              activity.map((log) => (
                <div key={log.id} className="px-5 py-3.5 flex items-center gap-3">
                  <div className={`flex items-center justify-center w-7 h-7 rounded-lg shrink-0 ${
                    log.category === 'ai' ? 'bg-brand-50 text-brand-600' :
                    log.category === 'approvals' ? 'bg-amber-50 text-amber-600' :
                    log.category === 'optimizer' ? 'bg-emerald-50 text-emerald-600' :
                    log.category === 'harvest' ? 'bg-purple-50 text-purple-600' :
                    'bg-slate-50 text-slate-500'
                  }`}>
                    {log.category === 'ai' ? <Bot size={14} /> :
                     log.category === 'approvals' ? <Shield size={14} /> :
                     log.category === 'optimizer' ? <TrendingUp size={14} /> :
                     log.category === 'harvest' ? <Sparkles size={14} /> :
                     <Activity size={14} />}
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-slate-700 truncate">{log.description}</p>
                    <p className="text-xs text-slate-400 mt-0.5">
                      {new Date(log.created_at).toLocaleString()}
                    </p>
                  </div>
                  <StatusBadge status={log.status} />
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
