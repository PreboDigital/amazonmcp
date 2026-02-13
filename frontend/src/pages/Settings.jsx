import { useState, useEffect } from 'react'
import {
  Settings as SettingsIcon,
  Plus,
  Trash2,
  X,
  CheckCircle,
  XCircle,
  Loader2,
  Shield,
  Star,
  TestTube,
  Eye,
  EyeOff,
  RefreshCw,
  Clock,
  Zap,
  Brain,
  Search,
  Building2,
  Link2,
  Receipt,
  FileText,
  UserPlus,
  ChevronDown,
  ChevronUp,
} from 'lucide-react'
import StatusBadge from '../components/StatusBadge'
import EmptyState from '../components/EmptyState'
import { credentials, accounts, settingsApi } from '../lib/api'
import { useAccount } from '../lib/AccountContext'

export default function Settings() {
  const { refreshAccounts } = useAccount()
  const [creds, setCreds] = useState([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [testingId, setTestingId] = useState(null)
  const [testResult, setTestResult] = useState(null)
  const [error, setError] = useState(null)
  const [showTokens, setShowTokens] = useState({})

  const [form, setForm] = useState({
    name: '',
    client_id: '',
    client_secret: '',
    access_token: '',
    refresh_token: '',
    profile_id: '',
    account_id: '',
    region: 'na',
  })

  const [llmSettings, setLlmSettings] = useState(null)
  const [llmLoading, setLlmLoading] = useState(true)
  const [llmSearch, setLlmSearch] = useState('')
  const [llmSaving, setLlmSaving] = useState(false)

  const [apiKeys, setApiKeys] = useState(null)
  const [apiKeysLoading, setApiKeysLoading] = useState(true)
  const [apiKeysSaving, setApiKeysSaving] = useState(false)
  const [apiKeyForm, setApiKeyForm] = useState({ openai_api_key: '', anthropic_api_key: '', paapi_access_key: '', paapi_secret_key: '', paapi_partner_tag: '' })

  const [accountLinks, setAccountLinks] = useState([])
  const [accountInvoices, setAccountInvoices] = useState([])
  const [accountLinksLoading, setAccountLinksLoading] = useState(false)
  const [accountInvoicesLoading, setAccountInvoicesLoading] = useState(false)
  const [accountLinksError, setAccountLinksError] = useState(null)
  const [accountInvoicesError, setAccountInvoicesError] = useState(null)
  const [accountSettingsForm, setAccountSettingsForm] = useState({ display_name: '', currency_code: '', timezone: '' })
  const [accountSettingsSaving, setAccountSettingsSaving] = useState(false)
  const [termsToken, setTermsToken] = useState(null)
  const [termsTokenLoading, setTermsTokenLoading] = useState(false)
  const [termsTokenStatus, setTermsTokenStatus] = useState(null)
  const [invitations, setInvitations] = useState([])
  const [invitationsLoading, setInvitationsLoading] = useState(false)
  const [invitationsError, setInvitationsError] = useState(null)
  const [inviteForm, setInviteForm] = useState({ email: '', role: '' })
  const [inviteSending, setInviteSending] = useState(false)
  const [accountBillingExpanded, setAccountBillingExpanded] = useState(true)
  const [termsExpanded, setTermsExpanded] = useState(false)
  const [invitationsExpanded, setInvitationsExpanded] = useState(false)

  useEffect(() => {
    loadCredentials()
  }, [])

  async function loadAccountLinks() {
    setAccountLinksLoading(true)
    setAccountLinksError(null)
    try {
      const data = await accounts.links()
      setAccountLinks(data?.links || [])
    } catch (err) {
      setAccountLinksError(err.message || 'Failed to load account links')
      setAccountLinks([])
    } finally { setAccountLinksLoading(false) }
  }

  async function loadAccountInvoices() {
    setAccountInvoicesLoading(true)
    setAccountInvoicesError(null)
    try {
      const data = await accounts.invoices()
      setAccountInvoices(data?.invoices || [])
    } catch (err) {
      setAccountInvoicesError(err.message || 'Failed to load invoices')
      setAccountInvoices([])
    } finally { setAccountInvoicesLoading(false) }
  }

  async function createTermsToken() {
    setTermsTokenLoading(true)
    try {
      const data = await accounts.createTermsToken('ADSP')
      setTermsToken(data?.termsToken || data?.token || data)
    } catch (err) { setError(err.message) }
    finally { setTermsTokenLoading(false) }
  }

  async function loadInvitations() {
    setInvitationsLoading(true)
    setInvitationsError(null)
    try {
      const data = await accounts.invitations.list()
      setInvitations(data?.invitations || [])
    } catch (err) {
      setInvitationsError(err.message || 'Failed to load invitations')
      setInvitations([])
    } finally { setInvitationsLoading(false) }
  }

  async function sendInvite(e) {
    e?.preventDefault?.()
    if (!inviteForm.email?.trim()) return
    setInviteSending(true)
    setError(null)
    try {
      await accounts.invitations.create({ email: inviteForm.email.trim(), role: inviteForm.role || undefined })
      setInviteForm({ email: '', role: '' })
      await loadInvitations()
    } catch (err) { setError(err.message) }
    finally { setInviteSending(false) }
  }

  async function saveAccountSettings(e) {
    e?.preventDefault?.()
    const payload = {}
    if (accountSettingsForm.display_name) payload.display_name = accountSettingsForm.display_name
    if (accountSettingsForm.currency_code) payload.currency_code = accountSettingsForm.currency_code
    if (accountSettingsForm.timezone) payload.timezone = accountSettingsForm.timezone
    if (Object.keys(payload).length === 0) return
    setAccountSettingsSaving(true)
    setError(null)
    try {
      await accounts.updateSettings(payload)
      setAccountSettingsForm({ display_name: '', currency_code: '', timezone: '' })
    } catch (err) { setError(err.message) }
    finally { setAccountSettingsSaving(false) }
  }

  useEffect(() => {
    loadLlmSettings()
  }, [])

  useEffect(() => {
    loadApiKeys()
  }, [])

  async function loadApiKeys() {
    setApiKeysLoading(true)
    try {
      const data = await settingsApi.apiKeys.get()
      setApiKeys(data)
    } catch (err) {
      setApiKeys(null)
    } finally {
      setApiKeysLoading(false)
    }
  }

  async function saveApiKeys(e) {
    e?.preventDefault?.()
    const payload = {}
    if (apiKeyForm.openai_api_key) payload.openai_api_key = apiKeyForm.openai_api_key
    if (apiKeyForm.anthropic_api_key) payload.anthropic_api_key = apiKeyForm.anthropic_api_key
    if (apiKeyForm.paapi_access_key) payload.paapi_access_key = apiKeyForm.paapi_access_key
    if (apiKeyForm.paapi_secret_key) payload.paapi_secret_key = apiKeyForm.paapi_secret_key
    if (apiKeyForm.paapi_partner_tag) payload.paapi_partner_tag = apiKeyForm.paapi_partner_tag
    if (Object.keys(payload).length === 0) return
    setApiKeysSaving(true)
    setError(null)
    try {
      await settingsApi.apiKeys.update(payload)
      setApiKeyForm({ openai_api_key: '', anthropic_api_key: '', paapi_access_key: '', paapi_secret_key: '', paapi_partner_tag: '' })
      await loadApiKeys()
      await loadLlmSettings()
    } catch (err) {
      setError(err.message)
    } finally {
      setApiKeysSaving(false)
    }
  }

  async function clearApiKey(key) {
    setApiKeysSaving(true)
    setError(null)
    try {
      await settingsApi.apiKeys.update({ [key]: '' })
      await loadApiKeys()
      await loadLlmSettings()
    } catch (err) {
      setError(err.message)
    } finally {
      setApiKeysSaving(false)
    }
  }

  async function clearPaapiKeys() {
    setApiKeysSaving(true)
    setError(null)
    try {
      await settingsApi.apiKeys.update({ paapi_access_key: '', paapi_secret_key: '', paapi_partner_tag: '' })
      await loadApiKeys()
    } catch (err) {
      setError(err.message)
    } finally {
      setApiKeysSaving(false)
    }
  }

  async function loadLlmSettings() {
    setLlmLoading(true)
    try {
      const data = await settingsApi.llm.get()
      setLlmSettings(data)
    } catch (err) {
      setLlmSettings(null)
    } finally {
      setLlmLoading(false)
    }
  }

  function modelId(provider, model) {
    return `${provider}:${model}`
  }

  function isEnabled(provider, model) {
    if (!llmSettings?.enabled_llms) return false
    return llmSettings.enabled_llms.some((e) => e.provider === provider && e.model === model)
  }

  function isDefault(provider, model) {
    return llmSettings?.default_llm_id === modelId(provider, model)
  }

  async function toggleEnabled(provider, model, label) {
    if (!llmSettings) return
    const enabled = [...(llmSettings.enabled_llms || [])]
    const idx = enabled.findIndex((e) => e.provider === provider && e.model === model)
    if (idx >= 0) {
      enabled.splice(idx, 1)
    } else {
      enabled.push({ provider, model, label })
    }
    await updateLlmSettings({ enabled_llms: enabled })
  }

  async function setDefaultLlm(provider, model) {
    await updateLlmSettings({ default_llm_id: modelId(provider, model) })
  }

  async function updateLlmSettings(updates) {
    setLlmSaving(true)
    setError(null)
    try {
      await settingsApi.llm.update(updates)
      await loadLlmSettings()
    } catch (err) {
      setError(err.message)
    } finally {
      setLlmSaving(false)
    }
  }

  async function loadCredentials() {
    setLoading(true)
    try {
      const data = await credentials.list()
      setCreds(data)
    } catch (err) {
      // Ignore
    } finally {
      setLoading(false)
    }
  }

  async function createCredential(e) {
    e.preventDefault()
    setError(null)
    try {
      await credentials.create(form)
      setShowCreate(false)
      setForm({ name: '', client_id: '', client_secret: '', access_token: '', refresh_token: '', profile_id: '', account_id: '', region: 'na' })
      await loadCredentials()
      await refreshAccounts()
    } catch (err) {
      setError(err.message)
    }
  }

  async function testCredential(id) {
    setTestingId(id)
    setTestResult(null)
    try {
      const result = await credentials.test(id)
      setTestResult({ id, ...result })
      await loadCredentials()
    } catch (err) {
      setTestResult({ id, status: 'error', error: err.message })
    } finally {
      setTestingId(null)
    }
  }

  async function setDefault(id) {
    try {
      await credentials.setDefault(id)
      await loadCredentials()
      await refreshAccounts()
    } catch (err) {
      setError(err.message)
    }
  }

  async function deleteCred(id) {
    if (!confirm('Delete these credentials? This cannot be undone.')) return
    try {
      await credentials.delete(id)
      await loadCredentials()
      await refreshAccounts()
    } catch (err) {
      setError(err.message)
    }
  }

  function toggleToken(id) {
    setShowTokens(prev => ({ ...prev, [id]: !prev[id] }))
  }

  function maskToken(token) {
    if (!token) return '—'
    if (token.length <= 12) return '••••••••'
    return token.substring(0, 6) + '•••••••••••••' + token.substring(token.length - 4)
  }

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 tracking-tight">Settings</h1>
          <p className="mt-1 text-sm text-slate-500">
            Manage your Amazon Ads API credentials and MCP connection
          </p>
        </div>
        <button onClick={() => setShowCreate(true)} className="btn-primary">
          <Plus size={16} /> Add Credentials
        </button>
      </div>

      {/* Setup guide */}
      <div className="card p-5">
        <h3 className="text-sm font-semibold text-slate-900 mb-3">Connection Requirements</h3>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div className="flex items-start gap-3">
            <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-brand-50 text-brand-600 text-xs font-bold shrink-0">1</div>
            <div>
              <p className="text-sm font-medium text-slate-700">LwA Application</p>
              <p className="text-xs text-slate-400 mt-0.5">Create at developer.amazon.com</p>
            </div>
          </div>
          <div className="flex items-start gap-3">
            <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-brand-50 text-brand-600 text-xs font-bold shrink-0">2</div>
            <div>
              <p className="text-sm font-medium text-slate-700">API Access</p>
              <p className="text-xs text-slate-400 mt-0.5">Apply at advertising.amazon.com</p>
            </div>
          </div>
          <div className="flex items-start gap-3">
            <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-brand-50 text-brand-600 text-xs font-bold shrink-0">3</div>
            <div>
              <p className="text-sm font-medium text-slate-700">Auth Grant</p>
              <p className="text-xs text-slate-400 mt-0.5">Get access + refresh tokens</p>
            </div>
          </div>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="card bg-red-50 border-red-200 p-4 text-sm text-red-700">{error}</div>
      )}

      {/* Test result */}
      {testResult && (
        <div className={`card p-4 flex items-center gap-3 ${
          testResult.status === 'connected' ? 'bg-emerald-50 border-emerald-200' : 'bg-red-50 border-red-200'
        }`}>
          {testResult.status === 'connected' ? (
            <CheckCircle size={18} className="text-emerald-600" />
          ) : (
            <XCircle size={18} className="text-red-600" />
          )}
          <div>
            <p className={`text-sm font-medium ${testResult.status === 'connected' ? 'text-emerald-800' : 'text-red-800'}`}>
              {testResult.status === 'connected'
                ? `Connected! ${testResult.tools_available} tools available.`
                : `Connection failed: ${testResult.error}`}
            </p>
          </div>
          <button onClick={() => setTestResult(null)} className="ml-auto p-1 hover:bg-white/50 rounded">
            <X size={14} />
          </button>
        </div>
      )}

      {/* Create Modal */}
      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
          <div className="card w-full max-w-lg p-6 space-y-5 max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold text-slate-900">Add API Credentials</h2>
              <button onClick={() => setShowCreate(false)} className="p-1.5 hover:bg-slate-100 rounded-lg">
                <X size={18} className="text-slate-400" />
              </button>
            </div>

            <form onSubmit={createCredential} className="space-y-4">
              <div>
                <label className="label">Name</label>
                <input type="text" className="input" placeholder="e.g., Main Account — NA" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
              </div>

              {/* LwA Application Credentials */}
              <div className="rounded-lg border border-slate-200 p-4 space-y-3 bg-slate-50/50">
                <div className="flex items-center gap-2 mb-1">
                  <Shield size={14} className="text-brand-600" />
                  <span className="text-xs font-semibold text-slate-700 uppercase tracking-wide">LwA Application</span>
                </div>
                <div>
                  <label className="label">Client ID</label>
                  <input type="text" className="input font-mono text-xs" placeholder="amzn1.application-oa2-client.xxxx" value={form.client_id} onChange={(e) => setForm({ ...form, client_id: e.target.value })} required />
                  <p className="text-xs text-slate-400 mt-1">From your LwA application</p>
                </div>
                <div>
                  <label className="label">Client Secret</label>
                  <input type="password" className="input font-mono text-xs" placeholder="amzn1.oa2-cs.v1.xxxxx" value={form.client_secret} onChange={(e) => setForm({ ...form, client_secret: e.target.value })} />
                  <p className="text-xs text-slate-400 mt-1">Required for automatic token refresh</p>
                </div>
              </div>

              {/* Tokens */}
              <div className="rounded-lg border border-slate-200 p-4 space-y-3 bg-slate-50/50">
                <div className="flex items-center gap-2 mb-1">
                  <RefreshCw size={14} className="text-brand-600" />
                  <span className="text-xs font-semibold text-slate-700 uppercase tracking-wide">Tokens</span>
                </div>
                <div>
                  <label className="label">Access Token</label>
                  <input type="password" className="input font-mono text-xs" placeholder="Atza|xxxxx" value={form.access_token} onChange={(e) => setForm({ ...form, access_token: e.target.value })} required />
                  <p className="text-xs text-slate-400 mt-1">Bearer token from authorization grant</p>
                </div>
                <div>
                  <label className="label">Refresh Token</label>
                  <input type="password" className="input font-mono text-xs" placeholder="Atzr|xxxxx" value={form.refresh_token} onChange={(e) => setForm({ ...form, refresh_token: e.target.value })} />
                  <p className="text-xs text-slate-400 mt-1">With client secret, tokens auto-refresh — no manual work needed</p>
                </div>

                {form.client_secret && form.refresh_token && (
                  <div className="flex items-center gap-2 px-3 py-2 bg-emerald-50 border border-emerald-200 rounded-lg text-xs text-emerald-700">
                    <Zap size={13} className="text-emerald-500" />
                    <span className="font-medium">Auto-refresh enabled</span>
                    <span className="text-emerald-500">— tokens will renew automatically before expiry</span>
                  </div>
                )}
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="label">Profile ID (optional)</label>
                  <input type="text" className="input font-mono text-xs" placeholder="For fixed account mode" value={form.profile_id} onChange={(e) => setForm({ ...form, profile_id: e.target.value })} />
                </div>
                <div>
                  <label className="label">Region</label>
                  <select className="input" value={form.region} onChange={(e) => setForm({ ...form, region: e.target.value })}>
                    <option value="na">North America (NA)</option>
                    <option value="eu">Europe (EU)</option>
                    <option value="fe">Far East (FE)</option>
                  </select>
                </div>
              </div>

              <div>
                <label className="label">Account ID (optional)</label>
                <input type="text" className="input font-mono text-xs" placeholder="DSP or global account ID" value={form.account_id} onChange={(e) => setForm({ ...form, account_id: e.target.value })} />
              </div>

              <div className="flex justify-end gap-3 pt-2">
                <button type="button" onClick={() => setShowCreate(false)} className="btn-secondary">Cancel</button>
                <button type="submit" className="btn-primary">
                  <Shield size={14} /> Save Credentials
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Credentials List */}
      {loading ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="animate-spin text-slate-400" size={24} />
        </div>
      ) : creds.length === 0 ? (
        <EmptyState
          icon={Shield}
          title="No credentials configured"
          description="Add your Amazon Ads API credentials to connect to the MCP server."
          action={
            <button onClick={() => setShowCreate(true)} className="btn-primary">
              <Plus size={16} /> Add Credentials
            </button>
          }
        />
      ) : (
        <div className="space-y-3">
          {creds.map((cred) => (
            <div key={cred.id} className={`card p-5 ${cred.is_default ? 'ring-2 ring-brand-500/20 border-brand-200' : ''}`}>
              <div className="flex items-start justify-between">
                <div className="flex-1">
                  <div className="flex items-center gap-3">
                    <h3 className="text-sm font-semibold text-slate-900">{cred.name}</h3>
                    <StatusBadge status={cred.status} />
                    {cred.is_default && (
                      <span className="badge bg-brand-50 text-brand-700 ring-1 ring-inset ring-brand-600/20">
                        <Star size={10} className="mr-1" /> Default
                      </span>
                    )}
                    {cred.auto_refresh_enabled ? (
                      <span className="badge bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-600/20">
                        <Zap size={10} className="mr-1" /> Auto-Refresh
                      </span>
                    ) : (
                      <span className="badge bg-amber-50 text-amber-700 ring-1 ring-inset ring-amber-600/20">
                        <Clock size={10} className="mr-1" /> Manual Tokens
                      </span>
                    )}
                  </div>

                  <div className="mt-3 grid grid-cols-2 sm:grid-cols-5 gap-4 text-sm">
                    <div>
                      <p className="text-xs text-slate-400">Client ID</p>
                      <p className="font-mono text-xs text-slate-600 truncate">{cred.client_id}</p>
                    </div>
                    <div>
                      <p className="text-xs text-slate-400">Region</p>
                      <p className="font-medium text-slate-700 uppercase">{cred.region}</p>
                    </div>
                    <div>
                      <p className="text-xs text-slate-400">Profile ID</p>
                      <p className="font-mono text-xs text-slate-600">{cred.profile_id || '—'}</p>
                    </div>
                    <div>
                      <p className="text-xs text-slate-400">Token Expiry</p>
                      <p className="text-xs text-slate-600">
                        {(() => {
                          if (!cred.token_expires_at) return '—'
                          // Backend stores UTC — ensure the browser parses it as UTC
                          const raw = cred.token_expires_at
                          const expiry = new Date(raw.endsWith('Z') ? raw : raw + 'Z')
                          const now = new Date()
                          const minutesLeft = Math.round((expiry - now) / 60000)
                          if (expiry > now) {
                            return <span className="text-emerald-600 font-medium">
                              {minutesLeft > 60 ? `${Math.floor(minutesLeft/60)}h ${minutesLeft%60}m` : `${minutesLeft}m left`}
                            </span>
                          }
                          if (cred.auto_refresh_enabled) {
                            return <span className="text-amber-500 font-medium">Refreshes on next call</span>
                          }
                          return <span className="text-red-500 font-medium">Expired</span>
                        })()}
                      </p>
                    </div>
                    <div>
                      <p className="text-xs text-slate-400">Added</p>
                      <p className="text-slate-700">{new Date(cred.created_at).toLocaleDateString()}</p>
                    </div>
                  </div>
                </div>

                <div className="flex items-center gap-2 ml-4">
                  <button onClick={() => testCredential(cred.id)} disabled={testingId === cred.id} className="btn-secondary text-xs">
                    {testingId === cred.id ? <Loader2 size={14} className="animate-spin" /> : <TestTube size={14} />}
                    Test
                  </button>
                  {!cred.is_default && (
                    <button onClick={() => setDefault(cred.id)} className="btn-ghost text-xs">
                      <Star size={14} /> Set Default
                    </button>
                  )}
                  <button onClick={() => deleteCred(cred.id)} className="btn-ghost text-red-500 hover:bg-red-50">
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Account & Billing */}
      <div className="card overflow-hidden">
        <button
          onClick={() => setAccountBillingExpanded(e => !e)}
          className="w-full px-5 py-4 flex items-center justify-between hover:bg-slate-50 transition-colors"
        >
          <div className="flex items-center gap-2">
            <Building2 size={18} className="text-brand-600" />
            <h3 className="text-sm font-semibold text-slate-900">Account & Billing</h3>
          </div>
          {accountBillingExpanded ? <ChevronUp size={16} className="text-slate-400" /> : <ChevronDown size={16} className="text-slate-400" />}
        </button>
        {accountBillingExpanded && (
          <div className="px-5 pb-5 border-t border-slate-100 space-y-5">
            <p className="text-xs text-slate-500 pt-4">
              Manage your active advertising account settings, billing, terms, and user invitations.
            </p>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <h4 className="text-xs font-semibold text-slate-600 uppercase tracking-wide mb-2">Account links</h4>
                <button onClick={loadAccountLinks} disabled={accountLinksLoading} className="btn-secondary text-xs mb-2">
                  {accountLinksLoading ? <Loader2 size={12} className="animate-spin" /> : <Link2 size={12} />} Load links
                </button>
                {accountLinksError && <p className="text-xs text-red-600 mb-2">{accountLinksError}</p>}
                {accountLinks.length > 0 ? (
                  <ul className="text-xs text-slate-600 space-y-1.5">
                    {accountLinks.slice(0, 5).map((l, i) => (
                      <li key={i} className="flex items-start gap-2">
                        {typeof l === 'object' ? (
                          <span className="text-slate-700">
                            {l.displayName || l.accountName || l.advertiserAccountId || l.relationshipType || JSON.stringify(l).slice(0, 80)}
                            {Object.keys(l).length > 1 && '…'}
                          </span>
                        ) : (
                          <span>{String(l)}</span>
                        )}
                      </li>
                    ))}
                    {accountLinks.length > 5 && <li className="text-slate-400">+{accountLinks.length - 5} more</li>}
                  </ul>
                ) : accountLinksLoading ? null : (
                  <p className="text-xs text-slate-400">No linked accounts or click Load</p>
                )}
              </div>
              <div>
                <h4 className="text-xs font-semibold text-slate-600 uppercase tracking-wide mb-2">Invoices</h4>
                <button onClick={loadAccountInvoices} disabled={accountInvoicesLoading} className="btn-secondary text-xs mb-2">
                  {accountInvoicesLoading ? <Loader2 size={12} className="animate-spin" /> : <Receipt size={12} />} Load invoices
                </button>
                {accountInvoicesError && <p className="text-xs text-red-600 mb-2">{accountInvoicesError}</p>}
                {accountInvoices.length > 0 ? (
                  <ul className="text-xs text-slate-600 space-y-1.5">
                    {accountInvoices.slice(0, 5).map((inv, i) => (
                      <li key={i}>
                        {typeof inv === 'object' ? (
                          <span className="text-slate-700">
                            {inv.invoiceId || inv.id || inv.period || inv.amount ? (
                              <>{(inv.period || inv.invoiceId || '').slice(0, 30)} {inv.amount != null ? `· ${inv.amount}` : ''}</>
                            ) : (
                              JSON.stringify(inv).slice(0, 60) + '…'
                            )}
                          </span>
                        ) : (
                          String(inv)
                        )}
                      </li>
                    ))}
                    {accountInvoices.length > 5 && <li className="text-slate-400">+{accountInvoices.length - 5} more</li>}
                  </ul>
                ) : accountInvoicesLoading ? null : (
                  <p className="text-xs text-slate-400">No invoices or click Load</p>
                )}
              </div>
            </div>

            <form onSubmit={saveAccountSettings} className="space-y-3 pt-4 border-t border-slate-100">
              <h4 className="text-xs font-semibold text-slate-600 uppercase tracking-wide">Account settings</h4>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <div>
                  <label className="label text-xs">Display name</label>
                  <input type="text" className="input text-sm" placeholder="Account name" value={accountSettingsForm.display_name} onChange={e => setAccountSettingsForm(f => ({ ...f, display_name: e.target.value }))} />
                </div>
                <div>
                  <label className="label text-xs">Currency</label>
                  <input type="text" className="input text-sm" placeholder="USD" value={accountSettingsForm.currency_code} onChange={e => setAccountSettingsForm(f => ({ ...f, currency_code: e.target.value }))} />
                </div>
                <div>
                  <label className="label text-xs">Timezone</label>
                  <input type="text" className="input text-sm" placeholder="America/New_York" value={accountSettingsForm.timezone} onChange={e => setAccountSettingsForm(f => ({ ...f, timezone: e.target.value }))} />
                </div>
              </div>
              <button type="submit" disabled={accountSettingsSaving || (!accountSettingsForm.display_name && !accountSettingsForm.currency_code && !accountSettingsForm.timezone)} className="btn-primary text-sm">
                {accountSettingsSaving ? <Loader2 size={14} className="animate-spin" /> : null} Update account
              </button>
            </form>

            {/* Terms Token (ADSP) */}
            <div className="pt-4 border-t border-slate-100">
              <button onClick={() => setTermsExpanded(e => !e)} className="flex items-center gap-2 text-xs font-semibold text-slate-600 uppercase tracking-wide hover:text-slate-800">
                <FileText size={14} /> Advertising terms (ADSP)
                {termsExpanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
              </button>
              {termsExpanded && (
                <div className="mt-3 p-3 bg-slate-50 rounded-lg space-y-2">
                  <p className="text-xs text-slate-500">Create a terms token for ADSP (Amazon DSP) advertising terms acceptance.</p>
                  <div className="flex items-center gap-2">
                    <button onClick={createTermsToken} disabled={termsTokenLoading} className="btn-secondary text-xs">
                      {termsTokenLoading ? <Loader2 size={12} className="animate-spin" /> : <FileText size={12} />} Create terms token
                    </button>
                    {termsToken && (
                      <code className="text-xs font-mono bg-white px-2 py-1 rounded border border-slate-200 text-slate-700 truncate max-w-[200px]" title={termsToken}>
                        {termsToken}
                      </code>
                    )}
                  </div>
                </div>
              )}
            </div>

            {/* User Invitations */}
            <div className="pt-4 border-t border-slate-100">
              <button onClick={() => { setInvitationsExpanded(e => !e); if (!invitationsExpanded && invitations.length === 0) loadInvitations() }} className="flex items-center gap-2 text-xs font-semibold text-slate-600 uppercase tracking-wide hover:text-slate-800">
                <UserPlus size={14} /> User invitations
                {invitationsExpanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
              </button>
              {invitationsExpanded && (
                <div className="mt-3 space-y-3">
                  <form onSubmit={sendInvite} className="flex flex-wrap items-end gap-2">
                    <div>
                      <label className="label text-[10px]">Email</label>
                      <input type="email" className="input text-sm w-48" placeholder="user@example.com" value={inviteForm.email} onChange={e => setInviteForm(f => ({ ...f, email: e.target.value }))} required />
                    </div>
                    <div>
                      <label className="label text-[10px]">Role (optional)</label>
                      <input type="text" className="input text-sm w-24" placeholder="user" value={inviteForm.role} onChange={e => setInviteForm(f => ({ ...f, role: e.target.value }))} />
                    </div>
                    <button type="submit" disabled={inviteSending || !inviteForm.email?.trim()} className="btn-primary text-xs">
                      {inviteSending ? <Loader2 size={12} className="animate-spin" /> : <UserPlus size={12} />} Invite
                    </button>
                  </form>
                  {invitationsError && <p className="text-xs text-red-600">{invitationsError}</p>}
                  {invitationsLoading ? (
                    <div className="flex items-center gap-2 text-xs text-slate-500"><Loader2 size={12} className="animate-spin" /> Loading invitations...</div>
                  ) : invitations.length > 0 ? (
                    <ul className="text-xs text-slate-600 space-y-1">
                      {invitations.slice(0, 10).map((inv, i) => (
                        <li key={i} className="flex items-center justify-between py-1">
                          <span>{inv.email || inv.invitationId || JSON.stringify(inv).slice(0, 40)}</span>
                          <span className="text-slate-400">{inv.status || inv.state || ''}</span>
                        </li>
                      ))}
                      {invitations.length > 10 && <li className="text-slate-400">+{invitations.length - 10} more</li>}
                    </ul>
                  ) : (
                    <p className="text-xs text-slate-400">No invitations. Invite users to share access to your advertising account.</p>
                  )}
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* API Keys for AI */}
      <div className="card p-5">
        <h3 className="text-sm font-semibold text-slate-900 mb-3 flex items-center gap-2">
          <Shield size={18} className="text-brand-600" />
          AI API Keys
        </h3>
        <p className="text-xs text-slate-500 mb-4">
          Add your API keys to enable GPT and Claude models. Keys are stored encrypted. Environment variables take precedence if set.
        </p>

        {apiKeysLoading ? (
          <div className="flex items-center justify-center py-4">
            <Loader2 className="animate-spin text-slate-400" size={20} />
          </div>
        ) : (
          <form onSubmit={saveApiKeys} className="space-y-4">
            <div>
              <label className="label">OpenAI API Key (GPT models)</label>
              <input
                type="password"
                className="input font-mono text-xs"
                placeholder={apiKeys?.openai_configured ? '•••••••••••••••• (leave blank to keep)' : 'sk-...'}
                value={apiKeyForm.openai_api_key}
                onChange={(e) => setApiKeyForm((f) => ({ ...f, openai_api_key: e.target.value }))}
              />
              {apiKeys?.openai_configured && (
                <p className="text-xs text-emerald-600 mt-1 flex items-center gap-2">
                  <CheckCircle size={12} /> Configured {apiKeys.openai_source === 'env' && '(from env)'}
                  {apiKeys.openai_source !== 'env' && (
                    <button type="button" className="text-amber-600 hover:text-amber-700" onClick={() => clearApiKey('openai_api_key')}>Clear</button>
                  )}
                </p>
              )}
            </div>
            <div>
              <label className="label">Anthropic API Key (Claude models)</label>
              <input
                type="password"
                className="input font-mono text-xs"
                placeholder={apiKeys?.anthropic_configured ? '•••••••••••••••• (leave blank to keep)' : 'sk-ant-...'}
                value={apiKeyForm.anthropic_api_key}
                onChange={(e) => setApiKeyForm((f) => ({ ...f, anthropic_api_key: e.target.value }))}
              />
              {apiKeys?.anthropic_configured && (
                <p className="text-xs text-emerald-600 mt-1 flex items-center gap-2">
                  <CheckCircle size={12} /> Configured {apiKeys.anthropic_source === 'env' && '(from env)'}
                  {apiKeys.anthropic_source !== 'env' && (
                    <button type="button" className="text-amber-600 hover:text-amber-700" onClick={() => clearApiKey('anthropic_api_key')}>Clear</button>
                  )}
                </p>
              )}
            </div>
            <div className="pt-4 mt-4 border-t border-slate-200">
              <p className="text-xs font-semibold text-slate-600 uppercase tracking-wide mb-3">Product Images (PA-API)</p>
              <p className="text-xs text-slate-500 mb-3">Optional. Amazon Product Advertising API credentials to fetch product images by ASIN in Campaign Manager. Get keys at affiliate-program.amazon.com.</p>
              <div className="space-y-3">
                <div>
                  <label className="label">Access Key</label>
                  <input
                    type="password"
                    className="input font-mono text-xs"
                    placeholder={apiKeys?.paapi_configured ? '•••••••••••••••• (leave blank to keep)' : 'AKIA...'}
                    value={apiKeyForm.paapi_access_key}
                    onChange={(e) => setApiKeyForm((f) => ({ ...f, paapi_access_key: e.target.value }))}
                  />
                </div>
                <div>
                  <label className="label">Secret Key</label>
                  <input
                    type="password"
                    className="input font-mono text-xs"
                    placeholder={apiKeys?.paapi_configured ? '•••••••••••••••• (leave blank to keep)' : 'Secret'}
                    value={apiKeyForm.paapi_secret_key}
                    onChange={(e) => setApiKeyForm((f) => ({ ...f, paapi_secret_key: e.target.value }))}
                  />
                </div>
                <div>
                  <label className="label">Partner Tag (Associate ID)</label>
                  <input
                    type="text"
                    className="input font-mono text-xs"
                    placeholder={apiKeys?.paapi_configured ? '(leave blank to keep)' : 'yourtag-20'}
                    value={apiKeyForm.paapi_partner_tag}
                    onChange={(e) => setApiKeyForm((f) => ({ ...f, paapi_partner_tag: e.target.value }))}
                  />
                </div>
                {apiKeys?.paapi_configured && (
                  <p className="text-xs text-emerald-600 flex items-center gap-2">
                    <CheckCircle size={12} /> PA-API configured
                    <button type="button" className="text-amber-600 hover:text-amber-700" onClick={clearPaapiKeys}>Clear</button>
                  </p>
                )}
              </div>
            </div>
            <button type="submit" className="btn-primary" disabled={apiKeysSaving || (!apiKeyForm.openai_api_key.trim() && !apiKeyForm.anthropic_api_key.trim() && !apiKeyForm.paapi_access_key.trim() && !apiKeyForm.paapi_secret_key.trim() && !apiKeyForm.paapi_partner_tag.trim())}>
              {apiKeysSaving ? <Loader2 size={14} className="animate-spin" /> : <Shield size={14} />}
              {' '}Save API Keys
            </button>
          </form>
        )}
      </div>

      {/* AI Models / LLM Settings */}
      <div className="card p-5">
        <h3 className="text-sm font-semibold text-slate-900 mb-3 flex items-center gap-2">
          <Brain size={18} className="text-brand-600" />
          AI Models & Default LLM
        </h3>
        <p className="text-xs text-slate-500 mb-4">
          Select which models to use and set the default for the AI Assistant and all AI features.
          Add API keys above first.
        </p>

        {llmLoading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="animate-spin text-slate-400" size={24} />
          </div>
        ) : llmSettings ? (
          <div className="space-y-4">
            {Object.values(llmSettings.providers_configured || {}).every((v) => !v) ? (
              <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
                No AI providers configured. Add your API keys in the section above.
              </div>
            ) : (
              <>
                <div className="relative">
                  <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                  <input
                    type="text"
                    placeholder="Search models..."
                    className="input pl-9 w-full"
                    value={llmSearch}
                    onChange={(e) => setLlmSearch(e.target.value)}
                  />
                </div>

                <div className="space-y-2 max-h-64 overflow-y-auto">
                  {(llmSettings.available_llms || []).filter((llm) => {
                    const q = llmSearch.toLowerCase()
                    return !q || llm.label?.toLowerCase().includes(q) || llm.model?.toLowerCase().includes(q)
                  }).map((llm) => {
                    const id = modelId(llm.provider, llm.model)
                    const enabled = isEnabled(llm.provider, llm.model)
                    const isDef = isDefault(llm.provider, llm.model)
                    const hasKey = llmSettings.providers_configured?.[llm.provider]
                    return (
                      <div
                        key={id}
                        className={`flex items-center gap-3 px-3 py-2.5 rounded-lg border transition-colors ${
                          isDef ? 'bg-brand-50 border-brand-200' : 'bg-slate-50/50 border-slate-200 hover:bg-slate-100/50'
                        }`}
                      >
                        <Brain size={16} className="text-slate-500 shrink-0" />
                        <div className="flex-1 min-w-0">
                          <p className="text-sm font-medium text-slate-900">{llm.label}</p>
                          {llm.description && (
                            <p className="text-xs text-slate-500 truncate">{llm.description}</p>
                          )}
                        </div>
                        {hasKey ? (
                          <div className="flex items-center gap-2 shrink-0">
                            <button
                              type="button"
                              onClick={() => toggleEnabled(llm.provider, llm.model, llm.label)}
                              className={`text-xs px-2 py-1 rounded ${
                                enabled ? 'bg-brand-100 text-brand-700' : 'bg-slate-200 text-slate-600 hover:bg-slate-300'
                              }`}
                            >
                              {enabled ? 'Enabled' : 'Add'}
                            </button>
                            {enabled && (
                              <button
                                type="button"
                                onClick={() => setDefaultLlm(llm.provider, llm.model)}
                                className={`p-1.5 rounded-full transition-colors ${
                                  isDef ? 'text-brand-600 bg-brand-100' : 'text-slate-400 hover:text-brand-600 hover:bg-slate-200'
                                }`}
                                title={isDef ? 'Default model' : 'Set as default'}
                              >
                                <Star size={16} fill={isDef ? 'currentColor' : 'none'} />
                              </button>
                            )}
                          </div>
                        ) : (
                          <span className="text-xs text-slate-400 shrink-0">API key not set</span>
                        )}
                      </div>
                    )
                  })}
                </div>

                {llmSettings.default_llm_id && (
                  <div className="flex items-center gap-2 pt-2 text-sm text-slate-600">
                    <Star size={14} className="text-brand-500" fill="currentColor" />
                    <span>Default: {llmSettings.enabled_llms?.find((e) => modelId(e.provider, e.model) === llmSettings.default_llm_id)?.label || llmSettings.default_llm_id}</span>
                  </div>
                )}
              </>
            )}
          </div>
        ) : (
          <div className="text-sm text-slate-500 py-4">Failed to load settings.</div>
        )}
      </div>

      {/* MCP Server Info */}
      <div className="card p-5">
        <h3 className="text-sm font-semibold text-slate-900 mb-3">MCP Server Endpoints</h3>
        <div className="space-y-2">
          {[
            { region: 'NA', url: 'https://advertising-ai.amazon.com/mcp' },
            { region: 'EU', url: 'https://advertising-ai-eu.amazon.com/mcp' },
            { region: 'FE', url: 'https://advertising-ai-fe.amazon.com/mcp' },
          ].map(({ region, url }) => (
            <div key={region} className="flex items-center gap-3 px-3 py-2 bg-slate-50 rounded-lg">
              <span className="badge-gray font-mono">{region}</span>
              <code className="text-xs text-slate-600 font-mono">{url}</code>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
