import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Zap, Loader2, CheckCircle } from 'lucide-react'
import { authApi } from '../lib/api'

export default function ForgotPassword() {
  const [email, setEmail] = useState('')
  const [loading, setLoading] = useState(false)
  const [sent, setSent] = useState(false)
  const [error, setError] = useState('')

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      await authApi.forgotPassword(email)
      setSent(true)
    } catch (err) {
      setError(err.message || 'Failed to send reset email')
    } finally {
      setLoading(false)
    }
  }

  if (sent) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-50 px-4">
        <div className="card p-8 max-w-md text-center">
          <CheckCircle className="w-12 h-12 text-emerald-500 mx-auto mb-4" />
          <h1 className="text-lg font-semibold text-slate-800 mb-2">Check your email</h1>
          <p className="text-slate-600 text-sm mb-6">
            If an account exists for {email}, you will receive a password reset link shortly.
          </p>
          <Link to="/login" className="btn-primary">
            Back to login
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
              <h1 className="text-xl font-semibold text-slate-800">Forgot password?</h1>
              <p className="text-sm text-slate-500">Enter your email to receive a reset link</p>
            </div>
          </div>

          <form onSubmit={handleSubmit} className="space-y-5">
            {error && (
              <div className="p-3 rounded-lg bg-red-50 text-red-700 text-sm">
                {error}
              </div>
            )}
            <div>
              <label className="label">Email</label>
              <input
                type="email"
                className="input"
                placeholder="you@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                autoComplete="email"
              />
            </div>
            <button
              type="submit"
              className="btn-primary w-full justify-center"
              disabled={loading}
            >
              {loading ? (
                <Loader2 className="w-5 h-5 animate-spin" />
              ) : (
                'Send reset link'
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
