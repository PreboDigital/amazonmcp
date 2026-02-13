import { createContext, useContext, useState, useEffect, useCallback } from 'react'
import { accounts as accountsApi, credentials } from './api'

const AccountContext = createContext(null)

/**
 * AccountProvider loads:
 * 1. Credentials (for auth — client_id, tokens)
 * 2. Discovered profiles (for account switching — the actual Amazon ad accounts)
 *
 * The "active account" is a discovered profile, not a credential.
 * Switching accounts calls /accounts/set-active to update the credential's profile_id
 * so all MCP calls are scoped to that advertiser.
 */
export function AccountProvider({ children }) {
  const [creds, setCreds] = useState([])
  const [discoveredAccounts, setDiscoveredAccounts] = useState([])
  const [activeAccount, setActiveAccount] = useState(null)
  const [loading, setLoading] = useState(true)

  const loadAccounts = useCallback(async () => {
    setLoading(true)
    try {
      // Load credentials and discovered profiles in parallel
      const [credsData, profilesData] = await Promise.allSettled([
        credentials.list(),
        accountsApi.stored(),
      ])

      const credsList = credsData.status === 'fulfilled' ? credsData.value : []
      const profilesList = profilesData.status === 'fulfilled' ? profilesData.value : []
      setCreds(credsList)
      setDiscoveredAccounts(profilesList)

      // Determine the active account
      const storedId = localStorage.getItem('activeAccountId')

      if (profilesList.length > 0) {
        // Use discovered profiles as accounts
        const match =
          profilesList.find((a) => a.id === storedId) ||
          profilesList[0] ||
          null
        setActiveAccount(match)
      } else if (credsList.length > 0) {
        // Fallback: no profiles discovered yet, use credential as placeholder
        const defaultCred = credsList.find((c) => c.is_default) || credsList[0]
        setActiveAccount({
          id: defaultCred.id,
          credential_id: defaultCred.id,
          account_name: defaultCred.name,
          marketplace: defaultCred.region?.toUpperCase(),
          profile_id: null,
          account_type: 'credential',
          account_status: defaultCred.status,
          _isCred: true, // marker so we know this is a credential, not a profile
        })
      } else {
        setActiveAccount(null)
      }
    } catch (err) {
      console.error('Failed to load accounts:', err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadAccounts()
  }, [loadAccounts])

  async function switchAccount(account) {
    // Update backend first so Reports/data pages get the correct profile when they refetch
    if (!account._isCred && account.profile_id) {
      try {
        await accountsApi.setActive(account.id)
      } catch (err) {
        console.error('Failed to set active account:', err)
      }
    }
    setActiveAccount(account)
    localStorage.setItem('activeAccountId', account.id)
  }

  // The credential_id to use for API calls
  // (discovered profiles have a credential_id, fallback accounts use their own id)
  const activeCredentialId = activeAccount?.credential_id || activeAccount?.id || null

  return (
    <AccountContext.Provider
      value={{
        // Credential-level data
        credentials: creds,
        // Discovered profile data (the real accounts)
        accounts: discoveredAccounts.length > 0 ? discoveredAccounts : creds.length > 0 ? [activeAccount].filter(Boolean) : [],
        discoveredAccounts,
        // Active selection
        activeAccount,
        activeAccountId: activeCredentialId,
        activeProfileId: activeAccount?.profile_id || null,
        // Loading
        loading,
        // Actions
        switchAccount,
        refreshAccounts: loadAccounts,
      }}
    >
      {children}
    </AccountContext.Provider>
  )
}

export function useAccount() {
  const ctx = useContext(AccountContext)
  if (!ctx) throw new Error('useAccount must be used within AccountProvider')
  return ctx
}
