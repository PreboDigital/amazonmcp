/**
 * API Client for Amazon Ads Optimizer backend
 * All data-fetching functions accept an optional credentialId to scope by account.
 */

// Local: Vite proxy forwards /api to localhost:8000. Leave VITE_API_BASE_URL unset.
// Production: Set VITE_API_BASE_URL=https://amazonmcp-backend-production.up.railway.app/api
const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api'

// Auth token getter — set by AuthContext. Requests include Bearer token when available.
let getAuthToken = () => localStorage.getItem('auth_token')

export function setAuthTokenGetter(fn) {
  getAuthToken = fn
}

async function request(path, options = {}) {
  const url = `${API_BASE}${path}`
  const headers = {
    'Content-Type': 'application/json',
    ...options.headers,
  }
  const token = getAuthToken?.()
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  const config = {
    headers,
    ...options,
  }

  try {
    const res = await fetch(url, config)

    // Try to parse as JSON; if the server returned non-JSON (e.g. plain 500), handle gracefully
    let data
    const contentType = res.headers.get('content-type') || ''
    if (contentType.includes('application/json')) {
      data = await res.json()
    } else {
      const text = await res.text()
      if (!res.ok) {
        throw new Error(text || `Request failed: ${res.status}`)
      }
      // Try parsing as JSON anyway (some servers don't set content-type correctly)
      try {
        data = JSON.parse(text)
      } catch {
        throw new Error(text || `Unexpected response format (${res.status})`)
      }
    }

    if (!res.ok) {
      if (res.status === 401 && path !== '/auth/login' && path !== '/auth/register') {
        localStorage.removeItem('auth_token')
        window.dispatchEvent(new Event('auth:logout'))
      }
      throw new Error(data.detail || data.message || `Request failed: ${res.status}`)
    }

    return data
  } catch (err) {
    console.error(`API Error [${path}]:`, err)
    throw err
  }
}

// ── Credentials ──────────────────────────────────────────────────────
export const credentials = {
  list: () => request('/credentials'),
  create: (data) => request('/credentials', { method: 'POST', body: JSON.stringify(data) }),
  update: (id, data) => request(`/credentials/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  delete: (id) => request(`/credentials/${id}`, { method: 'DELETE' }),
  setDefault: (id) => request(`/credentials/${id}/set-default`, { method: 'POST' }),
  test: (id) => request(`/credentials/${id}/test`, { method: 'POST' }),
}

// ── Accounts ─────────────────────────────────────────────────────────
export const accounts = {
  discover: (credentialId) =>
    request(`/accounts/discover${credentialId ? `?credential_id=${credentialId}` : ''}`),
  stored: (credentialId) =>
    request(`/accounts/stored${credentialId ? `?credential_id=${credentialId}` : ''}`),
  setActive: (accountId) =>
    request(`/accounts/set-active/${accountId}`, { method: 'POST' }),
  campaigns: (credentialId) =>
    request(`/accounts/campaigns${credentialId ? `?credential_id=${credentialId}` : ''}`),
  adGroups: (credentialId) =>
    request(`/accounts/ad-groups${credentialId ? `?credential_id=${credentialId}` : ''}`),
  targets: (credentialId) =>
    request(`/accounts/targets${credentialId ? `?credential_id=${credentialId}` : ''}`),
  products: (credentialId) =>
    request(`/accounts/products${credentialId ? `?credential_id=${credentialId}` : ''}`),
  tools: (credentialId) =>
    request(`/accounts/tools${credentialId ? `?credential_id=${credentialId}` : ''}`),
}

// ── Audit ────────────────────────────────────────────────────────────
export const audit = {
  run: (credentialId) => request('/audit/run', {
    method: 'POST',
    body: JSON.stringify({ credential_id: credentialId || null }),
  }),
  report: (type, credentialId) => request('/audit/report', {
    method: 'POST',
    body: JSON.stringify({ report_type: type, credential_id: credentialId || null }),
  }),
  snapshots: (credentialId) =>
    request(`/audit/snapshots${credentialId ? `?credential_id=${credentialId}` : ''}`),
  snapshot: (id) => request(`/audit/snapshots/${id}`),
  deleteSnapshot: (id) => request(`/audit/snapshots/${id}`, { method: 'DELETE' }),
}

// ── Harvest ──────────────────────────────────────────────────────────
export const harvest = {
  configs: (credentialId) =>
    request(`/harvest/configs${credentialId ? `?credential_id=${credentialId}` : ''}`),
  create: (data) => request('/harvest/configs', { method: 'POST', body: JSON.stringify(data) }),
  update: (id, data) => request(`/harvest/configs/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  run: (configId, credentialId, sendToApproval = true) => request('/harvest/run', {
    method: 'POST',
    body: JSON.stringify({
      config_id: configId,
      credential_id: credentialId || null,
      send_to_approval: sendToApproval,
    }),
  }),
  preview: (configId, credentialId) => request('/harvest/preview', {
    method: 'POST',
    body: JSON.stringify({ config_id: configId, credential_id: credentialId || null }),
  }),
  delete: (id) => request(`/harvest/configs/${id}`, { method: 'DELETE' }),
  campaigns: (credentialId, targetingType) => {
    const params = new URLSearchParams()
    if (credentialId) params.set('credential_id', credentialId)
    if (targetingType) params.set('targeting_type', targetingType)
    const qs = params.toString()
    return request(`/harvest/campaigns${qs ? `?${qs}` : ''}`)
  },
  runs: (configId, credentialId) => {
    const params = new URLSearchParams()
    if (configId) params.set('config_id', configId)
    if (credentialId) params.set('credential_id', credentialId)
    const qs = params.toString()
    return request(`/harvest/runs${qs ? `?${qs}` : ''}`)
  },
}

// ── Optimizer ────────────────────────────────────────────────────────
export const optimizer = {
  rules: (credentialId) =>
    request(`/optimizer/rules${credentialId ? `?credential_id=${credentialId}` : ''}`),
  createRule: (data) => request('/optimizer/rules', { method: 'POST', body: JSON.stringify(data) }),
  updateRule: (id, data) => request(`/optimizer/rules/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteRule: (id) => request(`/optimizer/rules/${id}`, { method: 'DELETE' }),
  run: (ruleId, dryRun = true, credentialId) => request('/optimizer/run', {
    method: 'POST',
    body: JSON.stringify({ rule_id: ruleId, dry_run: dryRun, credential_id: credentialId || null }),
  }),
  activity: (credentialId, limit = 50) =>
    request(`/optimizer/activity?limit=${limit}${credentialId ? `&credential_id=${credentialId}` : ''}`),
}

// ── AI ──────────────────────────────────────────────────────────────
export const ai = {
  chat: (message, credentialId, conversationId) => request('/ai/chat', {
    method: 'POST',
    body: JSON.stringify({
      message,
      credential_id: credentialId || null,
      conversation_id: conversationId || null,
    }),
  }),
  insights: (credentialId) => request('/ai/insights', {
    method: 'POST',
    body: JSON.stringify({ credential_id: credentialId || null }),
  }),
  optimize: (credentialId, targetAcos = 30) => request('/ai/optimize', {
    method: 'POST',
    body: JSON.stringify({ credential_id: credentialId || null, target_acos: targetAcos }),
  }),
  buildCampaign: (data) => request('/ai/build-campaign', {
    method: 'POST',
    body: JSON.stringify(data),
  }),
  publishCampaign: (plan, productAsin, credentialId) => request('/ai/publish-campaign', {
    method: 'POST',
    body: JSON.stringify({
      plan,
      product_asin: productAsin,
      credential_id: credentialId || null,
    }),
  }),
  conversations: (credentialId) =>
    request(`/ai/conversations${credentialId ? `?credential_id=${credentialId}` : ''}`),
  conversation: (id) => request(`/ai/conversations/${id}`),
  deleteConversation: (id) => request(`/ai/conversations/${id}`, { method: 'DELETE' }),
}

// ── Approvals ───────────────────────────────────────────────────────
export const approvals = {
  list: (credentialId, status, opts = {}) => {
    const params = new URLSearchParams()
    if (credentialId) params.set('credential_id', credentialId)
    if (status) params.set('status', status)
    if (opts.profile_id) params.set('profile_id', opts.profile_id)
    if (opts.change_type) params.set('change_type', opts.change_type)
    if (opts.source) params.set('source', opts.source)
    if (opts.batch_id) params.set('batch_id', opts.batch_id)
    if (opts.limit) params.set('limit', opts.limit)
    const qs = params.toString()
    return request(`/approvals${qs ? `?${qs}` : ''}`)
  },
  summary: (credentialId, profileId = null) => {
    const params = new URLSearchParams()
    if (credentialId) params.set('credential_id', credentialId)
    if (profileId) params.set('profile_id', profileId)
    const qs = params.toString()
    return request(`/approvals/summary${qs ? `?${qs}` : ''}`)
  },
  get: (id) => request(`/approvals/${id}`),
  create: (data) => request('/approvals', { method: 'POST', body: JSON.stringify(data) }),
  review: (id, action, note) => request(`/approvals/${id}/review`, {
    method: 'POST',
    body: JSON.stringify({ action, review_note: note || null }),
  }),
  batchReview: (changeIds, action, note) => request('/approvals/batch-review', {
    method: 'POST',
    body: JSON.stringify({ change_ids: changeIds, action, review_note: note || null }),
  }),
  apply: (changeIds, batchId) => request('/approvals/apply', {
    method: 'POST',
    body: JSON.stringify({
      change_ids: changeIds || null,
      batch_id: batchId || null,
    }),
  }),
  delete: (id) => request(`/approvals/${id}`, { method: 'DELETE' }),
}

// ── Reports ─────────────────────────────────────────────────────────
export const reports = {
  summary: (credentialId, opts = {}) => {
    const params = new URLSearchParams()
    if (credentialId) params.set('credential_id', credentialId)
    if (opts.preset) params.set('preset', opts.preset)
    if (opts.startDate) params.set('start_date', opts.startDate)
    if (opts.endDate) params.set('end_date', opts.endDate)
    return request(`/reports/summary?${params.toString()}`)
  },
  trends: (credentialId, limit = 30, opts = {}) => {
    const params = new URLSearchParams()
    params.set('limit', limit)
    if (credentialId) params.set('credential_id', credentialId)
    if (opts.preset) params.set('preset', opts.preset)
    if (opts.startDate) params.set('start_date', opts.startDate)
    if (opts.endDate) params.set('end_date', opts.endDate)
    return request(`/reports/trends?${params.toString()}`)
  },
  generate: (credentialId, opts = {}) =>
    request('/reports/generate', {
      method: 'POST',
      body: JSON.stringify({
        credential_id: credentialId || null,
        preset: opts.preset || 'this_month',
        start_date: opts.startDate || null,
        end_date: opts.endDate || null,
        compare: opts.compare,
      }),
    }),
  history: (credentialId, limit = 20) =>
    request(`/reports/history?limit=${limit}${credentialId ? `&credential_id=${credentialId}` : ''}`),
  detail: (id) => request(`/reports/history/${id}`),
  // Search term reports
  searchTermSync: (credentialId, opts = {}) => request('/reports/search-terms/sync', {
    method: 'POST',
    body: JSON.stringify({
      credential_id: credentialId || null,
      start_date: opts.startDate || null,
      end_date: opts.endDate || null,
      ad_product: opts.adProduct || 'SPONSORED_PRODUCTS',
      pending_report_id: opts.pendingReportId || null,
    }),
  }),
  searchTerms: (credentialId, opts = {}) => {
    const params = new URLSearchParams()
    if (credentialId) params.set('credential_id', credentialId)
    if (opts.campaignId) params.set('campaign_id', opts.campaignId)
    if (opts.minClicks) params.set('min_clicks', opts.minClicks)
    if (opts.nonConvertingOnly) params.set('non_converting_only', 'true')
    if (opts.limit) params.set('limit', opts.limit)
    if (opts.sortBy) params.set('sort_by', opts.sortBy)
    const qs = params.toString()
    return request(`/reports/search-terms${qs ? `?${qs}` : ''}`)
  },
  searchTermsSummary: (credentialId) =>
    request(`/reports/search-terms/summary${credentialId ? `?credential_id=${credentialId}` : ''}`),
}

// ── Campaign Management ─────────────────────────────────────────────
export const campaignManager = {
  // Stats
  stats: (credentialId) =>
    request(`/campaigns/stats${credentialId ? `?credential_id=${credentialId}` : ''}`),

  // Sync
  sync: (credentialId) =>
    request(`/campaigns/sync${credentialId ? `?credential_id=${credentialId}` : ''}`, { method: 'POST' }),

  // Campaigns CRUD
  listCampaigns: (credentialId, opts = {}) => {
    const params = new URLSearchParams()
    if (credentialId) params.set('credential_id', credentialId)
    if (opts.state) params.set('state', opts.state)
    if (opts.campaign_type) params.set('campaign_type', opts.campaign_type)
    if (opts.targeting_type) params.set('targeting_type', opts.targeting_type)
    if (opts.search) params.set('search', opts.search)
    if (opts.date_from) params.set('date_from', opts.date_from)
    if (opts.date_to) params.set('date_to', opts.date_to)
    if (opts.preset) params.set('preset', opts.preset)
    if (opts.sort_by) params.set('sort_by', opts.sort_by)
    if (opts.sort_dir) params.set('sort_dir', opts.sort_dir)
    if (opts.page) params.set('page', opts.page)
    if (opts.page_size) params.set('page_size', opts.page_size)
    const qs = params.toString()
    return request(`/campaigns${qs ? `?${qs}` : ''}`)
  },
  createCampaign: (data, credentialId, skipApproval = false) =>
    request(`/campaigns${credentialId ? `?credential_id=${credentialId}` : ''}`, {
      method: 'POST',
      body: JSON.stringify({ campaign_data: data, skip_approval: skipApproval }),
    }),
  updateCampaign: (amazonCampaignId, updates, credentialId, skipApproval = false) =>
    request(`/campaigns/${amazonCampaignId}${credentialId ? `?credential_id=${credentialId}` : ''}`, {
      method: 'PUT',
      body: JSON.stringify({ amazon_campaign_id: amazonCampaignId, updates, skip_approval: skipApproval }),
    }),
  updateCampaignState: (amazonCampaignId, state, credentialId, skipApproval = false) =>
    request(`/campaigns/${amazonCampaignId}/state${credentialId ? `?credential_id=${credentialId}` : ''}`, {
      method: 'POST',
      body: JSON.stringify({ amazon_campaign_id: amazonCampaignId, state, skip_approval: skipApproval }),
    }),
  updateCampaignBudget: (amazonCampaignId, budget, credentialId, skipApproval = false) =>
    request(`/campaigns/${amazonCampaignId}/budget${credentialId ? `?credential_id=${credentialId}` : ''}`, {
      method: 'POST',
      body: JSON.stringify({ amazon_campaign_id: amazonCampaignId, daily_budget: budget, skip_approval: skipApproval }),
    }),
  deleteCampaign: (amazonCampaignId, credentialId, skipApproval = false) =>
    request(`/campaigns/${amazonCampaignId}?skip_approval=${skipApproval}${credentialId ? `&credential_id=${credentialId}` : ''}`, {
      method: 'DELETE',
    }),

  // Ad Groups CRUD
  listAdGroups: (amazonCampaignId, credentialId) =>
    request(`/campaigns/${amazonCampaignId}/ad-groups${credentialId ? `?credential_id=${credentialId}` : ''}`),
  createAdGroup: (amazonCampaignId, data, credentialId, skipApproval = false) =>
    request(`/campaigns/${amazonCampaignId}/ad-groups${credentialId ? `?credential_id=${credentialId}` : ''}`, {
      method: 'POST',
      body: JSON.stringify({ ad_group_data: data, skip_approval: skipApproval }),
    }),
  updateAdGroup: (amazonAdGroupId, updates, credentialId, skipApproval = false) =>
    request(`/campaigns/ad-groups/${amazonAdGroupId}${credentialId ? `?credential_id=${credentialId}` : ''}`, {
      method: 'PUT',
      body: JSON.stringify({ updates, skip_approval: skipApproval }),
    }),
  deleteAdGroup: (amazonAdGroupId, credentialId, skipApproval = false) =>
    request(`/campaigns/ad-groups/${amazonAdGroupId}?skip_approval=${skipApproval}${credentialId ? `&credential_id=${credentialId}` : ''}`, {
      method: 'DELETE',
    }),

  // Targets CRUD
  listTargets: (amazonAdGroupId, credentialId) =>
    request(`/campaigns/ad-groups/${amazonAdGroupId}/targets${credentialId ? `?credential_id=${credentialId}` : ''}`),
  createTarget: (amazonAdGroupId, data, credentialId, skipApproval = false) =>
    request(`/campaigns/ad-groups/${amazonAdGroupId}/targets${credentialId ? `?credential_id=${credentialId}` : ''}`, {
      method: 'POST',
      body: JSON.stringify({ target_data: data, skip_approval: skipApproval }),
    }),
  updateTarget: (amazonTargetId, updates, credentialId, skipApproval = false) =>
    request(`/campaigns/targets/${amazonTargetId}${credentialId ? `?credential_id=${credentialId}` : ''}`, {
      method: 'PUT',
      body: JSON.stringify({ updates, skip_approval: skipApproval }),
    }),
  deleteTarget: (amazonTargetId, credentialId, skipApproval = false) =>
    request(`/campaigns/targets/${amazonTargetId}?skip_approval=${skipApproval}${credentialId ? `&credential_id=${credentialId}` : ''}`, {
      method: 'DELETE',
    }),

  // Ads CRUD
  listAds: (amazonAdGroupId, credentialId) =>
    request(`/campaigns/ad-groups/${amazonAdGroupId}/ads${credentialId ? `?credential_id=${credentialId}` : ''}`),
  createAd: (amazonAdGroupId, data, credentialId, skipApproval = false) =>
    request(`/campaigns/ad-groups/${amazonAdGroupId}/ads${credentialId ? `?credential_id=${credentialId}` : ''}`, {
      method: 'POST',
      body: JSON.stringify({ ad_data: data, skip_approval: skipApproval }),
    }),
  updateAd: (amazonAdId, updates, credentialId, skipApproval = false) =>
    request(`/campaigns/ads/${amazonAdId}${credentialId ? `?credential_id=${credentialId}` : ''}`, {
      method: 'PUT',
      body: JSON.stringify({ updates, skip_approval: skipApproval }),
    }),
  deleteAd: (amazonAdId, credentialId, skipApproval = false) =>
    request(`/campaigns/ads/${amazonAdId}?skip_approval=${skipApproval}${credentialId ? `&credential_id=${credentialId}` : ''}`, {
      method: 'DELETE',
    }),
}

// ── Settings ──────────────────────────────────────────────────────────
export const settingsApi = {
  llm: {
    get: () => request('/settings/llm'),
    update: (data) => request('/settings/llm', {
      method: 'PUT',
      body: JSON.stringify(data),
    }),
  },
  apiKeys: {
    get: () => request('/settings/api-keys'),
    update: (data) => request('/settings/api-keys', {
      method: 'PUT',
      body: JSON.stringify(data),
    }),
  },
}

// ── Auth ─────────────────────────────────────────────────────────────
export const authApi = {
  login: (email, password) =>
    request('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    }),
  register: (token, email, password, name) =>
    request('/auth/register', {
      method: 'POST',
      body: JSON.stringify({ token: token || null, email, password, name: name || null }),
    }),
  whoami: () => request('/auth/whoami'),
}

// ── Users (admin) ─────────────────────────────────────────────────────
export const usersApi = {
  list: () => request('/users'),
  create: (data) => request('/users', { method: 'POST', body: JSON.stringify(data) }),
  update: (id, data) => request(`/users/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  delete: (id) => request(`/users/${id}`, { method: 'DELETE' }),
  invitations: {
    list: () => request('/users/invitations'),
    create: (email, role = 'user') =>
      request('/users/invitations', {
        method: 'POST',
        body: JSON.stringify({ email, role }),
      }),
    revoke: (id) => request(`/users/invitations/${id}`, { method: 'DELETE' }),
  },
}

// ── Health ───────────────────────────────────────────────────────────
export const health = () => request('/health')
