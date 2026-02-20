import { useEffect, useMemo, useState } from 'react'
import { api } from '../api/client'
import type { Payment, BuyingGroup, Order, Item, Shipment } from '../api/types'

function parseDecimal(s: string | null | undefined): number {
  if (s == null || s === '') return 0
  const n = parseFloat(String(s).replace(/,/g, ''))
  return Number.isFinite(n) ? n : 0
}

function formatMoney(n: number): string {
  return n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export default function Payments() {
  const [payments, setPayments] = useState<Payment[]>([])
  const [buyingGroups, setBuyingGroups] = useState<BuyingGroup[]>([])
  const [loading, setLoading] = useState(true)
  const [filterGroupId, setFilterGroupId] = useState<number | ''>('')

  const [addPaymentOpen, setAddPaymentOpen] = useState(false)
  const [addPaymentGroupId, setAddPaymentGroupId] = useState<number | ''>('')
  const [addPaymentId, setAddPaymentId] = useState('')
  const [receivedItems, setReceivedItems] = useState<Item[]>([])
  const [shipments, setShipments] = useState<Shipment[]>([])
  const [itemsLoading, setItemsLoading] = useState(false)
  const [selectedItemIds, setSelectedItemIds] = useState<Set<number>>(new Set())
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)

  const [editPayment, setEditPayment] = useState<Payment | null>(null)
  const [editPaymentId, setEditPaymentId] = useState('')
  const [editScannedItems, setEditScannedItems] = useState<Item[]>([])
  const [editShipments, setEditShipments] = useState<Shipment[]>([])
  const [editItemsLoading, setEditItemsLoading] = useState(false)
  const [editSelectedToAdd, setEditSelectedToAdd] = useState<Set<number>>(new Set())
  const [editSaving, setEditSaving] = useState(false)
  const [editError, setEditError] = useState<string | null>(null)

  const [deleteConfirmPayment, setDeleteConfirmPayment] = useState<Payment | null>(null)
  const [deleting, setDeleting] = useState(false)

  const itemIdToTracking = useMemo(() => {
    const acc: Record<number, string> = {}
    for (const s of shipments) {
      const tn = s.tracking_number?.trim()
      if (tn && s.shipment_items)
        for (const si of s.shipment_items) acc[si.item_id] = tn
    }
    return acc
  }, [shipments])

  const itemIdsOnPayments = useMemo(() => {
    const ids = new Set<number>()
    for (const p of payments) {
      for (const li of p.line_items ?? []) ids.add(li.item_id)
    }
    return ids
  }, [payments])

  const availableItems = useMemo(
    () => receivedItems.filter((i) => !itemIdsOnPayments.has(i.id)),
    [receivedItems, itemIdsOnPayments]
  )

  const selectedTotal = useMemo(() => {
    let sum = 0
    for (const id of selectedItemIds) {
      const item = receivedItems.find((i) => i.id === id)
      if (item) {
        const qty = item.quantity || 1
        sum += parseDecimal(item.price_sold) * qty
      }
    }
    return sum
  }, [selectedItemIds, receivedItems])

  const editItemIdToTracking = useMemo(() => {
    const acc: Record<number, string> = {}
    for (const s of editShipments) {
      const tn = s.tracking_number?.trim()
      if (tn && s.shipment_items)
        for (const si of s.shipment_items) acc[si.item_id] = tn
    }
    return acc
  }, [editShipments])

  const editPaymentItemIds = useMemo(
    () => new Set((editPayment?.line_items ?? []).map((li) => li.item_id)),
    [editPayment]
  )
  const editAvailableToAdd = useMemo(
    () => editScannedItems.filter((i) => !editPaymentItemIds.has(i.id)),
    [editScannedItems, editPaymentItemIds]
  )
  const editPaymentTotal = useMemo(() => {
    if (!editPayment) return 0
    let sum = 0
    for (const li of editPayment.line_items ?? []) {
      const item = li.item
      if (item) {
        const qty = item.quantity || 1
        sum += parseDecimal(item.price_sold) * qty
      }
    }
    return sum
  }, [editPayment])

  useEffect(() => {
    const params = filterGroupId === '' ? '' : `?buying_group_id=${filterGroupId}`
    api
      .get<Payment[]>(`/payments${params}`)
      .then(setPayments)
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [filterGroupId])

  useEffect(() => {
    api.get<BuyingGroup[]>('/buying-groups').then(setBuyingGroups).catch(console.error)
  }, [])

  useEffect(() => {
    if (!addPaymentOpen) return
    setReceivedItems([])
    setSelectedItemIds(new Set())
    setCreateError(null)
    if (addPaymentGroupId === '') return
    setItemsLoading(true)
    const params = new URLSearchParams()
    params.set('status', 'scanned')
    params.append('buying_group_id', String(addPaymentGroupId))
    Promise.all([api.get<Order[]>(`/orders?${params}`), api.get<Shipment[]>('/shipments')])
      .then(([ordersData, shipmentsData]) => {
        setShipments(shipmentsData)
        const items = ordersData.flatMap((o) => o.items ?? [])
        setReceivedItems(items)
        const idsOnPayments = new Set(
          payments.flatMap((p) => (p.line_items ?? []).map((li) => li.item_id))
        )
        setSelectedItemIds(
          new Set(items.filter((i) => !idsOnPayments.has(i.id)).map((i) => i.id))
        )
      })
      .catch(console.error)
      .finally(() => setItemsLoading(false))
  }, [addPaymentOpen, addPaymentGroupId, payments])

  useEffect(() => {
    if (!editPayment) return
    setEditScannedItems([])
    setEditShipments([])
    setEditSelectedToAdd(new Set())
    setEditError(null)
    setEditItemsLoading(true)
    const groupId = editPayment.buying_group_id
    const params = new URLSearchParams()
    params.set('status', 'scanned')
    params.append('buying_group_id', String(groupId))
    Promise.all([api.get<Order[]>(`/orders?${params}`), api.get<Shipment[]>('/shipments')])
      .then(([ordersData, shipmentsData]) => {
        setEditShipments(shipmentsData)
        const items = ordersData.flatMap((o) => o.items ?? [])
        setEditScannedItems(items)
      })
      .catch(console.error)
      .finally(() => setEditItemsLoading(false))
  }, [editPayment?.id])

  useEffect(() => {
    if (editPayment) setEditPaymentId(editPayment.payment_id ?? '')
  }, [editPayment])

  const saveEditPaymentId = async () => {
    if (!editPayment) return
    const value = editPaymentId.trim() || null
    if (value === (editPayment.payment_id ?? '')) return
    setEditSaving(true)
    setEditError(null)
    try {
      const updated = await api.patch<Payment>(`/payments/${editPayment.id}`, { payment_id: value })
      setEditPayment(updated)
      const params = filterGroupId === '' ? '' : `?buying_group_id=${filterGroupId}`
      const list = await api.get<Payment[]>(`/payments${params}`)
      setPayments(list)
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      setEditError(msg)
    } finally {
      setEditSaving(false)
    }
  }

  const toggleItemSelection = (id: number) => {
    if (itemIdsOnPayments.has(id)) return
    setSelectedItemIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const selectAllAvailable = () => {
    setSelectedItemIds(new Set(availableItems.map((i) => i.id)))
  }

  const createPayment = async () => {
    if (addPaymentGroupId === '' || selectedItemIds.size === 0) return
    setCreating(true)
    setCreateError(null)
    try {
      const payment = await api.post<Payment>('/payments', {
        buying_group_id: addPaymentGroupId,
        payment_id: addPaymentId.trim() || null,
      })
      for (const itemId of selectedItemIds) {
        await api.post(`/payments/${payment.id}/line-items`, { item_id: itemId })
      }
      const params = filterGroupId === '' ? '' : `?buying_group_id=${filterGroupId}`
      const list = await api.get<Payment[]>(`/payments${params}`)
      setPayments(list)
      setAddPaymentOpen(false)
      setAddPaymentGroupId('')
      setAddPaymentId('')
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      setCreateError(msg)
    } finally {
      setCreating(false)
    }
  }

  const removePaymentLineItem = async (paymentId: number, lineItemId: number) => {
    if (!editPayment) return
    setEditSaving(true)
    setEditError(null)
    try {
      await api.delete(`/payments/${paymentId}/line-items/${lineItemId}`)
      const params = filterGroupId === '' ? '' : `?buying_group_id=${filterGroupId}`
      const list = await api.get<Payment[]>(`/payments${params}`)
      setPayments(list)
      try {
        const updated = await api.get<Payment>(`/payments/${paymentId}`)
        setEditPayment(updated)
      } catch {
        setEditPayment(null)
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      setEditError(msg)
    } finally {
      setEditSaving(false)
    }
  }

  const addSelectedToEditPayment = async () => {
    if (!editPayment || editSelectedToAdd.size === 0) return
    setEditSaving(true)
    setEditError(null)
    try {
      for (const itemId of editSelectedToAdd) {
        await api.post(`/payments/${editPayment.id}/line-items`, { item_id: itemId })
      }
      const updated = await api.get<Payment>(`/payments/${editPayment.id}`)
      setEditPayment(updated)
      setEditSelectedToAdd(new Set())
      const params = filterGroupId === '' ? '' : `?buying_group_id=${filterGroupId}`
      const list = await api.get<Payment[]>(`/payments${params}`)
      setPayments(list)
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      setEditError(msg)
    } finally {
      setEditSaving(false)
    }
  }

  const toggleEditSelectedToAdd = (id: number) => {
    setEditSelectedToAdd((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const confirmDeletePayment = async () => {
    if (!deleteConfirmPayment) return
    setDeleting(true)
    try {
      await api.delete(`/payments/${deleteConfirmPayment.id}`)
      const params = filterGroupId === '' ? '' : `?buying_group_id=${filterGroupId}`
      const list = await api.get<Payment[]>(`/payments${params}`)
      setPayments(list)
      setDeleteConfirmPayment(null)
    } catch (e: unknown) {
      console.error(e)
    } finally {
      setDeleting(false)
    }
  }

  if (loading) return <div className="text-ink-muted">Loading payments…</div>

  return (
    <div>
      <h1 className="text-2xl font-semibold text-ink mb-8">Payments</h1>
      <div className="mb-4 flex items-center gap-4">
        <div className="flex items-center gap-2">
          <label htmlFor="filter-group" className="text-sm text-ink-muted">
            Buying group:
          </label>
          <select
            id="filter-group"
            className="rounded-lg border border-brand-200 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm text-ink"
            value={filterGroupId}
            onChange={(e) => setFilterGroupId(e.target.value === '' ? '' : Number(e.target.value))}
          >
            <option value="">All</option>
            {buyingGroups.map((g) => (
              <option key={g.id} value={g.id}>
                {g.name}
              </option>
            ))}
          </select>
        </div>
        <button
          type="button"
          onClick={() => {
            setAddPaymentOpen(true)
            setAddPaymentId('')
          }}
          className="rounded-lg bg-brand-600 hover:bg-brand-700 text-white px-4 py-2 text-sm font-medium"
        >
          Add payment
        </button>
      </div>
      <div className="bg-white dark:bg-gray-800 rounded-xl border border-brand-200/80 dark:border-gray-700 shadow-sm overflow-hidden">
        <table className="w-full">
          <thead className="bg-brand-100/50 dark:bg-gray-700/50 border-b border-brand-200/80 dark:border-gray-700">
            <tr>
              <th className="text-left py-3 px-4 text-sm font-medium text-ink">Buying group</th>
              <th className="text-left py-3 px-4 text-sm font-medium text-ink">Payment ID</th>
              <th className="text-left py-3 px-4 text-sm font-medium text-ink">Items</th>
              <th className="text-left py-3 px-4 text-sm font-medium text-ink">Amount</th>
              <th className="text-left py-3 px-4 text-sm font-medium text-ink">Created</th>
              <th className="text-left py-3 px-4 text-sm font-medium text-ink">Actions</th>
            </tr>
          </thead>
          <tbody>
            {payments.length === 0 ? (
              <tr>
                <td colSpan={6} className="py-12 text-center text-ink-muted">
                  No payments yet.
                </td>
              </tr>
            ) : (
              payments.map((p) => (
                <tr key={p.id} className="border-b border-brand-100 last:border-0 hover:bg-brand-50/50 dark:hover:bg-gray-700/30">
                  <td className="py-3 px-4 text-sm">{p.buying_group?.name ?? '—'}</td>
                  <td className="py-3 px-4 text-sm font-mono">{p.payment_id ?? '—'}</td>
                  <td className="py-3 px-4 text-sm">{p.line_items?.length ?? 0}</td>
                  <td className="py-3 px-4 text-sm">
                    {formatMoney(
                      (p.line_items ?? []).reduce(
                        (sum, li) =>
                          sum +
                          parseDecimal(li.item?.price_sold) * (li.item?.quantity ?? 1),
                        0
                      )
                    )}
                  </td>
                  <td className="py-3 px-4 text-sm text-ink-muted">
                    {p.created_at ? new Date(p.created_at).toLocaleString() : '—'}
                  </td>
                  <td className="py-3 px-4">
                    <div className="flex items-center gap-1">
                      <button
                        type="button"
                        onClick={() => {
                          api.get<Payment>(`/payments/${p.id}`).then(setEditPayment).catch(console.error)
                        }}
                        className="p-1.5 rounded text-ink-muted hover:text-brand-600 hover:bg-brand-50 dark:hover:bg-brand-900/20 transition"
                        title="Edit payment"
                        aria-label="Edit payment"
                      >
                        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                        </svg>
                      </button>
                      <button
                        type="button"
                        onClick={() => setDeleteConfirmPayment(p)}
                        className="p-1.5 rounded text-ink-muted hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 transition"
                        title="Delete payment"
                        aria-label="Delete payment"
                      >
                        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                        </svg>
                      </button>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {addPaymentOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
          onClick={() => !creating && setAddPaymentOpen(false)}
        >
          <div
            className="bg-white dark:bg-gray-800 rounded-xl border border-brand-200/80 dark:border-gray-700 shadow-xl max-w-4xl w-full max-h-[90vh] flex flex-col m-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="p-4 border-b border-brand-200/80 dark:border-gray-700">
              <h2 className="text-lg font-semibold text-ink">Add payment</h2>
            </div>
            <div className="p-4 flex flex-col gap-4 overflow-auto min-h-0">
              <div>
                <label htmlFor="add-payment-group" className="block text-sm font-medium text-ink mb-1">
                  Buying group
                </label>
                <select
                  id="add-payment-group"
                  className="rounded-lg border border-brand-200 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm text-ink w-full max-w-xs"
                  value={addPaymentGroupId}
                  onChange={(e) => setAddPaymentGroupId(e.target.value === '' ? '' : Number(e.target.value))}
                >
                  <option value="">Select a buying group…</option>
                  {buyingGroups.map((g) => (
                    <option key={g.id} value={g.id}>
                      {g.name}
                    </option>
                  ))}
                </select>
              </div>

              {addPaymentGroupId !== '' && (
                <>
              <div>
                <label htmlFor="add-payment-id" className="block text-sm font-medium text-ink mb-1">
                  Payment ID
                </label>
                <input
                  id="add-payment-id"
                  type="text"
                  className="rounded-lg border border-brand-200 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm text-ink w-full max-w-xs font-mono"
                  value={addPaymentId}
                  onChange={(e) => setAddPaymentId(e.target.value)}
                  placeholder="Optional external reference"
                />
              </div>

                  {itemsLoading ? (
                    <div className="text-ink-muted py-6">Loading scanned items…</div>
                  ) : availableItems.length === 0 ? (
                    <div className="text-ink-muted py-6">
                      No scanned items available for this buying group (or all are already on a payment).
                    </div>
                  ) : (
                    <>
                      <div className="flex items-center justify-between">
                        <button
                          type="button"
                          onClick={selectAllAvailable}
                          className="text-sm text-brand-600 hover:underline"
                        >
                          Select all
                        </button>
                      </div>
                      <div className="border border-brand-200/80 dark:border-gray-700 rounded-lg overflow-hidden">
                        <div className="overflow-x-auto max-h-[40vh]">
                          <table className="w-full text-sm">
                            <thead className="bg-brand-100/50 dark:bg-gray-700/50 sticky top-0">
                              <tr>
                                <th className="text-left py-2 px-2 w-10">
                                  <input
                                    type="checkbox"
                                    checked={
                                      availableItems.length > 0 &&
                                      availableItems.every((i) => selectedItemIds.has(i.id))
                                    }
                                    onChange={(e) =>
                                      e.target.checked ? selectAllAvailable() : setSelectedItemIds(new Set())
                                    }
                                  />
                                </th>
                                <th className="text-left py-2 px-2 font-medium text-ink-muted">Qty</th>
                                <th className="text-left py-2 px-2 font-medium text-ink-muted">Item name</th>
                                <th className="text-right py-2 px-2 font-medium text-ink-muted">Payout price</th>
                                <th className="text-right py-2 px-2 font-medium text-ink-muted">Total price</th>
                                <th className="text-left py-2 px-2 font-medium text-ink-muted">Receipt ID</th>
                                <th className="text-left py-2 px-2 font-medium text-ink-muted">Tracking</th>
                              </tr>
                            </thead>
                            <tbody>
                              {availableItems.map((item) => {
                                const qty = item.quantity || 1
                                const payout = parseDecimal(item.price_sold)
                                const total = payout * qty
                                const disabled = itemIdsOnPayments.has(item.id)
                                return (
                                  <tr
                                    key={item.id}
                                    className={`border-t border-brand-100 dark:border-gray-700 ${disabled ? 'opacity-50' : 'hover:bg-brand-50/50 dark:hover:bg-gray-700/30'}`}
                                  >
                                    <td className="py-2 px-2">
                                      <input
                                        type="checkbox"
                                        checked={selectedItemIds.has(item.id)}
                                        disabled={disabled}
                                        onChange={() => toggleItemSelection(item.id)}
                                      />
                                    </td>
                                    <td className="py-2 px-2 text-ink">{qty}</td>
                                    <td className="py-2 px-2 text-ink">{item.description ?? '—'}</td>
                                    <td className="py-2 px-2 text-right text-ink">${formatMoney(payout)}</td>
                                    <td className="py-2 px-2 text-right text-ink">${formatMoney(total)}</td>
                                    <td className="py-2 px-2 text-ink font-mono">{item.receipt_id ?? '—'}</td>
                                    <td className="py-2 px-2 text-ink font-mono">{itemIdToTracking[item.id] ?? '—'}</td>
                                  </tr>
                                )
                              })}
                            </tbody>
                            <tfoot className="bg-brand-50/50 dark:bg-gray-700/30 border-t border-brand-200/80 dark:border-gray-700">
                              <tr>
                                <td className="py-2 px-2" />
                                <td className="py-2 px-2" />
                                <td className="py-2 px-2" />
                                <td className="py-2 px-2" />
                                <td className="py-2 px-2 text-right font-medium text-ink">${formatMoney(selectedTotal)}</td>
                                <td className="py-2 px-2" />
                                <td className="py-2 px-2" />
                              </tr>
                            </tfoot>
                          </table>
                        </div>
                      </div>
                    </>
                  )}
                </>
              )}
            </div>
            {createError && (
              <div className="px-4 pb-2 text-red-600 dark:text-red-400 text-sm">
                {createError}
              </div>
            )}
            <div className="p-4 border-t border-brand-200/80 dark:border-gray-700 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => !creating && setAddPaymentOpen(false)}
                className="rounded-lg border border-brand-200 dark:border-gray-600 px-4 py-2 text-sm text-ink"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={createPayment}
                disabled={creating || selectedItemIds.size === 0}
                className="rounded-lg bg-brand-600 hover:bg-brand-700 disabled:opacity-50 text-white px-4 py-2 text-sm font-medium"
              >
                {creating ? 'Creating…' : 'Create payment'}
              </button>
            </div>
          </div>
        </div>
      )}

      {editPayment && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
          onClick={() => !editSaving && setEditPayment(null)}
        >
          <div
            className="bg-white dark:bg-gray-800 rounded-xl border border-brand-200/80 dark:border-gray-700 shadow-xl max-w-4xl w-full max-h-[90vh] flex flex-col m-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="p-4 border-b border-brand-200/80 dark:border-gray-700">
              <h2 className="text-lg font-semibold text-ink">
                Edit payment — {editPayment.buying_group?.name ?? ''}
              </h2>
            </div>
            <div className="p-4 flex flex-col gap-6 overflow-auto min-h-0">
              <section>
                <label htmlFor="edit-payment-id" className="block text-sm font-medium text-ink mb-1">
                  Payment ID
                </label>
                <div className="flex items-center gap-2">
                  <input
                    id="edit-payment-id"
                    type="text"
                    className="rounded-lg border border-brand-200 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm text-ink font-mono flex-1 max-w-xs"
                    value={editPaymentId}
                    onChange={(e) => setEditPaymentId(e.target.value)}
                    onBlur={saveEditPaymentId}
                    placeholder="Optional external reference"
                  />
                </div>
              </section>
              <section>
                <h3 className="text-sm font-medium text-ink mb-2">Items on this payment</h3>
                {(editPayment.line_items?.length ?? 0) === 0 ? (
                  <p className="text-sm text-ink-muted">No items on this payment.</p>
                ) : (
                  <div className="border border-brand-200/80 dark:border-gray-700 rounded-lg overflow-hidden">
                    <div className="overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead className="bg-brand-100/50 dark:bg-gray-700/50">
                          <tr>
                            <th className="text-left py-2 px-2 font-medium text-ink-muted">Qty</th>
                            <th className="text-left py-2 px-2 font-medium text-ink-muted">Item name</th>
                            <th className="text-right py-2 px-2 font-medium text-ink-muted">Payout</th>
                            <th className="text-right py-2 px-2 font-medium text-ink-muted">Total</th>
                            <th className="text-left py-2 px-2 font-medium text-ink-muted">Receipt ID</th>
                            <th className="text-left py-2 px-2 font-medium text-ink-muted">Tracking</th>
                            <th className="w-20" />
                          </tr>
                        </thead>
                        <tbody>
                          {(editPayment.line_items ?? []).map((li) => {
                            const item = li.item
                            if (!item) return null
                            const qty = item.quantity || 1
                            const payout = parseDecimal(item.price_sold)
                            const total = payout * qty
                            return (
                              <tr key={li.id} className="border-t border-brand-100 dark:border-gray-700">
                                <td className="py-2 px-2 text-ink">{qty}</td>
                                <td className="py-2 px-2 text-ink">{item.description ?? '—'}</td>
                                <td className="py-2 px-2 text-right text-ink">${formatMoney(payout)}</td>
                                <td className="py-2 px-2 text-right text-ink">${formatMoney(total)}</td>
                                <td className="py-2 px-2 text-ink font-mono">{item.receipt_id ?? '—'}</td>
                                <td className="py-2 px-2 text-ink font-mono">{editItemIdToTracking[item.id] ?? '—'}</td>
                                <td className="py-2 px-2">
                                  <button
                                    type="button"
                                    onClick={() => removePaymentLineItem(editPayment.id, li.id)}
                                    disabled={editSaving}
                                    className="text-sm text-red-600 hover:underline disabled:opacity-50"
                                  >
                                    Remove
                                  </button>
                                </td>
                              </tr>
                            )
                          })}
                        </tbody>
                        <tfoot className="bg-brand-50/50 dark:bg-gray-700/30 border-t border-brand-200/80 dark:border-gray-700">
                          <tr>
                            <td className="py-2 px-2" colSpan={3} />
                            <td className="py-2 px-2 text-right font-medium text-ink">${formatMoney(editPaymentTotal)}</td>
                            <td className="py-2 px-2" colSpan={3} />
                          </tr>
                        </tfoot>
                      </table>
                    </div>
                  </div>
                )}
              </section>

              <section>
                <h3 className="text-sm font-medium text-ink mb-2">Scanned items in this group (not on payment)</h3>
                {editItemsLoading ? (
                  <p className="text-sm text-ink-muted">Loading…</p>
                ) : editAvailableToAdd.length === 0 ? (
                  <p className="text-sm text-ink-muted">No other scanned items in this group.</p>
                ) : (
                  <>
                    <div className="border border-brand-200/80 dark:border-gray-700 rounded-lg overflow-hidden">
                      <div className="overflow-x-auto max-h-[30vh]">
                        <table className="w-full text-sm">
                          <thead className="bg-brand-100/50 dark:bg-gray-700/50 sticky top-0">
                            <tr>
                              <th className="text-left py-2 px-2 w-10" />
                              <th className="text-left py-2 px-2 font-medium text-ink-muted">Qty</th>
                              <th className="text-left py-2 px-2 font-medium text-ink-muted">Item name</th>
                              <th className="text-right py-2 px-2 font-medium text-ink-muted">Payout</th>
                              <th className="text-right py-2 px-2 font-medium text-ink-muted">Total</th>
                              <th className="text-left py-2 px-2 font-medium text-ink-muted">Receipt ID</th>
                              <th className="text-left py-2 px-2 font-medium text-ink-muted">Tracking</th>
                            </tr>
                          </thead>
                          <tbody>
                            {editAvailableToAdd.map((item) => {
                              const qty = item.quantity || 1
                              const payout = parseDecimal(item.price_sold)
                              const total = payout * qty
                              return (
                                <tr key={item.id} className="border-t border-brand-100 dark:border-gray-700 hover:bg-brand-50/50 dark:hover:bg-gray-700/30">
                                  <td className="py-2 px-2">
                                    <input
                                      type="checkbox"
                                      checked={editSelectedToAdd.has(item.id)}
                                      onChange={() => toggleEditSelectedToAdd(item.id)}
                                    />
                                  </td>
                                  <td className="py-2 px-2 text-ink">{qty}</td>
                                  <td className="py-2 px-2 text-ink">{item.description ?? '—'}</td>
                                  <td className="py-2 px-2 text-right text-ink">${formatMoney(payout)}</td>
                                  <td className="py-2 px-2 text-right text-ink">${formatMoney(total)}</td>
                                  <td className="py-2 px-2 text-ink font-mono">{item.receipt_id ?? '—'}</td>
                                  <td className="py-2 px-2 text-ink font-mono">{editItemIdToTracking[item.id] ?? '—'}</td>
                                </tr>
                              )
                            })}
                          </tbody>
                        </table>
                      </div>
                    </div>
                    <div className="mt-2 flex justify-end">
                      <button
                        type="button"
                        onClick={addSelectedToEditPayment}
                        disabled={editSaving || editSelectedToAdd.size === 0}
                        className="rounded-lg bg-brand-600 hover:bg-brand-700 disabled:opacity-50 text-white px-4 py-2 text-sm font-medium"
                      >
                        {editSaving ? 'Adding…' : `Add selected (${editSelectedToAdd.size})`}
                      </button>
                    </div>
                  </>
                )}
              </section>
            </div>
            {editError && (
              <div className="px-4 pb-2 text-red-600 dark:text-red-400 text-sm">{editError}</div>
            )}
            <div className="p-4 border-t border-brand-200/80 dark:border-gray-700 flex justify-end">
              <button
                type="button"
                onClick={() => !editSaving && setEditPayment(null)}
                className="rounded-lg border border-brand-200 dark:border-gray-600 px-4 py-2 text-sm text-ink"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {deleteConfirmPayment && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
          onClick={() => !deleting && setDeleteConfirmPayment(null)}
        >
          <div
            className="bg-white dark:bg-gray-800 rounded-xl border border-brand-200/80 dark:border-gray-700 shadow-xl p-6 max-w-md w-full m-4"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="text-lg font-semibold text-ink mb-2">Delete payment?</h2>
            <p className="text-sm text-ink-muted mb-6">
              This payment ({deleteConfirmPayment.buying_group?.name ?? ''}) will be permanently
              deleted. This cannot be undone.
            </p>
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => !deleting && setDeleteConfirmPayment(null)}
                className="rounded-lg border border-brand-200 dark:border-gray-600 px-4 py-2 text-sm text-ink"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={confirmDeletePayment}
                disabled={deleting}
                className="rounded-lg bg-red-600 hover:bg-red-700 disabled:opacity-50 text-white px-4 py-2 text-sm font-medium"
              >
                {deleting ? 'Deleting…' : 'Delete'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
