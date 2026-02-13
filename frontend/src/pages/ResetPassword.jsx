import { useState, useEffect } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { Zap, Loader2 } from 'lucide-react'
import { useAuth } from '../lib/AuthContext'

export default function ResetPassword() {
  const { resetPassword } = useAuth()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const tokenFromUrl = searchParams.get('token') || ''

  const [token, setToken] = useState(tokenFromUrl)
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (tokenFromUrl) setToken(tokenFromUrl)
  }, [tokenFromUrl])

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      await resetPassword(token, password)
      navigate('/', { replace: true })
    } catch (err) {
      setError(err.message || 'Failed to reset password')
    } finally {
      setLoading(false)
    }
  }

  if (!token) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-50 px-4">
        <div className="card p-8 max-w-md text-center">
          <Zap className="w-12 h-12 text-brand-600 mx-auto mb-4" />
          <h1 className="text-lg font-semibold text-slate-800 mb-2">Invalid reset link</h1>
          <p className="text-slate-600 text-sm mb-6">
            This password reset link is invalid or has expired. Please request a new one.
          </p>
          <Link to="/forgot-password" className="btn-primary">
            Request new link
          </Link>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-50 px-4">
      <div className="w-full max-w-md">
        <div className="card p-8 shadow-lg">
          <div className="flex items-center gap-3 mb-8">
            <div className="p-2.5 bg-brand-100 rounded-xl">
              <Zap className="w-8 h-8 text-brand-600" />
            </div>
            <div>
              <h1 className="text-xl font-semibold text-slate-800">Set new password</h1>
              <p className="text-sm text-slate-500">Enter your new password below</p>
            </div>
          </div>

          <form onSubmit={handleSubmit} className="space-y-5">
            {error && (
              <div className="p-3 rounded-lg bg-red-50 text-red-700 text-sm">
                {error}
              </div>
            )}
            <div>
              <label className="label">New password</label>
              <input
                type="password"
                className="input"
                placeholder="••••••••"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                minLength={8}
                autoComplete="new-password"
              />
              <p className="mt-1 text-xs text-slate-500">At least 8 characters</p>
            </div>
            <button
              type="submit"
              className="btn-primary w-full justify-center"
              disabled={loading}
            >
              {loading ? (
                <Loader2 className="w-5 h-5 animate-spin" />
              ) : (
                'Reset password'
              )}
            </button>
          </form>

          <p className="mt-6 text-center text-sm text-slate-500">
            <Link to="/login" className="text-brand-600 hover:underline">Back to login</Link>
          </p>
        </div>
      </div>
    </div>
  )
}
