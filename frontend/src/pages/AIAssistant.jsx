import { useState, useEffect, useRef } from 'react'
import { Link } from 'react-router-dom'
import {
  Bot,
  Send,
  Loader2,
  Sparkles,
  TrendingUp,
  Lightbulb,
  BarChart3,
  Zap,
  RefreshCw,
  ChevronDown,
  ChevronUp,
  Rocket,
  Target,
  Brain,
  MessageSquarePlus,
  Trash2,
  AlertCircle,
  Shield,
  CheckCircle,
  Package,
} from 'lucide-react'
import { ai, accounts } from '../lib/api'
import { useAccount } from '../lib/AccountContext'

// ── Markdown renderer with table, list, heading, code, and inline support ──
function RenderMarkdown({ text }) {
  if (!text) return null
  const lines = text.split('\n')
  const elements = []
  let i = 0

  while (i < lines.length) {
    const line = lines[i]

    // ── Tables: detect header row like | col1 | col2 | ──
    if (/^\|(.+\|)+\s*$/.test(line.trim())) {
      const tableRows = []
      while (i < lines.length && /^\|(.+\|)+\s*$/.test(lines[i].trim())) {
        const raw = lines[i].trim()
        // Skip separator rows like |---|---|
        if (/^\|[\s:]*-{2,}[\s:]*(\|[\s:]*-{2,}[\s:]*)*\|\s*$/.test(raw)) {
          i++
          continue
        }
        const cells = raw.split('|').filter(Boolean).map(c => c.trim())
        tableRows.push(cells)
        i++
      }
      if (tableRows.length > 0) {
        const headerRow = tableRows[0]
        const bodyRows = tableRows.slice(1)
        const numCols = Math.max(headerRow.length, ...bodyRows.map(r => r.length), 1)
        const paddedHeader = [...headerRow]
        while (paddedHeader.length < numCols) paddedHeader.push('')
        elements.push(
          <div key={`tbl-${i}`} className="my-2 overflow-x-auto rounded-lg border border-slate-200">
            <table className="w-full text-xs table-fixed">
              <colgroup>
                <col style={{ width: 'min(200px, 30%)' }} />
                {Array.from({ length: numCols - 1 }, (_, ci) => (
                  <col key={ci} />
                ))}
              </colgroup>
              <thead>
                <tr className="bg-slate-50 border-b border-slate-200">
                  {paddedHeader.map((cell, ci) => (
                    <th key={ci} className="px-3 py-2 text-left font-semibold text-slate-700 whitespace-nowrap overflow-hidden text-ellipsis min-w-0">
                      {renderInline(cell)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {bodyRows.map((row, ri) => {
                  const cells = [...row]
                  while (cells.length < numCols) cells.push('')
                  return (
                    <tr key={ri} className={ri % 2 === 0 ? 'bg-white' : 'bg-slate-50/50'}>
                      {cells.map((cell, ci) => (
                        <td key={ci} className={`px-3 py-1.5 text-slate-600 min-w-0 overflow-hidden ${ci === 0 ? 'text-ellipsis whitespace-nowrap' : 'whitespace-nowrap'}`}>
                          {renderInline(cell)}
                        </td>
                      ))}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )
      }
      continue // i already advanced past the table
    }

    // ── Headings ──
    if (line.startsWith('#### ')) {
      elements.push(<h5 key={i} className="text-xs font-bold text-slate-700 mt-2 mb-1">{renderInline(line.slice(5))}</h5>)
    } else if (line.startsWith('### ')) {
      elements.push(<h4 key={i} className="text-sm font-bold text-slate-800 mt-3 mb-1">{renderInline(line.slice(4))}</h4>)
    } else if (line.startsWith('## ')) {
      elements.push(<h3 key={i} className="text-base font-bold text-slate-900 mt-4 mb-1.5">{renderInline(line.slice(3))}</h3>)
    } else if (line.startsWith('# ')) {
      elements.push(<h2 key={i} className="text-lg font-bold text-slate-900 mt-4 mb-2">{renderInline(line.slice(2))}</h2>)

    // ── Horizontal rules ──
    } else if (/^[-*_]{3,}\s*$/.test(line.trim())) {
      elements.push(<hr key={i} className="border-slate-200 my-3" />)

    // ── Unordered lists (including indented sub-items) ──
    } else if (/^\s*([-*])\s/.test(line)) {
      const indent = line.match(/^(\s*)/)[1].length
      const content = line.replace(/^\s*[-*]\s/, '')
      elements.push(
        <div key={i} className="flex gap-2 my-0.5" style={{ marginLeft: `${Math.min(indent, 6) * 8 + 4}px` }}>
          <span className="text-brand-500 mt-1.5 shrink-0">•</span>
          <span className="text-sm text-slate-700">{renderInline(content)}</span>
        </div>
      )

    // ── Ordered lists ──
    } else if (/^\s*\d+\.\s/.test(line)) {
      const indent = line.match(/^(\s*)/)[1].length
      const num = line.match(/^\s*(\d+)\.\s/)[1]
      const content = line.replace(/^\s*\d+\.\s/, '')
      elements.push(
        <div key={i} className="flex gap-2 my-0.5" style={{ marginLeft: `${Math.min(indent, 6) * 8 + 4}px` }}>
          <span className="text-brand-600 font-semibold text-sm shrink-0">{num}.</span>
          <span className="text-sm text-slate-700">{renderInline(content)}</span>
        </div>
      )

    // ── Fenced code blocks ──
    } else if (line.startsWith('```')) {
      const codeLines = []
      i++
      while (i < lines.length && !lines[i].startsWith('```')) {
        codeLines.push(lines[i])
        i++
      }
      elements.push(
        <pre key={`code-${i}`} className="bg-slate-800 text-emerald-300 text-xs p-3 rounded-lg my-2 overflow-x-auto">
          <code>{codeLines.join('\n')}</code>
        </pre>
      )

    // ── Blockquotes ──
    } else if (line.startsWith('> ')) {
      elements.push(
        <blockquote key={i} className="border-l-3 border-brand-400 pl-3 my-2 text-sm text-slate-600 italic">
          {renderInline(line.slice(2))}
        </blockquote>
      )

    // ── Empty lines ──
    } else if (line.trim() === '') {
      elements.push(<div key={i} className="h-2" />)

    // ── Default paragraph ──
    } else {
      elements.push(<p key={i} className="text-sm text-slate-700 my-0.5">{renderInline(line)}</p>)
    }
    i++
  }

  return <div className="space-y-0">{elements}</div>
}

function renderInline(text) {
  if (!text) return text
  // Bold, italic, bold-italic, inline code, and links
  return text.split(/(\*\*\*[^*]+\*\*\*|\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/).map((part, i) => {
    if (!part) return null
    // Bold italic ***text***
    if (part.startsWith('***') && part.endsWith('***'))
      return <strong key={i} className="font-semibold text-slate-900 italic">{part.slice(3, -3)}</strong>
    // Bold **text**
    if (part.startsWith('**') && part.endsWith('**'))
      return <strong key={i} className="font-semibold text-slate-900">{part.slice(2, -2)}</strong>
    // Italic *text*
    if (part.startsWith('*') && part.endsWith('*') && part.length > 2)
      return <em key={i} className="italic text-slate-600">{part.slice(1, -1)}</em>
    // Inline code `text`
    if (part.startsWith('`') && part.endsWith('`'))
      return <code key={i} className="text-xs bg-slate-100 text-brand-700 px-1 py-0.5 rounded">{part.slice(1, -1)}</code>
    // Links [text](url)
    const linkMatch = part.match(/^\[([^\]]+)\]\(([^)]+)\)$/)
    if (linkMatch)
      return <a key={i} href={linkMatch[2]} target="_blank" rel="noopener noreferrer" className="text-brand-600 underline hover:text-brand-800">{linkMatch[1]}</a>
    return part
  })
}


// ── Quick Action Cards ───────────────────────────────────────────────
const quickActions = [
  {
    icon: Lightbulb,
    label: 'Generate Insights',
    prompt: 'Analyze my campaign performance and give me actionable insights. What are my biggest opportunities?',
    color: 'amber',
  },
  {
    icon: TrendingUp,
    label: 'Optimize Bids',
    prompt: 'Review my current bids and suggest optimizations to reduce ACOS while maintaining sales volume.',
    color: 'emerald',
    action: 'optimize', // Run AI optimizer directly
  },
  {
    icon: BarChart3,
    label: 'Find Wasted Spend',
    prompt: 'Identify keywords and targets that are wasting money — high spend with no conversions.',
    color: 'red',
  },
  {
    icon: Rocket,
    label: 'Build Campaign',
    prompt: 'Help me plan and build a new Sponsored Products campaign. Walk me through the best structure.',
    color: 'brand',
  },
]

const colorMap = {
  amber: 'bg-amber-50 text-amber-600 group-hover:bg-amber-100',
  emerald: 'bg-emerald-50 text-emerald-600 group-hover:bg-emerald-100',
  red: 'bg-red-50 text-red-600 group-hover:bg-red-100',
  brand: 'bg-brand-50 text-brand-600 group-hover:bg-brand-100',
}


export default function AIAssistant() {
  const { activeAccount, activeAccountId } = useAccount()
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [conversationId, setConversationId] = useState(null)
  const [conversations, setConversations] = useState([])
  const [showHistory, setShowHistory] = useState(false)
  const [insightsLoading, setInsightsLoading] = useState(false)
  const [insights, setInsights] = useState(null)
  const [error, setError] = useState(null)
  const [successMsg, setSuccessMsg] = useState(null)
  const messagesEndRef = useRef(null)
  const inputRef = useRef(null)

  // Campaign Builder
  const [showCampaignBuilder, setShowCampaignBuilder] = useState(false)
  const [campaignBuilder, setCampaignBuilder] = useState({
    productName: '',
    productAsin: '',
    productCategory: '',
    dailyBudget: 50,
    targetAcos: 30,
    keywords: '',
  })
  const [products, setProducts] = useState([])
  const [campaignPlan, setCampaignPlan] = useState(null)
  const [campaignPlanLoading, setCampaignPlanLoading] = useState(false)
  const [publishLoading, setPublishLoading] = useState(false)

  // AI Optimizer
  const [optimizeLoading, setOptimizeLoading] = useState(false)
  const [optimizeResult, setOptimizeResult] = useState(null)

  useEffect(() => {
    setMessages([])
    setConversationId(null)
    setInsights(null)
    setOptimizeResult(null)
    loadConversations()
  }, [activeAccountId])

  useEffect(() => {
    if (activeAccountId) {
      accounts.products(activeAccountId).then(setProducts).catch(() => setProducts([]))
    } else {
      setProducts([])
    }
  }, [activeAccountId])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  async function loadConversations() {
    try {
      const data = await ai.conversations(activeAccountId)
      setConversations(data)
    } catch (err) { /* ignore */ }
  }

  async function sendMessage(text) {
    const msg = text || input.trim()
    if (!msg || loading) return
    setInput('')
    setError(null)

    const userMsg = { role: 'user', content: msg, timestamp: new Date().toISOString() }
    setMessages(prev => [...prev, userMsg])
    setLoading(true)

    try {
      const result = await ai.chat(msg, activeAccountId, conversationId)
      setConversationId(result.conversation_id)
      const assistantMsg = { role: 'assistant', content: result.message, timestamp: new Date().toISOString() }
      setMessages(prev => [...prev, assistantMsg])
    } catch (err) {
      setError(err.message)
      setMessages(prev => [...prev, { role: 'assistant', content: `Sorry, I encountered an error: ${err.message}`, timestamp: new Date().toISOString(), isError: true }])
    } finally {
      setLoading(false)
      inputRef.current?.focus()
    }
  }

  async function loadConversation(convId) {
    try {
      const data = await ai.conversation(convId)
      setConversationId(convId)
      setMessages(data.messages || [])
      setShowHistory(false)
    } catch (err) {
      setError(err.message)
    }
  }

  function startNewChat() {
    setMessages([])
    setConversationId(null)
    setError(null)
    inputRef.current?.focus()
  }

  async function generateInsights() {
    if (!activeAccountId || insightsLoading) return
    setInsightsLoading(true)
    setError(null)
    try {
      const data = await ai.insights(activeAccountId)
      setInsights(data)
    } catch (err) {
      setError(err.message)
    } finally {
      setInsightsLoading(false)
    }
  }

  async function generateCampaignPlan() {
    if (!activeAccountId || !campaignBuilder.productName.trim() || campaignPlanLoading) return
    setCampaignPlanLoading(true)
    setError(null)
    setCampaignPlan(null)
    try {
      const data = await ai.buildCampaign({
        credential_id: activeAccountId,
        product_name: campaignBuilder.productName.trim(),
        product_asin: campaignBuilder.productAsin.trim() || null,
        product_category: campaignBuilder.productCategory.trim() || null,
        daily_budget: campaignBuilder.dailyBudget,
        target_acos: campaignBuilder.targetAcos,
        campaign_type: 'SPONSORED_PRODUCTS',
        targeting_type: 'manual',
        keywords: campaignBuilder.keywords
          ? campaignBuilder.keywords.split(/[\n,]/).map((k) => k.trim()).filter(Boolean)
          : null,
      })
      setCampaignPlan(data)
    } catch (err) {
      setError(err.message)
    } finally {
      setCampaignPlanLoading(false)
    }
  }

  async function publishCampaign() {
    if (!campaignPlan || !campaignBuilder.productAsin.trim() || publishLoading) return
    setPublishLoading(true)
    setError(null)
    try {
      const data = await ai.publishCampaign(
        campaignPlan,
        campaignBuilder.productAsin.trim(),
        activeAccountId
      )
      setSuccessMsg(`Campaign sent to Approval Queue. Review and approve to publish.`)
      setTimeout(() => setSuccessMsg(null), 5000)
      setCampaignPlan(null)
      setShowCampaignBuilder(false)
    } catch (err) {
      setError(err.message)
    } finally {
      setPublishLoading(false)
    }
  }

  async function runOptimize() {
    if (!activeAccountId || optimizeLoading) return
    setOptimizeLoading(true)
    setError(null)
    setOptimizeResult(null)
    try {
      const data = await ai.optimize(activeAccountId, 30)
      setOptimizeResult(data)
      setSuccessMsg(`${data.changes_created} changes sent to Approval Queue`)
      setTimeout(() => setSuccessMsg(null), 5000)
    } catch (err) {
      setError(err.message)
    } finally {
      setOptimizeLoading(false)
    }
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  }

  const hasMessages = messages.length > 0

  return (
    <div className="h-[calc(100vh-8rem)] flex gap-6">
      {/* Main Chat Area */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Header */}
        <div className="flex items-center justify-between mb-4 shrink-0">
          <div className="flex items-center gap-3">
            <div className="flex items-center justify-center w-10 h-10 rounded-xl bg-gradient-to-br from-brand-500 to-purple-600 text-white">
              <Brain size={20} />
            </div>
            <div>
              <h1 className="text-xl font-bold text-slate-900 tracking-tight">AI Assistant</h1>
              <p className="text-xs text-slate-500">
                {activeAccount
                  ? <>Analyzing <span className="font-medium text-slate-700">{activeAccount.account_name || activeAccount.name}</span>{activeAccount.marketplace ? <> &middot; {activeAccount.marketplace}</> : ''}</>
                  : 'Powered by GPT-4o — insights, optimization, campaign building'}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={startNewChat} className="btn-secondary text-xs">
              <MessageSquarePlus size={14} /> New Chat
            </button>
            <button onClick={() => setShowHistory(!showHistory)} className="btn-ghost text-xs">
              {showHistory ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
              History
            </button>
          </div>
        </div>

        {/* Conversation History Dropdown */}
        {showHistory && (
          <div className="card mb-4 max-h-48 overflow-y-auto divide-y divide-slate-100 shrink-0">
            {conversations.length === 0 ? (
              <div className="p-4 text-center text-sm text-slate-400">No previous conversations</div>
            ) : conversations.map(conv => (
              <button
                key={conv.id}
                onClick={() => loadConversation(conv.id)}
                className={`w-full text-left px-4 py-3 hover:bg-slate-50 transition-colors ${
                  conversationId === conv.id ? 'bg-brand-50' : ''
                }`}
              >
                <p className="text-sm font-medium text-slate-700 truncate">{conv.title}</p>
                <p className="text-xs text-slate-400 mt-0.5">
                  {conv.message_count} messages &middot; {new Date(conv.updated_at).toLocaleDateString()}
                </p>
              </button>
            ))}
          </div>
        )}

        {!activeAccount && (
          <div className="card bg-amber-50 border-amber-200 p-4 text-sm text-amber-800 mb-4 shrink-0">
            <div className="flex items-center gap-2">
              <AlertCircle size={16} />
              Add and select an account in Settings to use the AI Assistant.
            </div>
          </div>
        )}

        {error && (
          <div className="card bg-red-50 border-red-200 p-4 text-sm text-red-800 mb-4 shrink-0 flex items-center justify-between">
            <span>{error}</span>
            <button onClick={() => setError(null)} className="text-red-400 hover:text-red-600">×</button>
          </div>
        )}

        {successMsg && (
          <div className="card bg-emerald-50 border-emerald-200 p-4 text-sm text-emerald-800 mb-4 shrink-0 flex items-center gap-2">
            <CheckCircle size={18} />
            {successMsg}
            {optimizeResult?.changes_created > 0 && (
              <Link to="/approvals" className="ml-2 text-emerald-600 font-medium underline">Review in Approval Queue →</Link>
            )}
          </div>
        )}

        {/* Campaign Builder */}
        {showCampaignBuilder && activeAccount && (
          <div className="card mb-4 p-4 shrink-0 border-brand-200 bg-brand-50/30">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-semibold text-slate-900 flex items-center gap-2">
                <Rocket size={16} className="text-brand-600" />
                Create Campaign
              </h3>
              <button onClick={() => { setShowCampaignBuilder(false); setCampaignPlan(null); setError(null) }} className="text-slate-400 hover:text-slate-600 text-xs">Close</button>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-4">
              <div>
                <label className="block text-xs font-medium text-slate-600 mb-1">Product Name *</label>
                <input
                  type="text"
                  value={campaignBuilder.productName}
                  onChange={(e) => setCampaignBuilder((p) => ({ ...p, productName: e.target.value }))}
                  placeholder="e.g. Wireless Bluetooth Headphones"
                  className="w-full px-3 py-2 text-sm border border-slate-200 rounded-lg"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-600 mb-1">Product ASIN *</label>
                {products.length > 0 ? (
                  <select
                    value={campaignBuilder.productAsin}
                    onChange={(e) => setCampaignBuilder((p) => ({ ...p, productAsin: e.target.value }))}
                    className="w-full px-3 py-2 text-sm border border-slate-200 rounded-lg"
                  >
                    <option value="">— Select from your ads —</option>
                    {products.map((p) => (
                      <option key={p.asin} value={p.asin}>{p.asin} {p.ad_name ? `— ${p.ad_name}` : ''}</option>
                    ))}
                  </select>
                ) : null}
                <input
                  type="text"
                  placeholder={products.length ? 'Or type ASIN' : 'e.g. B08XYZ123'}
                  value={campaignBuilder.productAsin}
                  onChange={(e) => setCampaignBuilder((p) => ({ ...p, productAsin: e.target.value }))}
                  className={`mt-1 w-full px-3 py-2 text-sm border border-slate-200 rounded-lg ${products.length ? '' : 'mt-0'}`}
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-600 mb-1">Daily Budget ($)</label>
                <input
                  type="number"
                  min={1}
                  step={1}
                  value={campaignBuilder.dailyBudget}
                  onChange={(e) => setCampaignBuilder((p) => ({ ...p, dailyBudget: +e.target.value || 50 }))}
                  className="w-full px-3 py-2 text-sm border border-slate-200 rounded-lg"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-600 mb-1">Target ACOS (%)</label>
                <input
                  type="number"
                  min={1}
                  max={100}
                  value={campaignBuilder.targetAcos}
                  onChange={(e) => setCampaignBuilder((p) => ({ ...p, targetAcos: +e.target.value || 30 }))}
                  className="w-full px-3 py-2 text-sm border border-slate-200 rounded-lg"
                />
              </div>
              <div className="sm:col-span-2">
                <label className="block text-xs font-medium text-slate-600 mb-1">Seed Keywords (optional, comma-separated)</label>
                <input
                  type="text"
                  value={campaignBuilder.keywords}
                  onChange={(e) => setCampaignBuilder((p) => ({ ...p, keywords: e.target.value }))}
                  placeholder="wireless headphones, bluetooth headphones, over ear"
                  className="w-full px-3 py-2 text-sm border border-slate-200 rounded-lg"
                />
              </div>
            </div>
            <div className="flex gap-2">
              <button
                onClick={generateCampaignPlan}
                disabled={!campaignBuilder.productName.trim() || campaignPlanLoading}
                className="btn-primary text-xs"
              >
                {campaignPlanLoading ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
                {campaignPlanLoading ? 'Generating...' : 'Generate Plan'}
              </button>
              {campaignPlan && (
                <button
                  onClick={publishCampaign}
                  disabled={!campaignBuilder.productAsin.trim() || publishLoading}
                  className="btn-secondary text-xs"
                >
                  {publishLoading ? <Loader2 size={14} className="animate-spin" /> : <Shield size={14} />}
                  {publishLoading ? 'Publishing...' : 'Publish to Approval'}
                </button>
              )}
            </div>
            {campaignPlan && (
              <div className="mt-4 p-3 bg-white rounded-lg border border-slate-200 text-xs">
                <p className="font-semibold text-slate-700 mb-1">
                  {campaignPlan.campaign_plan?.name || 'Campaign'} — ${campaignPlan.campaign_plan?.daily_budget || 50}/day
                </p>
                <p className="text-slate-600 mb-2">{campaignPlan.campaign_plan?.rationale}</p>
                <p className="text-slate-500">
                  {campaignPlan.ad_groups?.length || 0} ad group(s), {campaignPlan.ad_groups?.reduce((n, ag) => n + (ag.keywords?.length || 0), 0) || 0} keywords
                </p>
              </div>
            )}
          </div>
        )}

        {/* Chat messages or empty state */}
        <div className="flex-1 overflow-y-auto min-h-0">
          {!hasMessages ? (
            <div className="h-full flex flex-col items-center justify-center px-4">
              <div className="flex items-center justify-center w-16 h-16 rounded-2xl bg-gradient-to-br from-brand-100 to-purple-100 mb-6">
                <Sparkles size={28} className="text-brand-600" />
              </div>
              <h2 className="text-lg font-semibold text-slate-900 mb-1">How can I help optimize your ads?</h2>
              <p className="text-sm text-slate-500 mb-8 text-center max-w-md">
                Ask me anything about your Amazon campaigns — performance analysis, bid recommendations, keyword strategy, or campaign planning.
              </p>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 w-full max-w-xl">
                {quickActions.map((action) => (
                  <button
                    key={action.label}
                    onClick={() => {
                      if (action.label === 'Build Campaign') setShowCampaignBuilder(true)
                      else if (action.action === 'optimize') runOptimize()
                      else sendMessage(action.prompt)
                    }}
                    disabled={!activeAccount || loading || (action.action === 'optimize' && optimizeLoading)}
                    className="group card p-4 text-left hover:border-brand-200 hover:shadow-md transition-all disabled:opacity-50"
                  >
                    <div className="flex items-start gap-3">
                      <div className={`flex items-center justify-center w-9 h-9 rounded-lg shrink-0 transition-colors ${colorMap[action.color]}`}>
                        <action.icon size={18} />
                      </div>
                      <div>
                        <p className="text-sm font-medium text-slate-800">{action.label}</p>
                        <p className="text-xs text-slate-400 mt-0.5 line-clamp-2">
                          {action.label === 'Build Campaign'
                            ? 'Create a full campaign with products and keywords. Publish to approval queue.'
                            : `${action.prompt.slice(0, 60)}...`}
                        </p>
                      </div>
                    </div>
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="space-y-4 py-2">
              {messages.map((msg, i) => (
                <div key={i} className={`flex gap-3 ${msg.role === 'user' ? 'justify-end' : ''}`}>
                  {msg.role === 'assistant' && (
                    <div className={`flex items-center justify-center w-8 h-8 rounded-lg shrink-0 mt-0.5 ${
                      msg.isError ? 'bg-red-100 text-red-600' : 'bg-gradient-to-br from-brand-100 to-purple-100 text-brand-600'
                    }`}>
                      <Bot size={16} />
                    </div>
                  )}
                  <div className={`max-w-[80%] ${
                    msg.role === 'user'
                      ? 'bg-brand-600 text-white rounded-2xl rounded-tr-sm px-4 py-3'
                      : msg.isError
                        ? 'bg-red-50 border border-red-200 rounded-2xl rounded-tl-sm px-4 py-3'
                        : 'bg-white border border-slate-200 rounded-2xl rounded-tl-sm px-4 py-3 shadow-sm'
                  }`}>
                    {msg.role === 'user' ? (
                      <p className="text-sm">{msg.content}</p>
                    ) : (
                      <RenderMarkdown text={msg.content} />
                    )}
                  </div>
                </div>
              ))}
              {loading && (
                <div className="flex gap-3">
                  <div className="flex items-center justify-center w-8 h-8 rounded-lg shrink-0 bg-gradient-to-br from-brand-100 to-purple-100 text-brand-600">
                    <Bot size={16} />
                  </div>
                  <div className="bg-white border border-slate-200 rounded-2xl rounded-tl-sm px-4 py-3 shadow-sm">
                    <div className="flex items-center gap-2 text-sm text-slate-500">
                      <Loader2 size={14} className="animate-spin" />
                      Thinking...
                    </div>
                  </div>
                </div>
              )}
              <div ref={messagesEndRef} />
            </div>
          )}
        </div>

        {/* Input */}
        <div className="mt-4 shrink-0">
          <div className="relative">
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={activeAccount ? "Ask about your campaigns..." : "Select an account first"}
              disabled={!activeAccount || loading}
              rows={1}
              className="w-full px-4 py-3.5 pr-14 text-sm bg-white border border-slate-200 rounded-xl
                       placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-brand-500
                       focus:border-brand-500 transition-all duration-150 resize-none disabled:opacity-50"
              style={{ minHeight: '48px', maxHeight: '120px' }}
              onInput={(e) => { e.target.style.height = 'auto'; e.target.style.height = e.target.scrollHeight + 'px' }}
            />
            <button
              onClick={() => sendMessage()}
              disabled={!input.trim() || loading || !activeAccount}
              className="absolute right-2 bottom-2 p-2 rounded-lg bg-brand-600 text-white
                       hover:bg-brand-700 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
            >
              <Send size={16} />
            </button>
          </div>
          <p className="text-[10px] text-slate-400 mt-1.5 text-center">
            AI can make mistakes. Always review recommendations before applying to your campaigns.
          </p>
        </div>
      </div>

      {/* Right Panel — Insights */}
      <div className="hidden xl:flex xl:w-80 flex-col shrink-0">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-semibold text-slate-900 flex items-center gap-2">
            <Zap size={14} className="text-amber-500" />
            AI Insights
          </h2>
          <button
            onClick={generateInsights}
            disabled={!activeAccount || insightsLoading}
            className="btn-ghost text-xs"
          >
            {insightsLoading ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
            {insights ? 'Refresh' : 'Generate'}
          </button>
        </div>

        <div className="flex-1 overflow-y-auto space-y-3">
          {/* Run Optimizer — sends changes to approval queue */}
          {activeAccount && (
            <div className="card p-4">
              <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">AI Optimizer</p>
              <p className="text-xs text-slate-600 mb-2">
                Generate bid and budget recommendations. All changes go to the Approval Queue for your review.
              </p>
              <button
                onClick={runOptimize}
                disabled={optimizeLoading}
                className="btn-primary w-full text-xs"
              >
                {optimizeLoading ? <Loader2 size={14} className="animate-spin mx-auto" /> : <TrendingUp size={14} className="inline mr-1" />}
                {optimizeLoading ? 'Analyzing...' : 'Run AI Optimizer'}
              </button>
              {optimizeResult?.changes_created > 0 && (
                <Link to="/approvals" className="mt-2 block text-xs text-brand-600 font-medium text-center hover:underline">
                  {optimizeResult.changes_created} changes in Approval Queue →
                </Link>
              )}
            </div>
          )}

          {!insights && !insightsLoading ? (
            <div className="card p-6 text-center">
              <Lightbulb size={24} className="mx-auto text-slate-300 mb-3" />
              <p className="text-sm text-slate-500">
                {activeAccount
                  ? 'Click Generate to get AI-powered insights about your campaigns.'
                  : 'Select an account to generate insights.'}
              </p>
            </div>
          ) : insightsLoading ? (
            <div className="card p-6 text-center">
              <Loader2 size={24} className="mx-auto text-brand-500 animate-spin mb-3" />
              <p className="text-sm text-slate-500">Analyzing your campaigns...</p>
            </div>
          ) : insights ? (
            <>
              {/* Health Score */}
              <div className="card p-4">
                <div className="flex items-center justify-between mb-2">
                  <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Health Score</p>
                  <div className={`text-2xl font-bold ${
                    (insights.health_score || 0) >= 70 ? 'text-emerald-600' :
                    (insights.health_score || 0) >= 40 ? 'text-amber-600' : 'text-red-600'
                  }`}>
                    {insights.health_score || 0}
                  </div>
                </div>
                <div className="w-full bg-slate-100 rounded-full h-2">
                  <div
                    className={`h-2 rounded-full transition-all ${
                      (insights.health_score || 0) >= 70 ? 'bg-emerald-500' :
                      (insights.health_score || 0) >= 40 ? 'bg-amber-500' : 'bg-red-500'
                    }`}
                    style={{ width: `${insights.health_score || 0}%` }}
                  />
                </div>
                <p className="text-xs text-slate-500 mt-2">{insights.summary}</p>
              </div>

              {/* Quick Wins */}
              {insights.quick_wins?.length > 0 && (
                <div className="card p-4">
                  <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">Quick Wins</p>
                  <div className="space-y-2">
                    {insights.quick_wins.map((win, i) => (
                      <div key={i} className="flex items-start gap-2">
                        <Zap size={12} className="text-amber-500 mt-0.5 shrink-0" />
                        <div>
                          <p className="text-xs font-medium text-slate-700">{win.action}</p>
                          <p className="text-[10px] text-slate-400">{win.impact}</p>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Insights List */}
              {insights.insights?.map((insight, i) => (
                <div key={i} className={`card p-4 border-l-3 ${
                  insight.priority === 'high' ? 'border-l-red-500' :
                  insight.priority === 'medium' ? 'border-l-amber-500' : 'border-l-blue-500'
                }`}>
                  <div className="flex items-start justify-between gap-2 mb-1">
                    <p className="text-xs font-semibold text-slate-800">{insight.title}</p>
                    <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded-full ${
                      insight.priority === 'high' ? 'bg-red-50 text-red-700' :
                      insight.priority === 'medium' ? 'bg-amber-50 text-amber-700' : 'bg-blue-50 text-blue-700'
                    }`}>
                      {insight.priority}
                    </span>
                  </div>
                  <p className="text-[11px] text-slate-600 mb-1.5">{insight.description}</p>
                  {insight.recommendation && (
                    <p className="text-[11px] text-brand-700 font-medium">
                      → {insight.recommendation}
                    </p>
                  )}
                </div>
              ))}
            </>
          ) : null}
        </div>
      </div>
    </div>
  )
}
