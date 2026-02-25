import { useState, useEffect } from 'react'
import { api, getStoredToken } from '../api/client'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { AlertDialog } from '../components/AlertDialog'
import type { User, UserRole } from '../api/types'
import { testUpsCredentials } from '../utils/tracking'

const ROLES: { value: UserRole; label: string }[] = [
  { value: 'user', label: 'User' },
  { value: 'admin', label: 'Admin' },
]

export default function Admin() {
  const [users, setUsers] = useState<User[]>([])
  const [loading, setLoading] = useState(true)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [formUsername, setFormUsername] = useState('')
  const [formPassword, setFormPassword] = useState('')
  const [formRole, setFormRole] = useState<UserRole>('user')
  const [showResetConfirm, setShowResetConfirm] = useState(false)
  const [alertMessage, setAlertMessage] = useState<string | null>(null)
  const [upsTesting, setUpsTesting] = useState(false)

  async function loadUsers() {
    try {
      const list = await api.get<User[]>('/admin/users')
      setUsers(list)
    } catch (err) {
      setAlertMessage(err instanceof Error ? err.message : 'Failed to load users')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadUsers()
  }, [])

  function startCreate() {
    setEditingId(-1)
    setFormUsername('')
    setFormPassword('')
    setFormRole('user')
  }

  function startEdit(u: User) {
    setEditingId(u.id)
    setFormUsername(u.username)
    setFormPassword('')
    setFormRole(u.role)
  }

  function cancelEdit() {
    setEditingId(null)
    setFormUsername('')
    setFormPassword('')
    setFormRole('user')
  }

  async function saveUser(e: React.FormEvent) {
    e.preventDefault()
    if (!formUsername.trim()) return
    try {
      if (editingId === -1) {
        if (!formPassword) {
          setAlertMessage('Password is required for new users')
          return
        }
        await api.post<User>('/admin/users', {
          username: formUsername.trim(),
          password: formPassword,
          role: formRole,
        })
      } else {
        await api.patch<User>(`/admin/users/${editingId}`, {
          username: formUsername.trim(),
          password: formPassword || undefined,
          role: formRole,
        })
      }
      cancelEdit()
      await loadUsers()
    } catch (err) {
      setAlertMessage(err instanceof Error ? err.message : 'Failed to save user')
    }
  }

  async function deleteUser(id: number) {
    try {
      await api.delete(`/admin/users/${id}`)
      await loadUsers()
      if (editingId === id) cancelEdit()
    } catch (err) {
      setAlertMessage(err instanceof Error ? err.message : 'Failed to delete user')
    }
  }

  async function handleResetDatabase() {
    setShowResetConfirm(false)
    try {
      await api.post('/admin/reset-database', {})
      setAlertMessage('Database emptied. You will need to log in again.')
      setTimeout(() => {
        window.location.href = '/login'
      }, 1500)
    } catch (err) {
      setAlertMessage(err instanceof Error ? err.message : 'Failed to reset database')
    }
  }

  async function handleCreateBackup() {
    try {
      const data = await api.post<{ message: string; filename: string }>('/admin/create-backup')
      setAlertMessage(`Backup created: ${data.filename}`)
    } catch (err) {
      setAlertMessage(err instanceof Error ? err.message : 'Failed to create backup')
    }
  }

  async function handleDownloadBackup() {
    try {
      const token = getStoredToken()
      const res = await fetch('/api/admin/backup', {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }))
        throw new Error(err.detail || 'Failed to download backup')
      }
      const blob = await res.blob()
      const disposition = res.headers.get('Content-Disposition')
      const filenameMatch = disposition?.match(/filename="?([^";\n]+)"?/)
      const filename = filenameMatch?.[1] ?? `order-management-backup-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.json`
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = filename
      a.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      setAlertMessage(err instanceof Error ? err.message : 'Failed to download backup')
    }
  }

  async function handleSeedDev() {
    try {
      const data = await api.post<{ message: string; skipped: boolean }>('/admin/seed-dev')
      setAlertMessage(data.message)
    } catch (err) {
      setAlertMessage(err instanceof Error ? err.message : 'Failed to load sample data')
    }
  }

  async function handleTestUpsCredentials() {
    setUpsTesting(true)
    try {
      const result = await testUpsCredentials()
      setAlertMessage(result.detail)
    } catch (err) {
      setAlertMessage(err instanceof Error ? err.message : 'Failed to test UPS credentials')
    } finally {
      setUpsTesting(false)
    }
  }

  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-semibold text-brand-800 dark:text-gray-100">Admin</h1>

      <section>
        <h2 className="text-lg font-medium text-ink dark:text-gray-200 mb-4">Users</h2>
        {loading ? (
          <p className="text-ink-muted dark:text-gray-400">Loading…</p>
        ) : (
          <>
            <div className="mb-4">
              {editingId === null ? (
                <button
                  type="button"
                  onClick={startCreate}
                  className="px-3 py-1.5 bg-brand-600 text-white rounded-lg text-sm hover:bg-brand-700"
                >
                  Add user
                </button>
              ) : (
                <form onSubmit={saveUser} className="flex flex-wrap items-end gap-3 p-4 bg-white dark:bg-gray-800 border border-brand-200 dark:border-gray-700 rounded-lg">
                  <div>
                    <label className="block text-xs font-medium text-ink-muted dark:text-gray-400 mb-1">
                      Username
                    </label>
                    <input
                      type="text"
                      value={formUsername}
                      onChange={(e) => setFormUsername(e.target.value)}
                      className="px-2 py-1.5 border border-brand-200 dark:border-gray-600 rounded text-sm bg-white dark:bg-gray-700 text-ink dark:text-gray-100"
                      required
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-ink-muted dark:text-gray-400 mb-1">
                      Password {editingId === -1 ? '' : '(leave blank to keep)'}
                    </label>
                    <input
                      type="password"
                      value={formPassword}
                      onChange={(e) => setFormPassword(e.target.value)}
                      className="px-2 py-1.5 border border-brand-200 dark:border-gray-600 rounded text-sm bg-white dark:bg-gray-700 text-ink dark:text-gray-100"
                      autoComplete={editingId === -1 ? 'new-password' : 'new-password'}
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-ink-muted dark:text-gray-400 mb-1">
                      Role
                    </label>
                    <select
                      value={formRole}
                      onChange={(e) => setFormRole(e.target.value as UserRole)}
                      className="px-2 py-1.5 border border-brand-200 dark:border-gray-600 rounded text-sm bg-white dark:bg-gray-700 text-ink dark:text-gray-100"
                    >
                      {ROLES.map((r) => (
                        <option key={r.value} value={r.value}>
                          {r.label}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="flex gap-2">
                    <button
                      type="submit"
                      className="px-3 py-1.5 bg-brand-600 text-white rounded text-sm hover:bg-brand-700"
                    >
                      Save
                    </button>
                    <button
                      type="button"
                      onClick={cancelEdit}
                      className="px-3 py-1.5 border border-brand-300 dark:border-gray-600 rounded text-sm"
                    >
                      Cancel
                    </button>
                  </div>
                </form>
              )}
            </div>
            <ul className="border border-brand-200 dark:border-gray-700 rounded-lg divide-y divide-brand-200 dark:divide-gray-700">
              {users.map((u) => (
                <li
                  key={u.id}
                  className="flex items-center justify-between px-4 py-3 bg-white dark:bg-gray-800 first:rounded-t-lg last:rounded-b-lg"
                >
                  <div>
                    <span className="font-medium text-ink dark:text-gray-100">{u.username}</span>
                    <span className="ml-2 text-xs text-ink-muted dark:text-gray-400">
                      ({ROLES.find((r) => r.value === u.role)?.label ?? u.role})
                    </span>
                  </div>
                  <div className="flex gap-2">
                    {u.role !== 'admin' && (
                      <>
                        <button
                          type="button"
                          onClick={() => startEdit(u)}
                          className="p-1.5 rounded text-ink-muted hover:text-brand-600 hover:bg-brand-50 dark:hover:bg-brand-900/20 transition"
                          title="Edit"
                          aria-label="Edit"
                        >
                          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                          </svg>
                        </button>
                        <button
                          type="button"
                          onClick={() => deleteUser(u.id)}
                          className="p-1.5 rounded text-ink-muted hover:text-red-600 hover:bg-red-50 dark:hover:text-red-400 dark:hover:bg-red-900/20 transition"
                          title="Delete"
                          aria-label="Delete"
                        >
                          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                          </svg>
                        </button>
                      </>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </>
        )}
      </section>

      <section>
        <h2 className="text-lg font-medium text-ink dark:text-gray-200 mb-4">Database</h2>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={handleSeedDev}
            className="px-3 py-1.5 bg-brand-600 text-white rounded-lg text-sm hover:bg-brand-700"
          >
            Load sample data
          </button>
          <button
            type="button"
            onClick={handleCreateBackup}
            className="px-3 py-1.5 bg-brand-600 text-white rounded-lg text-sm hover:bg-brand-700"
          >
            Create backup
          </button>
          <button
            type="button"
            onClick={handleDownloadBackup}
            className="px-3 py-1.5 bg-brand-600 text-white rounded-lg text-sm hover:bg-brand-700"
          >
            Download backup
          </button>
          <button
            type="button"
            onClick={() => setShowResetConfirm(true)}
            className="px-3 py-1.5 bg-red-600 text-white rounded-lg text-sm hover:bg-red-700"
          >
            Empty database
          </button>
        </div>
        <p className="mt-2 text-sm text-ink-muted dark:text-gray-400">
          Load sample orders, stores, items, and related data for development (idempotent—safe to run again). Create a pg_dump backup in the backups folder,
          download a JSON backup of all data, or permanently delete all orders, shipments, stores, and other data. Admin user is recreated from environment
          after reset. Reset cannot be undone.
        </p>
      </section>

      <section>
        <h2 className="text-lg font-medium text-ink dark:text-gray-200 mb-4">Integrations</h2>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={handleTestUpsCredentials}
            disabled={upsTesting}
            className="px-3 py-1.5 bg-brand-600 text-white rounded-lg text-sm hover:bg-brand-700 disabled:opacity-60 disabled:cursor-not-allowed"
          >
            {upsTesting ? 'Testing UPS…' : 'Test UPS credentials'}
          </button>
        </div>
        <p className="mt-2 text-sm text-ink-muted dark:text-gray-400">
          Verify that UPS OAuth client credentials (UPS_CLIENT, UPS_SECRET) are configured correctly by calling the package-tracking service and reporting
          any errors from UPS.
        </p>
      </section>

      <ConfirmDialog
        open={showResetConfirm}
        message="Empty the entire database? All orders, shipments, stores, and other data will be permanently deleted. You will need to sign in again. This cannot be undone."
        confirmLabel="Empty database"
        danger
        onConfirm={handleResetDatabase}
        onCancel={() => setShowResetConfirm(false)}
      />
      <AlertDialog
        open={alertMessage !== null}
        message={alertMessage ?? ''}
        onClose={() => setAlertMessage(null)}
      />
    </div>
  )
}
