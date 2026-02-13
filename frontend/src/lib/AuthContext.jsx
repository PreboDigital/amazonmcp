import { createContext, useContext, useState, useEffect, useCallback } from 'react'
import { authApi } from './api'
import { setAuthTokenGetter } from './api'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)
  const [token, setTokenState] = useState(() => localStorage.getItem('auth_token'))

  const setToken = useCallback((t) => {
    if (t) {
      localStorage.setItem('auth_token', t)
      setTokenState(t)
    } else {
      localStorage.removeItem('auth_token')
      setTokenState(null)
      setUser(null)
    }
  }, [])

  const login = useCallback(async (email, password) => {
    const res = await authApi.login(email, password)
    setToken(res.access_token)
    setUser(res.user)
    return res.user
  }, [setToken])

  const register = useCallback(async (inviteToken, email, password, name) => {
    const res = await authApi.register(inviteToken, email, password, name)
    setToken(res.access_token)
    setUser(res.user)
    return res.user
  }, [setToken])

  const logout = useCallback(() => {
    setToken(null)
  }, [setToken])

  const checkAuth = useCallback(async () => {
    const t = localStorage.getItem('auth_token')
    if (!t) {
      setUser(null)
      setLoading(false)
      return null
    }
    try {
      const u = await authApi.whoami()
      setUser(u)
      return u
    } catch {
      localStorage.removeItem('auth_token')
      setTokenState(null)
      setUser(null)
      return null
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    setAuthTokenGetter(() => token || localStorage.getItem('auth_token'))
  }, [token])

  useEffect(() => {
    checkAuth()
  }, [checkAuth])

  useEffect(() => {
    function onLogout() {
      setToken(null)
    }
    window.addEventListener('auth:logout', onLogout)
    return () => window.removeEventListener('auth:logout', onLogout)
  }, [setToken])

  const value = {
    user,
    loading,
    token,
    login,
    register,
    logout,
    checkAuth,
    isAdmin: user?.role === 'admin',
  }

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
