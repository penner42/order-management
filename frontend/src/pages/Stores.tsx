import { useEffect, useState, Fragment } from 'react'
import { api } from '../api/client'
import { ConfirmDialog } from '../components/ConfirmDialog'
import type { Store, StoreAccount } from '../api/types'

export default function Stores() {
  const [stores, setStores] = useState<Store[]>([])
  const [accountsByStore, setAccountsByStore] = useState<Record<number, StoreAccount[]>>({})
  const [loading, setLoading] = useState(true)
  const [createName, setCreateName] = useState('')
  const [editingStoreId, setEditingStoreId] = useState<number | null>(null)
  const [editStoreName, setEditStoreName] = useState('')
  const [newAccountName, setNewAccountName] = useState('')
  const [editingAccountId, setEditingAccountId] = useState<number | null>(null)
  const [editAccountName, setEditAccountName] = useState('')
  const [confirmDeleteStoreId, setConfirmDeleteStoreId] = useState<number | null>(null)
  const [confirmDeleteAccount, setConfirmDeleteAccount] = useState<{ storeId: number; accountId: number } | null>(null)

  function loadStores() {
    api
      .get<Store[]>('/stores')
      .then((list) => {
        setStores(list)
        return Promise.all(list.map((s) => api.get<StoreAccount[]>(`/stores/${s.id}/accounts`))).then((accountLists) => ({
          list,
          accountLists,
        }))
      })
      .then(({ list, accountLists }) => {
        const byStore: Record<number, StoreAccount[]> = {}
        list.forEach((s, i) => {
          byStore[s.id] = accountLists[i] ?? []
        })
        setAccountsByStore(byStore)
      })
      .catch(console.error)
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    loadStores()
  }, [])

  const createStore = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!createName.trim()) return
    try {
      const created = await api.post<Store>('/stores', { name: createName.trim() })
      setStores((prev) => [...prev, created].sort((a, b) => a.name.localeCompare(b.name)))
      setAccountsByStore((prev) => ({ ...prev, [created.id]: [] }))
      setCreateName('')
    } catch (err) {
      console.error(err)
    }
  }

  const startEditStore = (s: Store) => {
    setEditingStoreId(s.id)
    setEditStoreName(s.name)
    setNewAccountName('')
    setEditingAccountId(null)
  }
  const saveStoreName = async () => {
    if (editingStoreId == null) return
    try {
      const updated = await api.patch<Store>(`/stores/${editingStoreId}`, { name: editStoreName.trim() })
      setStores((prev) => prev.map((p) => (p.id === updated.id ? updated : p)))
      setEditingStoreId(null)
    } catch (err) {
      console.error(err)
    }
  }
  const cancelEditStore = () => setEditingStoreId(null)

  const deleteStore = async (id: number) => {
    try {
      await api.delete(`/stores/${id}`)
      setStores((prev) => prev.filter((s) => s.id !== id))
      setAccountsByStore((prev) => {
        const next = { ...prev }
        delete next[id]
        return next
      })
      if (editingStoreId === id) setEditingStoreId(null)
    } catch (err) {
      console.error(err)
    } finally {
      setConfirmDeleteStoreId(null)
    }
  }

  const addAccount = async (storeId: number, e: React.FormEvent) => {
    e.preventDefault()
    if (!newAccountName.trim()) return
    try {
      const created = await api.post<StoreAccount>(`/stores/${storeId}/accounts`, { name: newAccountName.trim() })
      setAccountsByStore((prev) => ({
        ...prev,
        [storeId]: [...(prev[storeId] ?? []), created].sort((a, b) => a.name.localeCompare(b.name)),
      }))
      setNewAccountName('')
    } catch (err) {
      console.error(err)
    }
  }

  const startEditAccount = (a: StoreAccount) => {
    setEditingAccountId(a.id)
    setEditAccountName(a.name)
  }
  const saveAccountName = async (storeId: number) => {
    if (editingAccountId == null) return
    try {
      const updated = await api.patch<StoreAccount>(`/stores/${storeId}/accounts/${editingAccountId}`, {
        name: editAccountName.trim(),
      })
      setAccountsByStore((prev) => ({
        ...prev,
        [storeId]: (prev[storeId] ?? []).map((acc) => (acc.id === updated.id ? updated : acc)),
      }))
      setEditingAccountId(null)
    } catch (err) {
      console.error(err)
    }
  }
  const cancelEditAccount = () => setEditingAccountId(null)

  const deleteAccount = async (storeId: number, accountId: number) => {
    try {
      await api.delete(`/stores/${storeId}/accounts/${accountId}`)
      setAccountsByStore((prev) => ({
        ...prev,
        [storeId]: (prev[storeId] ?? []).filter((a) => a.id !== accountId),
      }))
    } catch (err) {
      console.error(err)
    } finally {
      setConfirmDeleteAccount(null)
    }
  }

  if (loading) return <div className="text-ink-muted">Loading stores…</div>

  return (
    <div>
      <h1 className="text-2xl font-semibold text-ink mb-8">Stores</h1>
      <form onSubmit={createStore} className="flex gap-2 mb-6">
        <input
          type="text"
          className="rounded-lg border border-brand-200 dark:border-gray-600 dark:bg-gray-800 px-3 py-2 flex-1 max-w-xs text-ink placeholder:text-ink-muted"
          placeholder="New store name"
          value={createName}
          onChange={(e) => setCreateName(e.target.value)}
        />
        <button type="submit" className="px-4 py-2 bg-brand-600 text-white rounded-lg font-medium hover:bg-brand-700">
          Add store
        </button>
      </form>
      <div className="w-fit min-w-[600px] max-w-full bg-white dark:bg-gray-800 rounded-xl border border-brand-200/80 dark:border-gray-700 shadow-sm overflow-hidden">
        <table className="min-w-0">
          <thead className="bg-brand-100/50 dark:bg-gray-700/50 border-b border-brand-200/80 dark:border-gray-700">
            <tr>
              <th className="text-left py-3 px-4 text-sm font-medium text-ink">Store</th>
              <th className="text-left py-3 px-4 text-sm font-medium text-ink">Accounts</th>
              <th className="w-24 py-3 px-4 text-sm font-medium text-ink text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {stores.length === 0 ? (
              <tr>
                <td colSpan={3} className="py-12 text-center text-ink-muted">
                  No stores yet. Add one above.
                </td>
              </tr>
            ) : (
              stores.map((s) => (
                <Fragment key={s.id}>
                  <tr className="border-b border-brand-100 dark:border-gray-700 last:border-0 hover:bg-brand-50/50 dark:hover:bg-gray-700/30">
                    <td className="py-3 px-4 font-medium text-brand-700 dark:text-brand-400">
                      {editingStoreId === s.id ? (
                        <input
                          type="text"
                          className="rounded border border-brand-200 dark:border-gray-600 dark:bg-gray-800 px-2 py-1 w-full max-w-xs text-ink"
                          value={editStoreName}
                          onChange={(e) => setEditStoreName(e.target.value)}
                          autoFocus
                        />
                      ) : (
                        s.name
                      )}
                    </td>
                    <td className="py-3 px-4 text-sm text-ink-muted dark:text-gray-400">
                      {(accountsByStore[s.id] ?? []).map((a) => a.name).join(', ') || '—'}
                    </td>
                    <td className="py-3 px-4 text-right">
                      {editingStoreId === s.id ? (
                        <div className="flex gap-2 justify-end">
                          <button
                            type="button"
                            onClick={saveStoreName}
                            className="text-sm text-brand-600 dark:text-brand-400 hover:underline"
                          >
                            Save
                          </button>
                          <button
                            type="button"
                            onClick={cancelEditStore}
                            className="text-sm text-ink-muted hover:underline"
                          >
                            Cancel
                          </button>
                        </div>
                      ) : (
                        <div className="flex gap-1 justify-end">
                          <button
                            type="button"
                            onClick={() => startEditStore(s)}
                            className="p-1.5 rounded text-ink-muted hover:text-brand-600 hover:bg-brand-50 dark:hover:bg-brand-900/20 transition"
                            title="Edit store & accounts"
                            aria-label="Edit store and accounts"
                          >
                            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                            </svg>
                          </button>
                          <button
                            type="button"
                            onClick={() => setConfirmDeleteStoreId(s.id)}
                            className="p-1.5 rounded text-ink-muted hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 transition"
                            title="Delete store"
                            aria-label="Delete store"
                          >
                            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                            </svg>
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                  {editingStoreId === s.id && (
                    <tr key={`${s.id}-accounts`}>
                      <td colSpan={3} className="bg-brand-50/50 dark:bg-gray-700/20 p-4">
                        <div className="max-w-2xl">
                          <h3 className="text-sm font-medium text-ink dark:text-gray-200 mb-3">Store accounts</h3>
                          <form
                            onSubmit={(e) => addAccount(s.id, e)}
                            className="flex gap-2 mb-4"
                          >
                            <input
                              type="text"
                              className="rounded-lg border border-brand-200 dark:border-gray-600 dark:bg-gray-800 px-3 py-2 flex-1 max-w-xs text-ink placeholder:text-ink-muted text-sm"
                              placeholder="New account name"
                              value={newAccountName}
                              onChange={(e) => setNewAccountName(e.target.value)}
                            />
                            <button
                              type="submit"
                              className="px-3 py-2 bg-brand-600 text-white rounded-lg text-sm font-medium hover:bg-brand-700"
                            >
                              Add account
                            </button>
                          </form>
                          <ul className="divide-y divide-brand-100 dark:divide-gray-600">
                            {(accountsByStore[s.id] ?? []).length === 0 ? (
                              <li className="py-2 text-sm text-ink-muted">No accounts yet.</li>
                            ) : (
                              (accountsByStore[s.id] ?? []).map((a) => (
                                <li key={a.id} className="flex items-center justify-between py-2">
                                  {editingAccountId === a.id ? (
                                    <>
                                      <input
                                        type="text"
                                        className="rounded border border-brand-200 dark:border-gray-600 dark:bg-gray-800 px-2 py-1 flex-1 max-w-sm text-ink text-sm"
                                        value={editAccountName}
                                        onChange={(e) => setEditAccountName(e.target.value)}
                                        autoFocus
                                      />
                                      <div className="flex gap-2 ml-2">
                                        <button
                                          type="button"
                                          onClick={() => saveAccountName(s.id)}
                                          className="text-sm text-brand-600 dark:text-brand-400 hover:underline"
                                        >
                                          Save
                                        </button>
                                        <button
                                          type="button"
                                          onClick={cancelEditAccount}
                                          className="text-sm text-ink-muted hover:underline"
                                        >
                                          Cancel
                                        </button>
                                      </div>
                                    </>
                                  ) : (
                                    <>
                                      <span className="text-sm text-ink dark:text-gray-200">{a.name}</span>
                                      <div className="flex gap-1">
                                        <button
                                          type="button"
                                          onClick={() => startEditAccount(a)}
                                          className="p-1 rounded text-ink-muted hover:text-brand-600 hover:bg-brand-50 dark:hover:bg-brand-900/20 transition"
                                          title="Edit account"
                                          aria-label="Edit account"
                                        >
                                          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                                          </svg>
                                        </button>
                                        <button
                                          type="button"
                                          onClick={() => setConfirmDeleteAccount({ storeId: s.id, accountId: a.id })}
                                          className="p-1 rounded text-ink-muted hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 transition"
                                          title="Delete account"
                                          aria-label="Delete account"
                                        >
                                          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                                          </svg>
                                        </button>
                                      </div>
                                    </>
                                  )}
                                </li>
                              ))
                            )}
                          </ul>
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))
            )}
          </tbody>
        </table>
      </div>
      <ConfirmDialog
        open={confirmDeleteStoreId !== null}
        message="Delete this store? Orders linked to it will remain but cannot be edited for store/account."
        confirmLabel="Delete store"
        danger
        onConfirm={() => confirmDeleteStoreId !== null && deleteStore(confirmDeleteStoreId)}
        onCancel={() => setConfirmDeleteStoreId(null)}
      />
      <ConfirmDialog
        open={confirmDeleteAccount !== null}
        message="Delete this store account?"
        confirmLabel="Delete"
        danger
        onConfirm={() =>
          confirmDeleteAccount !== null && deleteAccount(confirmDeleteAccount.storeId, confirmDeleteAccount.accountId)
        }
        onCancel={() => setConfirmDeleteAccount(null)}
      />
    </div>
  )
}
