import { useState, useEffect } from 'react'
import {
  Users,
  Mail,
  Plus,
  Trash2,
  Copy,
  Loader2,
  UserPlus,
  Shield,
} from 'lucide-react'
import { usersApi } from '../lib/api'
import { useAuth } from '../lib/AuthContext'
import EmptyState from '../components/EmptyState'

export default function UserManagement() {
  const { user: currentUser, isAdmin } = useAuth()
  const [users, setUsers] = useState([])
  const [invitations, setInvitations] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [inviteEmail, setInviteEmail] = useState('')
  const [inviteRole, setInviteRole] = useState('user')
  const [inviting, setInviting] = useState(false)
  const [copiedId, setCopiedId] = useState(null)

  useEffect(() => {
    load()
  }, [])

  async function load() {
    setLoading(true)
    setError(null)
    try {
      const [u, i] = await Promise.all([
        usersApi.list(),
        usersApi.invitations.list(),
      ])
      setUsers(u)
      setInvitations(i)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  async function handleInvite(e) {
    e.preventDefault()
    if (!inviteEmail.trim()) return
    setInviting(true)
    setError(null)
    try {
      const res = await usersApi.invitations.create(inviteEmail.trim(), inviteRole)
      setInviteEmail('')
      await load()
      copyToClipboard(res.invite_link, res.id)
    } catch (err) {
      setError(err.message)
    } finally {
      setInviting(false)
    }
  }

  async function revokeInvitation(id) {
    try {
      await usersApi.invitations.revoke(id)
      await load()
    } catch (err) {
      setError(err.message)
    }
  }

  function copyToClipboard(text, id) {
    navigator.clipboard.writeText(text)
    setCopiedId(id)
    setTimeout(() => setCopiedId(null), 2000)
  }

  function formatDate(d) {
    if (!d) return '-'
    return new Date(d).toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  }

  if (!isAdmin) {
    return (
      <div className="p-8">
        <div className="card p-8 text-center">
          <Shield className="w-12 h-12 text-amber-500 mx-auto mb-4" />
          <h2 className="text-lg font-semibold text-slate-800">Access denied</h2>
          <p className="text-slate-600 mt-2">Only administrators can manage users.</p>
        </div>
      </div>
    )
  }

  if (loading) {
    return (
      <div className="p-8 flex items-center justify-center">
        <Loader2 className="w-8 h-8 animate-spin text-brand-600" />
      </div>
    )
  }

  return (
    <div className="p-8 max-w-4xl mx-auto">
      <div className="flex items-center gap-3 mb-8">
        <div className="p-2.5 bg-brand-100 rounded-xl">
          <Users className="w-6 h-6 text-brand-600" />
        </div>
        <div>
          <h1 className="text-xl font-semibold text-slate-800">User Management</h1>
          <p className="text-sm text-slate-500">Manage users and invitations</p>
        </div>
      </div>

      {error && (
        <div className="mb-6 p-4 rounded-lg bg-red-50 text-red-700 text-sm">
          {error}
        </div>
      )}

      {/* Invite form */}
      <div className="card p-6 mb-8">
        <h2 className="text-sm font-medium text-slate-700 mb-4 flex items-center gap-2">
          <UserPlus className="w-4 h-4" />
          Invite new user
        </h2>
        <form onSubmit={handleInvite} className="flex flex-wrap gap-3 items-end">
          <div className="flex-1 min-w-[200px]">
            <label className="label">Email</label>
            <input
              type="email"
              className="input"
              placeholder="colleague@example.com"
              value={inviteEmail}
              onChange={(e) => setInviteEmail(e.target.value)}
              required
            />
          </div>
          <div className="w-32">
            <label className="label">Role</label>
            <select
              className="input"
              value={inviteRole}
              onChange={(e) => setInviteRole(e.target.value)}
            >
              <option value="user">User</option>
              <option value="admin">Admin</option>
            </select>
          </div>
          <button type="submit" className="btn-primary" disabled={inviting}>
            {inviting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
            Invite
          </button>
        </form>
      </div>

      {/* Pending invitations */}
      <div className="card overflow-hidden mb-8">
        <div className="px-6 py-4 border-b border-slate-200">
          <h2 className="text-sm font-medium text-slate-700 flex items-center gap-2">
            <Mail className="w-4 h-4" />
            Pending invitations ({invitations.filter((i) => i.status === 'pending').length})
          </h2>
        </div>
        <div className="divide-y divide-slate-100">
          {invitations.filter((i) => i.status === 'pending').length === 0 ? (
            <div className="px-6 py-8 text-center text-slate-500 text-sm">
              No pending invitations
            </div>
          ) : (
            invitations
              .filter((i) => i.status === 'pending')
              .map((inv) => (
                <div
                  key={inv.id}
                  className="px-6 py-4 flex items-center justify-between gap-4"
                >
                  <div>
                    <p className="font-medium text-slate-800">{inv.email}</p>
                    <p className="text-xs text-slate-500">
                      {inv.role} · expires {formatDate(inv.expires_at)}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      className="btn-ghost text-sm"
                      onClick={() =>
                        copyToClipboard(
                          inv.invite_link || `${window.location.origin}/register?token=${inv.id}`,
                          inv.id
                        )
                      }
                    >
                      {copiedId === inv.id ? (
                        'Copied!'
                      ) : (
                        <>
                          <Copy className="w-4 h-4" />
                          Copy link
                        </>
                      )}
                    </button>
                    <button
                      type="button"
                      className="btn-ghost text-red-600 hover:bg-red-50"
                      onClick={() => revokeInvitation(inv.id)}
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>
              ))
          )}
        </div>
      </div>

      {/* Users list */}
      <div className="card overflow-hidden">
        <div className="px-6 py-4 border-b border-slate-200">
          <h2 className="text-sm font-medium text-slate-700 flex items-center gap-2">
            <Users className="w-4 h-4" />
            Users ({users.length})
          </h2>
        </div>
        <div className="divide-y divide-slate-100">
          {users.map((u) => (
            <div
              key={u.id}
              className="px-6 py-4 flex items-center justify-between gap-4"
            >
              <div>
                <div className="flex items-center gap-2">
                  <p className="font-medium text-slate-800">
                    {u.name || u.email}
                    {u.id === currentUser?.id && (
                      <span className="text-xs text-slate-500">(you)</span>
                    )}
                  </p>
                  {u.role === 'admin' && (
                    <span className="badge badge-yellow">Admin</span>
                  )}
                </div>
                <p className="text-sm text-slate-500">{u.email}</p>
                <p className="text-xs text-slate-400">
                  Last login: {formatDate(u.last_login_at)} · Created {formatDate(u.created_at)}
                </p>
              </div>
              <div className="flex items-center gap-2">
                {!u.is_active && (
                  <span className="badge bg-slate-100 text-slate-600">Inactive</span>
                )}
                {u.id !== currentUser?.id && (
                  <button
                    type="button"
                    className="btn-ghost text-red-600 hover:bg-red-50 text-sm"
                    onClick={async () => {
                      if (window.confirm(`Remove ${u.email}?`)) {
                        try {
                          await usersApi.delete(u.id)
                          await load()
                        } catch (err) {
                          setError(err.message)
                        }
                      }
                    }}
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
