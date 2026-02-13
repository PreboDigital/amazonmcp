import { NavLink, useNavigate } from 'react-router-dom'
import {
  LayoutDashboard,
  SearchCheck,
  Sparkles,
  TrendingUp,
  Settings,
  Zap,
  ChevronDown,
  Building2,
  Check,
  Brain,
  Shield,
  Plus,
  RefreshCw,
  Loader2,
  BarChart3,
} from 'lucide-react'
import clsx from 'clsx'
import { useState, useRef, useEffect } from 'react'
import { useAccount } from '../lib/AccountContext'
import { approvals, accounts as accountsApi } from '../lib/api'

const navigation = [
  { name: 'Dashboard', href: '/', icon: LayoutDashboard },
  { name: 'Campaigns', href: '/campaigns', icon: Zap },
  { name: 'AI Assistant', href: '/ai', icon: Brain },
  { name: 'Reports', href: '/reports', icon: BarChart3 },
  { name: 'Approval Queue', href: '/approvals', icon: Shield },
  { name: 'Audit & Reports', href: '/audit', icon: SearchCheck },
  { name: 'Keyword Harvester', href: '/harvester', icon: Sparkles },
  { name: 'Bid Optimizer', href: '/optimizer', icon: TrendingUp },
  { name: 'Settings', href: '/settings', icon: Settings },
]

const COUNTRY_FLAGS = {
  US: '🇺🇸', CA: '🇨🇦', MX: '🇲🇽', BR: '🇧🇷',
  GB: '🇬🇧', DE: '🇩🇪', FR: '🇫🇷', IT: '🇮🇹', ES: '🇪🇸',
  NL: '🇳🇱', SE: '🇸🇪', PL: '🇵🇱', BE: '🇧🇪', TR: '🇹🇷',
  AE: '🇦🇪', SA: '🇸🇦', EG: '🇪🇬', ZA: '🇿🇦',
  JP: '🇯🇵', AU: '🇦🇺', SG: '🇸🇬', IN: '🇮🇳',
}

function AccountSwitcher({ variant = 'sidebar' }) {
  const { discoveredAccounts, accounts, activeAccount, switchAccount, refreshAccounts } = useAccount()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [resyncing, setResyncing] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    function onClickOutside(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', onClickOutside)
    return () => document.removeEventListener('mousedown', onClickOutside)
  }, [])

  async function handleResync() {
    setResyncing(true)
    try {
      await accountsApi.discover()
      await refreshAccounts()
    } catch { /* ignore */ }
    setResyncing(false)
  }

  if (!activeAccount && accounts.length === 0) return null

  const isSidebar = variant === 'sidebar'
  const displayName = activeAccount?.account_name || activeAccount?.name || 'Select Account'
  const flag = COUNTRY_FLAGS[activeAccount?.marketplace] || ''
  const subtitle = activeAccount?.marketplace
    ? `${activeAccount.marketplace} · ${activeAccount.account_status || 'active'}`
    : activeAccount?.region?.toUpperCase() || ''

  // Group discovered accounts by parent name
  const displayList = discoveredAccounts.length > 0 ? discoveredAccounts : accounts

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(!open)}
        className={clsx(
          'w-full flex items-center gap-2.5 rounded-lg text-left transition-all',
          isSidebar
            ? 'px-3 py-2.5 hover:bg-slate-800'
            : 'px-3 py-2 hover:bg-slate-100'
        )}
      >
        <div className={clsx(
          'flex items-center justify-center w-8 h-8 rounded-lg text-xs font-bold shrink-0',
          isSidebar ? 'bg-brand-600/30 text-brand-300' : 'bg-brand-100 text-brand-700'
        )}>
          {flag || displayName.charAt(0)?.toUpperCase() || '?'}
        </div>
        <div className="flex-1 min-w-0">
          <p className={clsx(
            'text-sm font-medium truncate',
            isSidebar ? 'text-white' : 'text-slate-800'
          )}>
            {displayName}
          </p>
          <p className={clsx(
            'text-[10px] uppercase tracking-wider truncate',
            isSidebar ? 'text-slate-400' : 'text-slate-400'
          )}>
            {subtitle}
          </p>
        </div>
        <ChevronDown size={14} className={clsx(
          'shrink-0 transition-transform',
          open && 'rotate-180',
          isSidebar ? 'text-slate-400' : 'text-slate-400'
        )} />
      </button>

      {open && (
        <div className={clsx(
          'absolute z-50 mt-1 w-full rounded-lg shadow-xl border overflow-hidden max-h-96 overflow-y-auto',
          isSidebar
            ? 'bg-slate-800 border-slate-700'
            : 'bg-white border-slate-200'
        )}>
          <div className={clsx(
            'px-3 py-2 text-[10px] font-semibold uppercase tracking-wider',
            isSidebar ? 'text-slate-500' : 'text-slate-400'
          )}>
            Switch Account ({displayList.length})
          </div>
          {displayList.map((acct) => {
            const acctName = acct.account_name || acct.name || 'Unknown'
            const acctFlag = COUNTRY_FLAGS[acct.marketplace] || ''
            const isActive = activeAccount?.id === acct.id

            return (
              <button
                key={acct.id}
                onClick={() => { switchAccount(acct); setOpen(false) }}
                className={clsx(
                  'w-full flex items-center gap-2.5 px-3 py-2.5 text-left transition-colors',
                  isSidebar
                    ? 'hover:bg-slate-700/50'
                    : 'hover:bg-slate-50',
                  isActive && (isSidebar ? 'bg-slate-700/30' : 'bg-brand-50')
                )}
              >
                <div className={clsx(
                  'flex items-center justify-center w-7 h-7 rounded-md text-xs font-bold shrink-0',
                  isActive
                    ? 'bg-brand-600 text-white'
                    : isSidebar ? 'bg-slate-700 text-slate-300' : 'bg-slate-100 text-slate-500'
                )}>
                  {acctFlag || acctName.charAt(0)?.toUpperCase()}
                </div>
                <div className="flex-1 min-w-0">
                  <p className={clsx(
                    'text-sm font-medium truncate',
                    isSidebar ? 'text-slate-200' : 'text-slate-700'
                  )}>
                    {acctName}
                  </p>
                  <p className={clsx(
                    'text-[10px] truncate',
                    isSidebar ? 'text-slate-500' : 'text-slate-400'
                  )}>
                    {[acct.marketplace, acct.account_type, acct.account_status].filter(Boolean).join(' · ')}
                  </p>
                </div>
                {isActive && (
                  <Check size={14} className="text-brand-500 shrink-0" />
                )}
              </button>
            )
          })}

          {/* Divider + actions */}
          <div className={clsx(
            'border-t',
            isSidebar ? 'border-slate-700' : 'border-slate-200'
          )}>
            <button
              onClick={() => { setOpen(false); handleResync() }}
              disabled={resyncing}
              className={clsx(
                'w-full flex items-center gap-2.5 px-3 py-2.5 text-left transition-colors',
                isSidebar ? 'hover:bg-slate-700/50' : 'hover:bg-slate-50'
              )}
            >
              {resyncing
                ? <Loader2 size={14} className={clsx('animate-spin shrink-0', isSidebar ? 'text-slate-400' : 'text-slate-400')} />
                : <RefreshCw size={14} className={clsx('shrink-0', isSidebar ? 'text-slate-400' : 'text-slate-400')} />
              }
              <span className={clsx('text-xs font-medium', isSidebar ? 'text-slate-400' : 'text-slate-500')}>
                Re-sync Accounts
              </span>
            </button>
            <button
              onClick={() => { setOpen(false); navigate('/settings') }}
              className={clsx(
                'w-full flex items-center gap-2.5 px-3 py-2.5 text-left transition-colors',
                isSidebar ? 'hover:bg-slate-700/50' : 'hover:bg-slate-50'
              )}
            >
              <Plus size={14} className={clsx('shrink-0', isSidebar ? 'text-brand-400' : 'text-brand-500')} />
              <span className={clsx('text-xs font-medium', isSidebar ? 'text-brand-400' : 'text-brand-600')}>
                Add New Credentials
              </span>
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

export default function Layout({ children }) {
  const { activeAccount, activeAccountId, activeProfileId } = useAccount()
  const [pendingCount, setPendingCount] = useState(0)

  useEffect(() => {
    let interval
    async function loadPending() {
      try {
        const data = await approvals.summary(activeAccountId, activeProfileId)
        setPendingCount(data?.total_pending || 0)
      } catch { /* ignore */ }
    }
    loadPending()
    interval = setInterval(loadPending, 30000) // Refresh every 30s
    return () => clearInterval(interval)
  }, [activeAccountId, activeProfileId])

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Sidebar */}
      <aside className="hidden lg:flex lg:flex-col lg:w-64 bg-slate-900">
        {/* Logo */}
        <div className="flex items-center gap-3 h-16 px-6 border-b border-slate-700/50">
          <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-brand-600">
            <Zap className="w-4.5 h-4.5 text-white" size={18} />
          </div>
          <div>
            <h1 className="text-sm font-semibold text-white tracking-tight">Ads Optimizer</h1>
            <p className="text-[10px] text-slate-400 uppercase tracking-wider">Amazon MCP</p>
          </div>
        </div>

        {/* Account Switcher */}
        <div className="px-3 py-3 border-b border-slate-700/50">
          <AccountSwitcher variant="sidebar" />
        </div>

        {/* Navigation */}
        <nav className="flex-1 px-3 py-4 space-y-1 overflow-y-auto">
          {navigation.map((item) => (
            <NavLink
              key={item.name}
              to={item.href}
              end={item.href === '/'}
              className={({ isActive }) =>
                clsx(
                  'flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all duration-150',
                  isActive
                    ? 'bg-brand-600/20 text-brand-400'
                    : 'text-slate-400 hover:text-white hover:bg-slate-800'
                )
              }
            >
              <item.icon size={18} />
              <span className="flex-1">{item.name}</span>
              {item.name === 'Approval Queue' && pendingCount > 0 && (
                <span className="flex items-center justify-center min-w-[20px] h-5 px-1.5 text-[10px] font-bold bg-amber-500 text-white rounded-full">
                  {pendingCount}
                </span>
              )}
            </NavLink>
          ))}
        </nav>

        {/* Footer */}
        <div className="p-4 border-t border-slate-700/50">
          <div className="flex items-center gap-2 px-2">
            <div className={clsx(
              'w-2 h-2 rounded-full',
              activeAccount ? 'bg-emerald-400 animate-pulse' : 'bg-slate-500'
            )} />
            <span className="text-xs text-slate-400">
              {activeAccount ? 'MCP Server Ready' : 'No Account Selected'}
            </span>
          </div>
        </div>
      </aside>

      {/* Mobile header */}
      <div className="lg:hidden fixed top-0 left-0 right-0 z-50 h-14 bg-slate-900 flex items-center px-4 gap-3">
        <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-brand-600">
          <Zap className="text-white" size={14} />
        </div>
        <div className="flex-1">
          <AccountSwitcher variant="mobile" />
        </div>
      </div>

      {/* Mobile nav */}
      <nav className="lg:hidden fixed bottom-0 left-0 right-0 z-50 bg-white border-t border-slate-200 flex">
        {navigation.map((item) => (
          <NavLink
            key={item.name}
            to={item.href}
            end={item.href === '/'}
            className={({ isActive }) =>
              clsx(
                'flex-1 flex flex-col items-center py-2 text-[10px] font-medium transition-colors',
                isActive ? 'text-brand-600' : 'text-slate-400'
              )
            }
          >
            <item.icon size={18} />
            <span className="mt-0.5">{item.name.split(' ')[0]}</span>
          </NavLink>
        ))}
      </nav>

      {/* Main content */}
      <main className="flex-1 overflow-y-auto lg:pt-0 pt-14 pb-20 lg:pb-0">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
          {children}
        </div>
      </main>
    </div>
  )
}
