import { Fragment, useEffect, useState } from 'react'
import { api } from '../api/client'
import { AlertDialog } from '../components/AlertDialog'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { SearchableCombobox } from '../components/SearchableCombobox'
import type { PaymentMethod, PaymentMethodNested, PaymentMethodStoreEarningsEntry, Reward } from '../api/types'

export default function PaymentMethods() {
  const [methods, setMethods] = useState<PaymentMethod[]>([])
  const [rewards, setRewards] = useState<Reward[]>([])
  const [loading, setLoading] = useState(true)
  const [showForm, setShowForm] = useState(false)
  const [label, setLabel] = useState('')
  const [rewardId, setRewardId] = useState<number | ''>('')
  const [editing, setEditing] = useState<number | null>(null)
  const [editLabel, setEditLabel] = useState('')
  const [editRewardId, setEditRewardId] = useState<number | ''>('')
  const [storeEarnings, setStoreEarnings] = useState<PaymentMethodStoreEarningsEntry[]>([])
  const [editPointsPerDollar, setEditPointsPerDollar] = useState<Record<number, string>>({})

  const selectedReward = rewardId === '' ? null : rewards.find((r) => r.id === rewardId) ?? null
  const editReward = editRewardId === '' ? null : rewards.find((r) => r.id === editRewardId) ?? null

  const createReward = async (name: string): Promise<Reward> => {
    const created = await api.post<Reward>('/rewards', { name: name.trim() })
    setRewards((prev) => [...prev, created])
    return created
  }
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [addingSubForParentId, setAddingSubForParentId] = useState<number | null>(null)
  const [subMethodLabel, setSubMethodLabel] = useState('')

  const startEdit = async (m: PaymentMethod) => {
    setEditing(m.id)
    setEditLabel(m.label)
    setEditRewardId(m.reward_id ?? '')
    try {
      const [earningsData] = await Promise.all([
        api.get<PaymentMethodStoreEarningsEntry[]>(`/payment-methods/${m.id}/store-earnings`),
      ])
      setStoreEarnings(earningsData)
      setEditPointsPerDollar(
        Object.fromEntries(
          earningsData.map((e) => [e.store_id, e.points_per_dollar != null ? String(e.points_per_dollar) : ''])
        )
      )
    } catch (e) {
      console.error(e)
      setStoreEarnings([])
      setEditPointsPerDollar({})
    }
  }
  const saveEdit = async () => {
    if (editing == null) return
    if (!editLabel.trim()) return
    try {
      const payload: { label: string; reward_id: number | null } = {
        label: editLabel.trim(),
        reward_id: editRewardId === '' ? null : editRewardId,
      }
      const updated = await api.patch<PaymentMethod>(`/payment-methods/${editing}`, payload)
      const storeEarningsPayload = {
        store_earnings: storeEarnings.map((e) => ({
          store_id: e.store_id,
          points_per_dollar: parseFloat(editPointsPerDollar[e.store_id] ?? '0') || 0,
        })),
      }
      await api.put(`/payment-methods/${editing}/store-earnings`, storeEarningsPayload)
      const parentId = updated.parent_id ?? null
      setMethods((prev) =>
        parentId
          ? prev.map((m) =>
              m.id === parentId
                ? { ...m, sub_methods: (m.sub_methods ?? []).map((s) => (s.id === updated.id ? updated : s)) }
                : m
            )
          : prev.map((pm) => (pm.id === updated.id ? { ...updated, sub_methods: pm.sub_methods } : pm))
      )
      setEditing(null)
    } catch (e) {
      console.error(e)
    }
  }
  const cancelEdit = () => setEditing(null)

  useEffect(() => {
    Promise.all([
      api.get<PaymentMethod[]>('/payment-methods'),
      api.get<Reward[]>('/rewards'),
    ])
      .then(([methodsData, rewardsData]) => {
        setMethods(methodsData)
        setRewards(rewardsData)
      })
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [])

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!label.trim()) return
    try {
      const created = await api.post<PaymentMethod>('/payment-methods', {
        label: label.trim(),
        reward_id: rewardId === '' ? null : rewardId,
      })
      setMethods((prev) => [created, ...prev])
      setLabel('')
      setRewardId('')
      setShowForm(false)
    } catch (e) {
      console.error(e)
    }
  }

  const remove = async (id: number) => {
    try {
      await api.delete(`/payment-methods/${id}`)
      setMethods((prev) =>
        prev.map((m) => ({
          ...m,
          sub_methods: (m.sub_methods ?? []).filter((s) => s.id !== id),
        })).filter((m) => m.id !== id)
      )
    } catch (e) {
      setErrorMessage(e instanceof Error ? e.message : 'Failed to delete payment method.')
    } finally {
      setConfirmDeleteId(null)
    }
  }

  const addSubMethod = async (parentId: number, label: string) => {
    if (!label.trim()) return
    try {
      const created = await api.post<PaymentMethod & { sub_methods?: PaymentMethod['sub_methods'] }>('/payment-methods', {
        label: label.trim(),
        parent_id: parentId,
      })
      setMethods((prev) =>
        prev.map((m) =>
          m.id === parentId ? { ...m, sub_methods: [...(m.sub_methods ?? []), created] } : m
        )
      )
      setSubMethodLabel('')
      setAddingSubForParentId(null)
    } catch (e) {
      console.error(e)
    }
  }

  if (loading) return <div className="text-ink-muted">Loading…</div>

  return (
    <div>
      <div className="flex items-center justify-between mb-8">
        <h1 className="text-2xl font-semibold text-ink">Payment methods</h1>
        <button
          type="button"
          onClick={() => setShowForm((v) => !v)}
          className="px-4 py-2 bg-brand-600 text-white rounded-lg font-medium hover:bg-brand-700 transition"
        >
          {showForm ? 'Cancel' : 'Add payment method'}
        </button>
      </div>
      {showForm && (
        <form onSubmit={submit} className="bg-white dark:bg-gray-800 rounded-xl border border-brand-200/80 dark:border-gray-700 p-6 mb-6 max-w-md space-y-4">
          <div>
            <label className="block text-sm font-medium text-ink mb-1">Label</label>
            <div className="flex gap-2 mt-1">
              <input
                type="text"
                className="flex-1 rounded-lg border border-brand-200 px-3 py-2"
                placeholder="e.g. Visa ****1234, PayPal - Amex"
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                autoFocus
              />
              <button type="submit" className="px-4 py-2 bg-brand-600 text-white rounded-lg font-medium hover:bg-brand-700">
                Add
              </button>
            </div>
          </div>
          <div>
            <SearchableCombobox<Reward>
              label="Rewards type"
              options={rewards}
              value={selectedReward}
              onChange={(r) => setRewardId(r?.id ?? '')}
              onCreate={createReward}
              allowEmpty
              placeholder="Type to search or add rewards type…"
              inputClassName="w-full rounded-lg border border-brand-200 dark:border-gray-600 px-3 py-2 bg-white dark:bg-gray-700 text-ink focus:outline-none focus:ring-2 focus:ring-brand-500"
            />
          </div>
        </form>
      )}
      <div className="w-full md:w-fit md:min-w-[600px] md:max-w-full bg-white dark:bg-gray-800 rounded-xl border border-brand-200/80 dark:border-gray-700 shadow-sm overflow-visible">
        <ul className="divide-y divide-brand-100 dark:divide-gray-700 overflow-hidden rounded-b-xl">
          {methods.length === 0 ? (
            <li className="py-8 text-center text-ink-muted">No payment methods yet. Add one to use on orders.</li>
          ) : (
            methods.map((m) => (
              <Fragment key={m.id}>
                <li
                  className={`flex items-center justify-between px-4 py-3 gap-4 ${editing === m.id ? 'relative z-10' : ''}`}
                >
                  {editing === m.id ? (
                    <>
                      <div className="flex flex-col gap-3 flex-1 min-w-0">
                        <div className="flex flex-col gap-2">
                          <input
                            type="text"
                            className="rounded border border-brand-200 dark:border-gray-600 px-2 py-1 max-w-sm bg-white dark:bg-gray-700 text-ink"
                            value={editLabel}
                            onChange={(e) => setEditLabel(e.target.value)}
                            autoFocus
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') saveEdit()
                              if (e.key === 'Escape') cancelEdit()
                            }}
                          />
                          <SearchableCombobox<Reward>
                            options={rewards}
                            value={editReward}
                            onChange={(r) => setEditRewardId(r?.id ?? '')}
                            onCreate={createReward}
                            allowEmpty
                            placeholder="Search or add rewards type…"
                            inputClassName="rounded border border-brand-200 dark:border-gray-600 px-2 py-1 max-w-xs bg-white dark:bg-gray-700 text-ink text-sm w-full min-w-[160px] focus:outline-none focus:ring-2 focus:ring-brand-500"
                          />
                        </div>
                        {storeEarnings.length > 0 && (
                          <div className="border border-brand-200 dark:border-gray-600 rounded-lg overflow-hidden max-w-xl">
                            <table className="w-full text-sm">
                              <thead>
                                <tr className="bg-brand-50 dark:bg-gray-700/50">
                                  <th className="text-left px-3 py-2 font-medium text-ink">Store</th>
                                  <th className="text-left px-3 py-2 font-medium text-ink w-[140px]">Points per dollar</th>
                                </tr>
                              </thead>
                              <tbody className="divide-y divide-brand-100 dark:divide-gray-700">
                                {storeEarnings.map((e) => (
                                  <tr key={e.store_id}>
                                    <td className="px-3 py-2 text-ink">{e.store.name}</td>
                                    <td className="px-3 py-2">
                                      <input
                                        type="number"
                                        step="any"
                                        min="0"
                                        className="w-full rounded border border-brand-200 dark:border-gray-600 px-2 py-1 bg-white dark:bg-gray-700 text-ink"
                                        placeholder="0"
                                        value={editPointsPerDollar[e.store_id] ?? ''}
                                        onChange={(ev) =>
                                          setEditPointsPerDollar((prev) => ({ ...prev, [e.store_id]: ev.target.value }))
                                        }
                                      />
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        )}
                      </div>
                      <div className="flex gap-2 shrink-0 self-start">
                        <button
                          type="button"
                          onClick={saveEdit}
                          className="text-sm text-brand-600 hover:underline"
                        >
                          Save
                        </button>
                        <button type="button" onClick={cancelEdit} className="text-sm text-ink-muted hover:underline">
                          Cancel
                        </button>
                      </div>
                    </>
                  ) : (
                    <>
                      <div className="min-w-0">
                        <span className="font-medium text-ink">{m.label}</span>
                        <span className="ml-2 text-sm text-ink-muted">
                          → {m.reward ? m.reward.name : 'No Rewards Set'}
                        </span>
                      </div>
                      <div className="flex items-center gap-0.5 shrink-0">
                        <button
                          type="button"
                          onClick={() => setAddingSubForParentId(m.id)}
                          className="text-sm text-brand-600 hover:underline"
                          title="Add sub-method"
                        >
                          + Sub-method
                        </button>
                        <button
                          type="button"
                          onClick={() => startEdit(m)}
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
                          onClick={() => setConfirmDeleteId(m.id)}
                          className="p-1.5 rounded text-ink-muted hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 transition"
                          title="Delete"
                          aria-label="Delete"
                        >
                          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                          </svg>
                        </button>
                      </div>
                    </>
                  )}
                </li>
                {addingSubForParentId === m.id && (
                  <li className="px-4 py-2 pl-8 border-t border-brand-100 dark:border-gray-700 bg-brand-50/30 dark:bg-gray-700/30">
                    <form
                      onSubmit={(e) => {
                        e.preventDefault()
                        addSubMethod(m.id, subMethodLabel)
                      }}
                      className="flex items-center gap-2 flex-wrap"
                    >
                      <input
                        type="text"
                        className="rounded border border-brand-200 dark:border-gray-600 px-2 py-1.5 flex-1 min-w-[160px] bg-white dark:bg-gray-700 text-ink text-sm"
                        placeholder="Sub-method label (inherits reward)"
                        value={subMethodLabel}
                        onChange={(e) => setSubMethodLabel(e.target.value)}
                        autoFocus
                      />
                      <button type="submit" className="text-sm text-brand-600 hover:underline">
                        Add
                      </button>
                      <button
                        type="button"
                        onClick={() => { setAddingSubForParentId(null); setSubMethodLabel('') }}
                        className="text-sm text-ink-muted hover:underline"
                      >
                        Cancel
                      </button>
                    </form>
                  </li>
                )}
                {(m.sub_methods ?? []).map((sub) => (
                  <li
                    key={sub.id}
                    className={`flex items-center justify-between px-4 py-2 pl-8 gap-4 border-t border-brand-100 dark:border-gray-700 ${editing === sub.id ? 'relative z-10 bg-brand-50/30 dark:bg-gray-700/30' : ''}`}
                  >
                    {editing === sub.id ? (
                      <>
                        <div className="flex flex-col gap-3 flex-1 min-w-0">
                          <div className="flex flex-col gap-2">
                            <input
                              type="text"
                              className="rounded border border-brand-200 dark:border-gray-600 px-2 py-1 max-w-sm bg-white dark:bg-gray-700 text-ink text-sm"
                              value={editLabel}
                              onChange={(e) => setEditLabel(e.target.value)}
                              autoFocus
                              onKeyDown={(e) => {
                                if (e.key === 'Enter') saveEdit()
                                if (e.key === 'Escape') cancelEdit()
                              }}
                            />
                            <SearchableCombobox<Reward>
                              options={rewards}
                              value={editReward}
                              onChange={(r) => setEditRewardId(r?.id ?? '')}
                              onCreate={createReward}
                              allowEmpty
                              placeholder="Reward (inherited from parent)"
                              inputClassName="rounded border border-brand-200 dark:border-gray-600 px-2 py-1 max-w-xs bg-white dark:bg-gray-700 text-ink text-sm w-full min-w-[160px] focus:outline-none focus:ring-2 focus:ring-brand-500"
                            />
                          </div>
                          {storeEarnings.length > 0 && (
                            <div className="border border-brand-200 dark:border-gray-600 rounded-lg overflow-hidden max-w-xl">
                              <table className="w-full text-sm">
                                <thead>
                                  <tr className="bg-brand-50 dark:bg-gray-700/50">
                                    <th className="text-left px-3 py-2 font-medium text-ink">Store</th>
                                    <th className="text-left px-3 py-2 font-medium text-ink w-[140px]">Points per dollar</th>
                                  </tr>
                                </thead>
                                <tbody className="divide-y divide-brand-100 dark:divide-gray-700">
                                  {storeEarnings.map((e) => (
                                    <tr key={e.store_id}>
                                      <td className="px-3 py-2 text-ink">{e.store.name}</td>
                                      <td className="px-3 py-2">
                                        <input
                                          type="number"
                                          step="any"
                                          min="0"
                                          className="w-full rounded border border-brand-200 dark:border-gray-600 px-2 py-1 bg-white dark:bg-gray-700 text-ink text-sm"
                                          placeholder="0"
                                          value={editPointsPerDollar[e.store_id] ?? ''}
                                          onChange={(ev) =>
                                            setEditPointsPerDollar((prev) => ({ ...prev, [e.store_id]: ev.target.value }))
                                          }
                                        />
                                      </td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                          )}
                        </div>
                        <div className="flex gap-2 shrink-0 self-start">
                          <button type="button" onClick={saveEdit} className="text-sm text-brand-600 hover:underline">
                            Save
                          </button>
                          <button type="button" onClick={cancelEdit} className="text-sm text-ink-muted hover:underline">
                            Cancel
                          </button>
                        </div>
                      </>
                    ) : (
                      <>
                        <div className="min-w-0">
                          <span className="text-ink text-sm">{sub.label}</span>
                          <span className="ml-2 text-sm text-ink-muted">
                            → {sub.reward ? sub.reward.name : 'inherited'}
                          </span>
                        </div>
                        <div className="flex items-center gap-0.5 shrink-0">
                          <button
                            type="button"
                            onClick={() => startEdit(sub)}
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
                            onClick={() => setConfirmDeleteId(sub.id)}
                            className="p-1.5 rounded text-ink-muted hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 transition"
                            title="Delete"
                            aria-label="Delete"
                          >
                            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                            </svg>
                          </button>
                        </div>
                      </>
                    )}
                  </li>
                ))}
              </Fragment>
            ))
          )}
        </ul>
      </div>
      <ConfirmDialog
        open={confirmDeleteId !== null}
        message="Delete this payment method?"
        confirmLabel="Delete"
        danger
        onConfirm={() => confirmDeleteId !== null && remove(confirmDeleteId)}
        onCancel={() => setConfirmDeleteId(null)}
      />
      <AlertDialog
        open={errorMessage !== null}
        message={errorMessage ?? ''}
        onClose={() => setErrorMessage(null)}
      />
    </div>
  )
}
