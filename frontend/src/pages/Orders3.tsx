/**
 * Test page: Option 3 layout — horizontal strip per order.
 * Left: vertical order box (order #, store, account, group, date, payment).
 * Right: compact item list (one line per item: qty× description — cost | payout — tracking — status).
 */
import React, { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { SearchableCombobox } from '../components/SearchableCombobox'
import type {
  Order,
  BuyingGroup,
  Store,
  StoreAccount,
  PaymentMethod,
  PaymentMethodNested,
  Item,
} from '../api/types'

function defaultOrdersPath(): string {
  const statuses = [
    'purchased', 'shipped', 'submitted', 'delivered', 'scanned',
    'payment_requested', 'payment_sent', 'payment_received',
    'needs_return', 'return_started', 'return_sent', 'return_received', 'return_refunded',
  ]
  const params = new URLSearchParams()
  statuses.forEach((s) => params.append('status', s))
  return `/orders?${params.toString()}`
}

const STATUS_LABELS: Record<string, string> = {
  purchased: 'Purchased',
  shipped: 'Shipped',
  submitted: 'Submitted',
  delivered: 'Delivered',
  scanned: 'Scanned',
  payment_requested: 'Payment requested',
  payment_sent: 'Payment sent',
  payment_received: 'Paid',
  paid: 'Paid',
  canceled: 'Canceled',
  needs_return: 'Needs return',
  return_started: 'Return started',
  return_sent: 'Return sent',
  return_received: 'Return received',
  return_refunded: 'Refunded',
  returned: 'Returned',
  refunded: 'Refunded',
}

export default function Orders3() {
  const [orders, setOrders] = useState<Order[]>([])
  const [groups, setGroups] = useState<BuyingGroup[]>([])
  const [loading, setLoading] = useState(true)
  const [shipments, setShipments] = useState<{ id: number; shipment_items: { item_id: number }[]; tracking_number: string | null }[]>([])
  const [copyingId, setCopyingId] = useState<number | null>(null)
  const [stores, setStores] = useState<Store[]>([])
  const [paymentMethods, setPaymentMethods] = useState<PaymentMethod[]>([])
  const [accountsByStore, setAccountsByStore] = useState<Record<number, StoreAccount[]>>({})
  const [orderEdits, setOrderEdits] = useState<Record<number, { store_order_number?: string; shipping?: string; sales_tax?: string }>>({})
  const [savingOrderId, setSavingOrderId] = useState<number | null>(null)
  const [paymentEdits, setPaymentEdits] = useState<Record<number, { payment_method_id: number; amount: string }[]>>({})
  const paymentSaveTimeouts = useRef<Record<number, ReturnType<typeof setTimeout>>>({})
  const navigate = useNavigate()

  const loadAccountsForStore = (storeId: number) =>
    api.get<StoreAccount[]>(`/stores/${storeId}/accounts`).then((list) => {
      setAccountsByStore((prev) => ({ ...prev, [storeId]: list }))
    })

  useEffect(() => {
    setLoading(true)
    Promise.all([
      api.get<Order[]>(defaultOrdersPath()),
      api.get<BuyingGroup[]>('/buying-groups'),
      api.get<{ id: number; shipment_items: { item_id: number }[]; tracking_number: string | null }[]>('/shipments'),
      api.get<Store[]>('/stores'),
      api.get<PaymentMethod[]>('/payment-methods'),
    ])
      .then(([ordersData, groupsData, shipmentsData, storesData, pmData]) => {
        setOrders(ordersData)
        setGroups(groupsData)
        setShipments(shipmentsData)
        setStores(storesData)
        setPaymentMethods(pmData)
        const storeIds = [...new Set(ordersData.map((o) => o.store_id).filter(Boolean))]
        storeIds.forEach((sid) =>
          api.get<StoreAccount[]>(`/stores/${sid}/accounts`).then((list) => {
            setAccountsByStore((prev) => ({ ...prev, [sid]: list }))
          })
        )
      })
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [])

  const flatPaymentMethods = useMemo(
    () => paymentMethods.flatMap((pm) => [pm, ...(pm.sub_methods ?? [])]),
    [paymentMethods]
  )
  const paymentMethodOptions = useMemo(() => {
    return flatPaymentMethods.map((pm: PaymentMethod | PaymentMethodNested) => {
      const parent = pm.parent_id != null ? paymentMethods.find((p) => p.id === pm.parent_id) : null
      const name = parent ? `${parent.label} — ${pm.label}` : pm.label
      return { id: pm.id, name }
    })
  }, [paymentMethods, flatPaymentMethods])

  const itemIdToTracking = useMemo(
    () =>
      shipments.reduce<Record<number, string>>((acc, s) => {
        const tn = s.tracking_number?.trim()
        if (tn) for (const si of s.shipment_items || []) acc[si.item_id] = tn
        return acc
      }, {}),
    [shipments]
  )
  const getTracking = (itemId: number) => itemIdToTracking[itemId] ?? ''

  const updateOrder = async (orderId: number, data: Partial<Order>) => {
    setSavingOrderId(orderId)
    try {
      const updated = await api.patch<Order>(`/orders/${orderId}`, data)
      setOrders((prev) => prev.map((o) => (o.id === orderId ? updated : o)))
    } catch (e) {
      console.error(e)
    } finally {
      setSavingOrderId(null)
    }
  }

  const updateOrderPayments = async (orderId: number, payment_methods: { payment_method_id: number; amount: string | null }[]) => {
    setSavingOrderId(orderId)
    try {
      const payload = payment_methods.map((pm) => ({ payment_method_id: pm.payment_method_id, amount: pm.amount?.trim() || null }))
      const updated = await api.patch<Order>(`/orders/${orderId}`, { payment_methods: payload })
      setOrders((prev) => prev.map((o) => (o.id === orderId ? updated : o)))
    } catch (e) {
      console.error(e)
    } finally {
      setSavingOrderId(null)
    }
  }

  useEffect(() => {
    const orderIds = Object.keys(paymentEdits).map(Number)
    orderIds.forEach((orderId) => {
      if (savingOrderId === orderId) return
      const order = orders.find((o) => o.id === orderId)
      if (!order || !paymentEdits[orderId]?.length) return
      const rows = paymentEdits[orderId]!
      const totalPaid = orderTotals(order.items ?? [], order.shipping, order.sales_tax)
      const paymentSum = rows.reduce((s, r) => s + parseDecimal(r.amount), 0)
      const usedIds = new Set(rows.map((r) => r.payment_method_id))
      const canSave =
        rows.length > 0 &&
        Math.abs(paymentSum - totalPaid) < 0.01 &&
        usedIds.size === rows.length
      if (!canSave) return
      const serverRows = (order.order_payments ?? []).map((op) => ({
        payment_method_id: op.payment_method_id,
        amount: op.amount != null ? String(op.amount) : '',
      }))
      const hasChanges =
        rows.length !== serverRows.length ||
        rows.some((r, i) => serverRows[i]?.payment_method_id !== r.payment_method_id || (serverRows[i]?.amount ?? '') !== (r.amount ?? ''))
      if (!hasChanges) return
      const t = paymentSaveTimeouts.current[orderId]
      if (t) clearTimeout(t)
      paymentSaveTimeouts.current[orderId] = setTimeout(() => {
        delete paymentSaveTimeouts.current[orderId]
        updateOrderPayments(orderId, rows.map((r) => ({ payment_method_id: r.payment_method_id, amount: r.amount }))).then(() => {
          setPaymentEdits((prev) => { const next = { ...prev }; delete next[orderId]; return next })
        })
      }, 600)
    })
    return () => {
      orderIds.forEach((id) => {
        const t = paymentSaveTimeouts.current[id]
        if (t) clearTimeout(t)
        delete paymentSaveTimeouts.current[id]
      })
    }
  }, [paymentEdits, orders, savingOrderId])

  const parseDecimal = (s: string | null | undefined): number => {
    if (s == null || String(s).trim() === '') return 0
    const n = parseFloat(String(s))
    return Number.isNaN(n) ? 0 : n
  }
  const orderTotals = (
    items: { price_paid?: string | null; quantity?: number }[],
    orderShipping?: string | null,
    orderSalesTax?: string | null
  ) => {
    let totalPaid = 0
    for (const item of items) {
      const qty = Math.max(0, item.quantity ?? 1)
      totalPaid += parseDecimal(item.price_paid) * qty
    }
    totalPaid += parseDecimal(orderShipping) + parseDecimal(orderSalesTax)
    return totalPaid
  }

  const nowIso = () => new Date().toISOString().slice(0, 19)
  const copyOrder = async (o: Order, e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (!o.items?.length) return
    setCopyingId(o.id)
    try {
      const created = await api.post<Order>('/orders', {
        store_id: o.store_id,
        store_account_id: o.store_account_id ?? null,
        store_order_number: null,
        purchase_date: o.purchase_date ?? nowIso(),
        notes: o.notes ?? undefined,
        buying_group_id: o.buying_group_id ?? null,
        payment_methods: (o.order_payments ?? []).map((op) => ({ payment_method_id: op.payment_method_id, amount: op.amount ?? undefined })),
        items: o.items.map((item) => ({
          price_paid: item.price_paid ?? undefined,
          price_sold: item.price_sold ?? undefined,
          status: item.status,
          quantity: item.quantity ?? 1,
          description: item.description ?? undefined,
        })),
        shipping: o.shipping ?? undefined,
        sales_tax: o.sales_tax ?? undefined,
      })
      navigate(`/orders/${created.id}`)
    } catch (err) {
      console.error(err)
    } finally {
      setCopyingId(null)
    }
  }

  if (loading) return <div className="text-ink-muted">Loading orders…</div>

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-semibold text-ink dark:text-gray-100">Orders (Option 3)</h1>
          <Link to="/" className="text-sm text-brand-600 dark:text-brand-400 hover:underline">Back to Orders</Link>
        </div>
        <Link
          to="/orders/new"
          className="px-4 py-2 bg-brand-600 text-white rounded-lg font-medium hover:bg-brand-700 transition shrink-0"
        >
          New order
        </Link>
      </div>

      <div className="space-y-4">
        {orders.length === 0 ? (
          <div className="bg-white dark:bg-gray-800 rounded-xl border border-brand-200/80 dark:border-gray-700 py-12 text-center text-ink-muted">
            No orders yet. Create one to get started.
          </div>
        ) : (
          orders.map((o) => {
            const paymentRows =
              paymentEdits[o.id] ??
              (o.order_payments ?? []).map((op) => ({
                payment_method_id: op.payment_method_id,
                amount: op.amount != null ? String(op.amount) : '',
              }))
            const totalPaid = orderTotals(o.items ?? [], o.shipping, o.sales_tax)
            const paymentSum = paymentRows.reduce((s, r) => s + parseDecimal(r.amount), 0)
            const paymentMatchesTotal =
              paymentRows.length > 0 && Math.round(paymentSum * 100) === Math.round(totalPaid * 100)
            const usedIds = new Set(paymentRows.map((r) => r.payment_method_id))
            const firstUnused = flatPaymentMethods.find((pm) => !usedIds.has(pm.id)) ?? flatPaymentMethods[0]
            const amountRemaining = Math.max(0, totalPaid - paymentSum)
            const addPaymentDisabled =
              savingOrderId === o.id || paymentSum >= totalPaid || flatPaymentMethods.length === 0
            const itemCount = o.items?.length ?? 0
            const lineCount = Math.max(1, itemCount)
            const lineHeight = 28
            const minRightHeight = lineCount * lineHeight + 24

            return (
              <div
                key={o.id}
                className="flex gap-0 border-2 border-brand-400 dark:border-gray-400 rounded-xl overflow-hidden bg-brand-50/50 dark:bg-gray-600/80"
                style={{ minHeight: Math.max(120, minRightHeight) }}
              >
                {/* Left: vertical order box (same as Orders2) */}
                <div
                  className="w-[260px] shrink-0 flex flex-col gap-2 p-3 border-r-2 border-brand-400 dark:border-gray-400 bg-white/80 dark:bg-gray-700/80"
                  style={{ minHeight: minRightHeight }}
                >
                  <div className="flex items-center gap-2">
                    <Link
                      to={`/orders/${o.id}`}
                      title="Edit order"
                      className="shrink-0 p-1 rounded text-ink-muted hover:text-brand-600 hover:bg-brand-50 dark:hover:bg-gray-600 dark:hover:text-brand-400 transition"
                      aria-label="Edit order"
                    >
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                      </svg>
                    </Link>
                    <input
                      type="text"
                      value={orderEdits[o.id]?.store_order_number ?? (o.store_order_number ?? '')}
                      onChange={(e) =>
                        setOrderEdits((prev) => ({ ...prev, [o.id]: { ...prev[o.id], store_order_number: e.target.value } }))
                      }
                      onBlur={(e) => {
                        const v = e.target.value.trim()
                        if (v !== (o.store_order_number ?? '')) updateOrder(o.id, { store_order_number: v || null })
                        setOrderEdits((prev) => {
                          const next = { ...prev }
                          if (next[o.id]) {
                            delete next[o.id].store_order_number
                            if (Object.keys(next[o.id]).length === 0) delete next[o.id]
                          }
                          return next
                        })
                      }}
                      placeholder="Order #"
                      disabled={savingOrderId === o.id}
                      className="flex-1 min-w-0 h-6 rounded border border-transparent bg-transparent px-1 py-0 text-sm font-medium text-brand-700 dark:text-brand-400 focus:border-brand-300 focus:bg-white dark:focus:bg-gray-700 focus:outline-none focus:ring-1 focus:ring-brand-500 disabled:opacity-60"
                    />
                  </div>
                  <div className="min-w-0">
                    <SearchableCombobox<Store>
                      inputClassName="h-6 py-0 px-2 text-sm rounded border border-brand-200 dark:border-gray-600 w-full min-w-0 text-ink focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent disabled:opacity-60 dark:bg-gray-700"
                      options={stores}
                      value={stores.find((s) => s.id === o.store_id) ?? null}
                      onChange={(s) => {
                        const storeId = s?.id ?? 0
                        if (!storeId) return
                        updateOrder(o.id, { store_id: storeId, store_account_id: null })
                        if (!accountsByStore[storeId]) loadAccountsForStore(storeId)
                      }}
                      onCreate={async (name) => {
                        const s = await api.post<Store>('/stores', { name })
                        setStores((prev) => [...prev, s].sort((a, b) => a.name.localeCompare(b.name)))
                        updateOrder(o.id, { store_id: s.id, store_account_id: null })
                        loadAccountsForStore(s.id)
                        return s
                      }}
                      placeholder="Store…"
                    />
                  </div>
                  <div className="min-w-0">
                    <SearchableCombobox<StoreAccount>
                      inputClassName="h-6 py-0 px-2 text-sm rounded border border-brand-200 dark:border-gray-600 w-full min-w-0 text-ink focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent disabled:opacity-60 dark:bg-gray-700"
                      options={accountsByStore[o.store_id] ?? []}
                      value={(accountsByStore[o.store_id] ?? []).find((a) => a.id === o.store_account_id) ?? null}
                      onChange={(a) => updateOrder(o.id, { store_account_id: a?.id ?? null })}
                      onCreate={
                        o.store_id
                          ? async (name) => {
                              const a = await api.post<StoreAccount>(`/stores/${o.store_id}/accounts`, { name })
                              setAccountsByStore((prev) => ({ ...prev, [o.store_id]: [...(prev[o.store_id] ?? []), a] }))
                              updateOrder(o.id, { store_account_id: a.id })
                              return a
                            }
                          : undefined
                      }
                      placeholder="Account…"
                      allowEmpty
                      disabled={!o.store_id || savingOrderId === o.id}
                    />
                  </div>
                  <div className="min-w-0">
                    <SearchableCombobox<BuyingGroup>
                      inputClassName="h-6 py-0 px-2 text-sm rounded border border-brand-200 dark:border-gray-600 w-full min-w-0 text-ink focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent disabled:opacity-60 dark:bg-gray-700"
                      options={groups}
                      value={groups.find((g) => g.id === o.buying_group_id) ?? null}
                      onChange={(g) => updateOrder(o.id, { buying_group_id: g?.id ?? null })}
                      onCreate={async (name) => {
                        const g = await api.post<BuyingGroup>('/buying-groups', { name })
                        setGroups((prev) => [...prev, g].sort((a, b) => a.name.localeCompare(b.name)))
                        updateOrder(o.id, { buying_group_id: g.id })
                        return g
                      }}
                      placeholder="Buying group…"
                      allowEmpty
                      disabled={savingOrderId === o.id}
                    />
                  </div>
                  <div className="text-xs text-ink-muted">
                    Purchase: {o.purchase_date ? new Date(o.purchase_date).toLocaleDateString() : '—'}
                  </div>
                  <div className="space-y-1">
                    <p className="text-xs font-medium text-ink-muted">
                      Total: <span className="font-mono text-ink">${totalPaid.toFixed(2)}</span>
                      {paymentRows.length > 0 && (
                        <>
                          {' — '}
                          <span className="font-mono">${paymentSum.toFixed(2)}</span>
                          {!paymentMatchesTotal && <span className="text-amber-600 ml-1">(match total)</span>}
                        </>
                      )}
                    </p>
                    {paymentRows.map((row, idx) => (
                      <div key={idx} className="flex items-center gap-1 flex-wrap">
                        <div className="min-w-0 flex-1">
                          <SearchableCombobox<{ id: number; name: string }>
                            inputClassName="h-6 py-0 px-2 text-sm rounded border border-brand-200 dark:border-gray-600 w-full min-w-0 text-ink focus:outline-none focus:ring-2 focus:ring-brand-500 dark:bg-gray-700"
                            options={paymentMethodOptions}
                            value={row.payment_method_id ? paymentMethodOptions.find((opt) => opt.id === row.payment_method_id) ?? null : null}
                            onChange={(opt) =>
                              setPaymentEdits((prev) => ({
                                ...prev,
                                [o.id]: paymentRows.map((r, i) => (i === idx ? { ...r, payment_method_id: opt?.id ?? 0 } : r)),
                              }))
                            }
                            onCreate={async (label) => {
                              const pm = await api.post<PaymentMethod>('/payment-methods', { label })
                              setPaymentMethods((prev) => [...prev, pm].sort((a, b) => a.label.localeCompare(b.label)))
                              setPaymentEdits((prev) => ({
                                ...prev,
                                [o.id]: paymentRows.map((r, i) => (i === idx ? { ...r, payment_method_id: pm.id } : r)),
                              }))
                              return { id: pm.id, name: pm.label }
                            }}
                            placeholder="Payment…"
                          />
                        </div>
                        <input
                          type="text"
                          placeholder="Amt"
                          value={row.amount}
                          onChange={(e) => {
                            const raw = e.target.value
                            const otherSum = paymentRows.reduce((s, r, i) => (i === idx ? s : s + parseDecimal(r.amount)), 0)
                            const maxAmount = Math.max(0, totalPaid - otherSum)
                            const num = parseDecimal(raw)
                            const amount =
                              raw.trim() === '' || String(raw) === '.' || Number.isNaN(parseFloat(raw))
                                ? raw
                                : num > maxAmount
                                  ? maxAmount.toFixed(2)
                                  : raw
                            setPaymentEdits((prev) => ({
                              ...prev,
                              [o.id]: paymentRows.map((r, i) => (i === idx ? { ...r, amount } : r)),
                            }))
                          }}
                          className="w-16 h-6 rounded border border-brand-200 dark:border-gray-600 px-1 py-0 text-sm font-mono !bg-brand-50/50 dark:!bg-gray-600/80"
                        />
                        <button
                          type="button"
                          onClick={() =>
                            setPaymentEdits((prev) => ({ ...prev, [o.id]: paymentRows.filter((_, i) => i !== idx) }))
                          }
                          className="p-0.5 rounded text-ink-muted hover:text-ink hover:bg-brand-100 dark:hover:bg-gray-600"
                          aria-label="Remove payment"
                        >
                          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                          </svg>
                        </button>
                      </div>
                    ))}
                    {paymentRows.length === 0 && (
                      <button
                        type="button"
                        disabled={addPaymentDisabled}
                        onClick={() =>
                          setPaymentEdits((prev) => ({
                            ...prev,
                            [o.id]: [
                              { payment_method_id: firstUnused?.id ?? 0, amount: amountRemaining > 0 ? amountRemaining.toFixed(2) : '' },
                            ],
                          }))
                        }
                        className="text-xs text-brand-600 dark:text-brand-400 hover:underline disabled:opacity-50"
                      >
                        Add payment
                      </button>
                    )}
                    {paymentRows.length > 0 && (
                      <button
                        type="button"
                        disabled={addPaymentDisabled}
                        onClick={() =>
                          setPaymentEdits((prev) => ({
                            ...prev,
                            [o.id]: [
                              ...paymentRows,
                              { payment_method_id: firstUnused?.id ?? 0, amount: amountRemaining > 0 ? amountRemaining.toFixed(2) : '' },
                            ],
                          }))
                        }
                        className="text-xs text-brand-600 dark:text-brand-400 hover:underline disabled:opacity-50"
                      >
                        + payment
                      </button>
                    )}
                  </div>
                  <div className="flex items-center gap-2 flex-wrap">
                    <label className="flex items-center gap-1 text-xs text-ink-muted">
                      Ship <input
                        type="text"
                        value={orderEdits[o.id]?.shipping ?? (o.shipping ?? '')}
                        onChange={(e) =>
                          setOrderEdits((prev) => ({ ...prev, [o.id]: { ...prev[o.id], shipping: e.target.value } }))
                        }
                        onBlur={(e) => {
                          const v = e.target.value.trim()
                          if (v !== (o.shipping ?? '')) updateOrder(o.id, { shipping: v || null })
                          setOrderEdits((prev) => {
                            const next = { ...prev }
                            if (next[o.id]) {
                              delete next[o.id].shipping
                              if (Object.keys(next[o.id]).length === 0) delete next[o.id]
                            }
                            return next
                          })
                        }}
                        placeholder="0"
                        disabled={savingOrderId === o.id}
                        className="w-14 h-5 rounded border border-brand-200 dark:border-gray-600 px-1 py-0 text-xs font-mono !bg-brand-50/50 dark:!bg-gray-600/80"
                      />
                    </label>
                    <label className="flex items-center gap-1 text-xs text-ink-muted">
                      Tax <input
                        type="text"
                        value={orderEdits[o.id]?.sales_tax ?? (o.sales_tax ?? '')}
                        onChange={(e) =>
                          setOrderEdits((prev) => ({ ...prev, [o.id]: { ...prev[o.id], sales_tax: e.target.value } }))
                        }
                        onBlur={(e) => {
                          const v = e.target.value.trim()
                          if (v !== (o.sales_tax ?? '')) updateOrder(o.id, { sales_tax: v || null })
                          setOrderEdits((prev) => {
                            const next = { ...prev }
                            if (next[o.id]) {
                              delete next[o.id].sales_tax
                              if (Object.keys(next[o.id]).length === 0) delete next[o.id]
                            }
                            return next
                          })
                        }}
                        placeholder="0"
                        disabled={savingOrderId === o.id}
                        className="w-14 h-5 rounded border border-brand-200 dark:border-gray-600 px-1 py-0 text-xs font-mono !bg-brand-50/50 dark:!bg-gray-600/80"
                      />
                    </label>
                  </div>
                  {o.items && o.items.length > 0 && (
                    <button
                      type="button"
                      title="Copy order"
                      onClick={(e) => copyOrder(o, e)}
                      disabled={copyingId === o.id}
                      className="p-1.5 rounded text-ink-muted hover:text-brand-600 hover:bg-brand-50 dark:hover:bg-gray-600 dark:hover:text-brand-400 transition disabled:opacity-50 self-start"
                      aria-label="Copy order"
                    >
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                      </svg>
                    </button>
                  )}
                </div>

                {/* Right: compact item list (Option 3) */}
                <div
                  className="flex-1 min-w-0 p-3 flex flex-col gap-0 overflow-x-auto"
                  style={{ minHeight: minRightHeight }}
                >
                  {itemCount === 0 ? (
                    <div className="text-sm text-ink-muted flex items-center gap-2">
                      No line items.
                      <Link
                        to={`/orders/${o.id}`}
                        className="text-brand-600 dark:text-brand-400 hover:underline"
                      >
                        Add items
                      </Link>
                    </div>
                  ) : (
                    <>
                      <div className="text-xs font-medium text-ink-muted mb-1 pb-1 border-b border-brand-200/80 dark:border-gray-600">
                        Line items — edit on{' '}
                        <Link to={`/orders/${o.id}`} className="text-brand-600 dark:text-brand-400 hover:underline">
                          order page
                        </Link>
                      </div>
                      <ul className="list-none p-0 m-0 space-y-0.5">
                        {(o.items ?? []).map((item: Item) => {
                          const qty = item.quantity ?? 1
                          const cost = item.price_paid != null && item.price_paid !== '' ? `$${parseFloat(item.price_paid).toFixed(2)}` : '—'
                          const payout = item.price_sold != null && item.price_sold !== '' ? `$${parseFloat(item.price_sold).toFixed(2)}` : '—'
                          const tracking = getTracking(item.id) || '—'
                          const status = STATUS_LABELS[item.status] ?? item.status
                          const desc = (item.description ?? '').trim() || '—'
                          return (
                            <li
                              key={item.id}
                              className="flex items-center gap-2 text-sm py-1 px-2 rounded hover:bg-brand-100/50 dark:hover:bg-gray-600/50"
                              style={{ minHeight: lineHeight }}
                            >
                              <span className="shrink-0 font-mono text-ink-muted w-6">{qty}×</span>
                              <span className="min-w-0 truncate flex-1 text-ink" title={desc}>
                                {desc}
                              </span>
                              <span className="shrink-0 text-ink-muted font-mono text-xs">{cost}</span>
                              <span className="shrink-0 text-ink-muted">|</span>
                              <span className="shrink-0 text-ink-muted font-mono text-xs">{payout}</span>
                              <span className="shrink-0 text-ink-muted">—</span>
                              <span className="shrink-0 font-mono text-xs text-ink-muted truncate max-w-[6rem]" title={tracking}>
                                {tracking}
                              </span>
                              <span className="shrink-0 text-ink-muted">—</span>
                              <span className="shrink-0 text-xs text-ink-muted">{status}</span>
                              <Link
                                to={`/orders/${o.id}`}
                                className="shrink-0 p-1 rounded text-ink-muted hover:text-brand-600 hover:bg-brand-50 dark:hover:bg-gray-600 dark:hover:text-brand-400 transition"
                                title="Edit order"
                                aria-label="Edit order"
                              >
                                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                                </svg>
                              </Link>
                            </li>
                          )
                        })}
                      </ul>
                    </>
                  )}
                </div>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}
