import { useEffect, useMemo, useState } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { api } from '../api/client'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { SearchableCombobox } from '../components/SearchableCombobox'
import type { Order, Item, ItemStatus, BuyingGroup, PaymentMethod, PaymentMethodNested, Store, StoreAccount, Shipment } from '../api/types'
import { getTrackingInfo } from '../utils/tracking'

const STATUS_OPTIONS: ItemStatus[] = [
  'purchased',
  'shipped',
  'submitted',
  'delivered',
  'scanned',
  'payment_requested',
  'payment_sent',
  'payment_received',
  'canceled',
  'needs_return',
  'return_sent',
  'return_received',
  'return_refunded',
]
/** Format purchase_date for datetime-local input (YYYY-MM-DDTHH:mm) */
function toDatetimeLocal(value: string | null): string {
  if (!value) return ''
  if (value.includes('T')) return value.slice(0, 16)
  return value + 'T00:00'
}

/** Current date and time in datetime-local format (YYYY-MM-DDTHH:mm) */
function nowDatetimeLocal(): string {
  const d = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function parseDecimal(s: string | null | undefined): number {
  if (s == null || String(s).trim() === '') return 0
  const n = parseFloat(String(s))
  return Number.isNaN(n) ? 0 : n
}

function orderTotals(items: { price_paid?: string | null; price_sold?: string | null; quantity?: number }[]) {
  let totalPaid = 0
  let totalSold = 0
  for (const item of items) {
    const qty = Math.max(0, item.quantity ?? 1)
    totalPaid += parseDecimal(item.price_paid) * qty
    totalSold += parseDecimal(item.price_sold) * qty
  }
  return { totalPaid, totalSold }
}

const STATUS_LABELS: Record<ItemStatus, string> = {
  purchased: 'Purchased',
  shipped: 'Shipped',
  submitted: 'Submitted',
  delivered: 'Delivered',
  scanned: 'Scanned',
  payment_requested: 'Payment requested',
  payment_sent: 'Payment sent',
  payment_received: 'Payment received',
  canceled: 'Canceled',
  needs_return: 'Needs return',
  return_sent: 'Return sent',
  return_received: 'Return received',
  return_refunded: 'Return refunded',
}

export default function OrderDetail() {
  const { orderId } = useParams()
  const navigate = useNavigate()
  const isNew = orderId === 'new'
  const [order, setOrder] = useState<Order | null>(null)
  const [groups, setGroups] = useState<BuyingGroup[]>([])
  const [paymentMethods, setPaymentMethods] = useState<PaymentMethod[]>([])
  const [stores, setStores] = useState<Store[]>([])
  const [accountsByStore, setAccountsByStore] = useState<Record<number, StoreAccount[]>>({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [splitModalItem, setSplitModalItem] = useState<Item | null>(null)
  const [confirmRemoveItem, setConfirmRemoveItem] = useState<number | null>(null)
  const [shipments, setShipments] = useState<Shipment[]>([])
  const [paymentRows, setPaymentRows] = useState<{ payment_method_id: number; amount: string }[]>([])

  const loadAccountsForStore = (storeId: number) =>
    api.get<StoreAccount[]>(`/stores/${storeId}/accounts`).then((list) => {
      setAccountsByStore((prev) => ({ ...prev, [storeId]: list }))
    })

  /** Flat list of payment methods (parents + sub-methods) for order form selection */
  const flatPaymentMethods = useMemo(
    () => paymentMethods.flatMap((pm) => [pm, ...(pm.sub_methods ?? [])]),
    [paymentMethods]
  )

  useEffect(() => {
    if (isNew) {
      Promise.all([
        api.get<BuyingGroup[]>('/buying-groups'),
        api.get<PaymentMethod[]>('/payment-methods'),
        api.get<Store[]>('/stores'),
      ])
        .then(([g, p, s]) => {
          setGroups(g)
          setPaymentMethods(p)
          setStores(s)
          if (s.length > 0) loadAccountsForStore(s[0].id)
        })
        .finally(() => setLoading(false))
      return
    }
    const id = Number(orderId)
    if (!id) return
    Promise.all([
      api.get<Order>(`/orders/${id}`),
      api.get<BuyingGroup[]>('/buying-groups'),
      api.get<PaymentMethod[]>('/payment-methods'),
      api.get<Store[]>('/stores'),
      api.get<Shipment[]>('/shipments'),
    ])
      .then(([o, g, p, s, shipmentsData]) => {
        setOrder(o)
        setGroups(g)
        setPaymentMethods(p)
        setStores(s)
        setShipments(shipmentsData)
        setPaymentRows(
          (o.order_payments ?? []).map((op) => ({
            payment_method_id: op.payment_method_id,
            amount: op.amount != null ? String(op.amount) : '',
          }))
        )
        if (o.store_id) loadAccountsForStore(o.store_id)
      })
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [orderId, isNew])

  const createOrder = async (payload: {
    store_id: number
    store_account_id?: number | null
    store_order_number?: string | null
    purchase_date?: string | null
    notes?: string
    buying_group_id?: number | null
    payment_methods: { payment_method_id: number; amount?: string }[]
    items: { price_paid?: string; price_sold?: string; status?: ItemStatus; quantity?: number; description?: string }[]
  }) => {
    setSaving(true)
    try {
      const created = await api.post<Order>('/orders', payload)
      navigate(`/orders/${created.id}`, { replace: true })
    } catch (e) {
      console.error(e)
    } finally {
      setSaving(false)
    }
  }

  const updateOrderNotes = async (notes: string) => {
    if (!order || isNew) return
    setSaving(true)
    try {
      const updated = await api.patch<Order>(`/orders/${order.id}`, { notes })
      setOrder(updated)
    } catch (e) {
      console.error(e)
    } finally {
      setSaving(false)
    }
  }

  const updateOrderStoreOrderNumber = async (storeOrderNumber: string) => {
    if (!order || isNew) return
    setSaving(true)
    try {
      const updated = await api.patch<Order>(`/orders/${order.id}`, {
        store_order_number: storeOrderNumber || null,
      })
      setOrder(updated)
    } catch (e) {
      console.error(e)
    } finally {
      setSaving(false)
    }
  }

  const updateOrderPurchaseDate = async (purchase_date: string | null) => {
    if (!order || isNew) return
    setSaving(true)
    try {
      const updated = await api.patch<Order>(`/orders/${order.id}`, { purchase_date })
      setOrder(updated)
    } catch (e) {
      console.error(e)
    } finally {
      setSaving(false)
    }
  }

  const updateOrderStore = async (storeId: number, storeAccountId: number | null) => {
    if (!order || isNew) return
    setSaving(true)
    try {
      const updated = await api.patch<Order>(`/orders/${order.id}`, {
        store_id: storeId,
        store_account_id: storeAccountId,
      })
      setOrder(updated)
      loadAccountsForStore(storeId)
    } catch (e) {
      console.error(e)
    } finally {
      setSaving(false)
    }
  }

  const updateOrderBuyingGroup = async (buying_group_id: number | null) => {
    if (!order || isNew) return
    setSaving(true)
    try {
      const updated = await api.patch<Order>(`/orders/${order.id}`, { buying_group_id })
      setOrder(updated)
    } catch (e) {
      console.error(e)
    } finally {
      setSaving(false)
    }
  }

  const updateOrderPayments = async (rows: { payment_method_id: number; amount: string }[]) => {
    if (!order || isNew) return
    setSaving(true)
    try {
      const payment_methods = rows.map(({ payment_method_id, amount }) => ({
        payment_method_id,
        amount: amount.trim() ? amount.trim() : null,
      }))
      const updated = await api.patch<Order>(`/orders/${order.id}`, { payment_methods })
      setOrder(updated)
      setPaymentRows(
        (updated.order_payments ?? []).map((op) => ({
          payment_method_id: op.payment_method_id,
          amount: op.amount != null ? String(op.amount) : '',
        }))
      )
    } catch (e) {
      console.error(e)
    } finally {
      setSaving(false)
    }
  }

  // Autosave payments when valid and different from server (debounced)
  useEffect(() => {
    if (!order || isNew || saving) return
    const { totalPaid: orderTotalPaid } = orderTotals(order.items)
    const paymentSum = paymentRows.reduce(
      (sum, r) => sum + (parseDecimal(r.amount) || 0),
      0
    )
    const usedMethodIds = new Set(paymentRows.map((r) => r.payment_method_id))
    const hasDuplicateMethod =
      usedMethodIds.size > 0 && usedMethodIds.size < paymentRows.length
    const paymentMatchesTotal =
      paymentRows.length > 0 &&
      Math.abs(paymentSum - orderTotalPaid) < 0.01
    const canSave =
      paymentRows.length > 0 && !hasDuplicateMethod && paymentMatchesTotal
    if (!canSave) return

    const serverPayments = (order.order_payments ?? []).map((op) => ({
      payment_method_id: op.payment_method_id,
      amount: op.amount != null ? String(op.amount) : '',
    }))
    const sameAsServer =
      paymentRows.length === serverPayments.length &&
      paymentRows.every(
        (r, i) =>
          r.payment_method_id === serverPayments[i].payment_method_id &&
          (r.amount.trim() || '') === (serverPayments[i].amount || '')
      )
    if (sameAsServer) return

    const t = setTimeout(() => {
      updateOrderPayments(paymentRows)
    }, 600)
    return () => clearTimeout(t)
  }, [order, paymentRows, saving])

  const updateItem = async (itemId: number, data: Partial<Item>) => {
    if (!order) return
    try {
      const updated = await api.patch<Item>(`/items/${itemId}`, data)
      setOrder((o) => (o ? { ...o, items: o.items.map((i) => (i.id === itemId ? updated : i)) } : o))
    } catch (e) {
      console.error(e)
    }
  }

  const addItem = async () => {
    if (!order || isNew) return
    try {
      const newItem = await api.post<Item>('/items', {
        order_id: order.id,
        status: 'purchased',
      })
      setOrder((o) => (o ? { ...o, items: [...o.items, newItem] } : o))
    } catch (e) {
      console.error(e)
    }
  }

  const deleteItem = async (itemId: number) => {
    if (!order) return
    try {
      await api.delete(`/items/${itemId}`)
      setOrder((o) => (o ? { ...o, items: o.items.filter((i) => i.id !== itemId) } : o))
    } catch (e) {
      console.error(e)
    }
  }

  const splitItem = async (itemId: number, keepQuantity: number, currentQuantity: number) => {
    if (!order) return
    try {
      // Ensure backend has latest quantity (user may have edited without blur)
      await api.patch<Item>(`/items/${itemId}`, { quantity: currentQuantity })
      const { kept, split_off } = await api.post<{ kept: Item; split_off: Item }>(
        `/items/${itemId}/split`,
        { keep_quantity: keepQuantity }
      )
      setOrder((o) =>
        o
          ? {
              ...o,
              items: o.items
                .map((i) => (i.id === itemId ? kept : i))
                .concat(split_off)
                .sort((a, b) => a.id - b.id),
            }
          : o
      )
      setSplitModalItem(null)
    } catch (e) {
      console.error(e)
    }
  }

  if (loading) return <div className="text-ink-muted">Loading…</div>

  if (isNew) {
    return (
      <NewOrderForm
        stores={stores}
        accountsByStore={accountsByStore}
        loadAccountsForStore={loadAccountsForStore}
        groups={groups}
        paymentMethods={flatPaymentMethods}
        onSubmit={createOrder}
        saving={saving}
        onCancel={() => navigate('/')}
        onStoreCreated={(s) => setStores((prev) => [...prev, s].sort((a, b) => a.name.localeCompare(b.name)))}
        onAccountCreated={(a) => loadAccountsForStore(a.store_id)}
        onGroupCreated={(g) => setGroups((prev) => [...prev, g])}
        onPaymentMethodCreated={(pm) => setPaymentMethods((prev) => [...prev, pm])}
      />
    )
  }

  if (!order) return <div className="text-ink-muted">Order not found.</div>

  return (
    <div className="space-y-8">
      <div className="flex items-center gap-4 flex-wrap">
        <Link to="/" className="text-ink-muted hover:text-ink">
          ← Orders
        </Link>
        <h1 className="text-2xl font-semibold text-ink">Order</h1>
      </div>

      <section className="bg-white dark:bg-gray-800 rounded-xl border border-brand-200/80 dark:border-gray-700 shadow-sm p-6">
        <h2 className="text-sm font-medium text-ink-muted mb-2">Order number (from store)</h2>
        <input
          type="text"
          className="rounded-lg border border-brand-200 px-3 py-2 text-ink w-full max-w-md mb-4"
          placeholder="e.g. 112-3456789-1234567"
          value={order.store_order_number ?? ''}
          onChange={(e) => setOrder((o) => (o ? { ...o, store_order_number: e.target.value } : o))}
          onBlur={(e) => updateOrderStoreOrderNumber(e.target.value)}
        />
        <h2 className="text-sm font-medium text-ink-muted mb-2">Purchase date &amp; time</h2>
        <input
          type="datetime-local"
          className="rounded-lg border border-brand-200 px-3 py-2 text-ink mb-4"
          value={toDatetimeLocal(order.purchase_date ?? null)}
          onChange={(e) => {
            const v = e.target.value || null
            setOrder((o) => (o ? { ...o, purchase_date: v } : o))
          }}
          onBlur={(e) => updateOrderPurchaseDate(e.target.value || null)}
        />
        <h2 className="text-sm font-medium text-ink-muted mb-2">Store</h2>
        <div className="flex gap-4 mb-4 flex-wrap">
          <div className="min-w-[200px]">
            <SearchableCombobox<Store>
              label="Store"
              options={stores}
              value={stores.find((s) => s.id === order.store_id) ?? null}
              onChange={(s) => {
                const storeId = s?.id ?? 0
                const accounts = accountsByStore[storeId]
                const firstAccountId = accounts?.[0]?.id ?? null
                updateOrderStore(storeId, firstAccountId)
                setOrder((o) =>
                  o ? { ...o, store_id: storeId, store_account_id: firstAccountId } : o
                )
                if (storeId) loadAccountsForStore(storeId)
              }}
              onCreate={async (name) => {
                const s = await api.post<Store>('/stores', { name })
                setStores((prev) => [...prev, s])
                updateOrderStore(s.id, null)
                setOrder((o) => (o ? { ...o, store_id: s.id, store_account_id: null } : o))
                loadAccountsForStore(s.id)
                return s
              }}
              placeholder="Type to search stores…"
            />
          </div>
          <div className="min-w-[200px]">
            <SearchableCombobox<StoreAccount>
              label="Account (optional)"
              options={accountsByStore[order.store_id] ?? []}
              value={
                (accountsByStore[order.store_id] ?? []).find(
                  (a) => a.id === order.store_account_id
                ) ?? null
              }
              onChange={(a) => {
                const storeAccountId = a?.id ?? null
                updateOrderStore(order.store_id, storeAccountId)
                setOrder((o) => (o ? { ...o, store_account_id: storeAccountId } : o))
              }}
              onCreate={
                order.store_id
                  ? async (name) => {
                      const a = await api.post<StoreAccount>(
                        `/stores/${order.store_id}/accounts`,
                        { name }
                      )
                      setAccountsByStore((prev) => ({
                        ...prev,
                        [order.store_id]: [...(prev[order.store_id] ?? []), a],
                      }))
                      updateOrderStore(order.store_id, a.id)
                      setOrder((o) => (o ? { ...o, store_account_id: a.id } : o))
                      return a
                    }
                  : undefined
              }
              placeholder="Type to search accounts…"
              allowEmpty
              disabled={!order.store_id}
            />
          </div>
          <div className="min-w-[200px]">
            <SearchableCombobox<BuyingGroup>
              label="Buying group"
              options={groups}
              value={groups.find((g) => g.id === order.buying_group_id) ?? null}
              onChange={(g) => {
                const buying_group_id = g?.id ?? null
                updateOrderBuyingGroup(buying_group_id)
                setOrder((o) => (o ? { ...o, buying_group_id } : o))
              }}
              onCreate={async (name) => {
                const g = await api.post<BuyingGroup>('/buying-groups', { name })
                setGroups((prev) => [...prev, g])
                updateOrderBuyingGroup(g.id)
                setOrder((o) => (o ? { ...o, buying_group_id: g.id } : o))
                return g
              }}
              placeholder="Type to search buying groups…"
              allowEmpty
            />
          </div>
        </div>
        <div className="mt-4 pt-4 border-t border-brand-100">
          <h2 className="text-sm font-medium text-ink-muted mb-2">Payment</h2>
          {(() => {
            const { totalPaid: orderTotalPaid } = orderTotals(order.items)
            const paymentSum = paymentRows.reduce(
              (sum, r) => sum + (parseDecimal(r.amount) || 0),
              0
            )
            const usedMethodIds = new Set(paymentRows.map((r) => r.payment_method_id))
            const paymentMatchesTotal =
              paymentRows.length > 0 &&
              Math.round(paymentSum * 100) === Math.round(orderTotalPaid * 100)
            const availableForRow = (idx: number) =>
              flatPaymentMethods.filter(
                (pm) =>
                  pm.id === paymentRows[idx]?.payment_method_id ||
                  !paymentRows.some((r, i) => i !== idx && r.payment_method_id === pm.id)
              )
            const firstUnusedMethodId =
              flatPaymentMethods.find((pm) => !usedMethodIds.has(pm.id))?.id ??
              flatPaymentMethods[0]?.id
            const amountRemaining = Math.max(0, orderTotalPaid - paymentSum)
            return (
              <div className="space-y-3">
                <p className="text-sm text-ink-muted">
                  Order total (price paid):{' '}
                  <span className="font-mono text-ink">${orderTotalPaid.toFixed(2)}</span>
                  {' — '}
                  Payment total:{' '}
                  <span className="font-mono text-ink">${paymentSum.toFixed(2)}</span>
                  {paymentRows.length > 0 && !paymentMatchesTotal && (
                    <span className="text-amber-600 ml-1">
                      (should equal order total)
                    </span>
                  )}
                </p>
                {paymentRows.map((row, idx) => (
                  <div key={idx} className="flex items-center gap-3 flex-wrap">
                    <select
                      className="rounded-lg border border-brand-200 px-3 py-2 text-ink min-w-[140px]"
                      value={row.payment_method_id}
                      onChange={(e) =>
                        setPaymentRows((prev) =>
                          prev.map((r, i) =>
                            i === idx
                              ? { ...r, payment_method_id: Number(e.target.value) }
                              : r
                          )
                        )
                      }
                    >
                      {availableForRow(idx).map((pm) => (
                        <option key={pm.id} value={pm.id}>
                          {pm.label}
                        </option>
                      ))}
                    </select>
                    <input
                      type="text"
                      className="rounded-lg border border-brand-200 px-3 py-2 text-ink w-24 font-mono"
                      placeholder="Amount"
                      value={row.amount}
                      onChange={(e) => {
                        const raw = e.target.value
                        setPaymentRows((prev) => {
                          const otherSum = prev.reduce(
                            (s, r, i) => (i === idx ? s : s + (parseDecimal(r.amount) || 0)),
                            0
                          )
                          const maxAmount = Math.max(0, orderTotalPaid - otherSum)
                          const num = parseDecimal(raw)
                          const amount =
                            raw.trim() === '' || String(raw) === '.' || Number.isNaN(parseFloat(raw))
                              ? raw
                              : num > maxAmount
                                ? maxAmount.toFixed(2)
                                : raw
                          return prev.map((r, i) => (i === idx ? { ...r, amount } : r))
                        })
                      }}
                    />
                    <button
                      type="button"
                      onClick={() =>
                        setPaymentRows((prev) => prev.filter((_, i) => i !== idx))
                      }
                      className="p-1 rounded text-ink-muted hover:text-ink hover:bg-brand-100 dark:hover:bg-gray-600 transition"
                      title="Remove payment method"
                      aria-label="Remove payment method"
                    >
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                      </svg>
                    </button>
                    {idx === paymentRows.length - 1 && (() => {
                      const addPaymentDisabled =
                        saving ||
                        flatPaymentMethods.length === 0 ||
                        paymentRows.length >= flatPaymentMethods.length ||
                        paymentMatchesTotal
                      const addPaymentReason = addPaymentDisabled
                        ? saving
                          ? 'Saving…'
                          : flatPaymentMethods.length === 0
                            ? 'No payment methods configured'
                            : paymentRows.length >= flatPaymentMethods.length
                              ? 'All payment methods already added'
                              : 'Payment total already matches order total'
                        : undefined
                      return (
                        <button
                          type="button"
                          onClick={() =>
                            setPaymentRows((prev) => [
                              ...prev,
                              {
                                payment_method_id: firstUnusedMethodId ?? 0,
                                amount:
                                  amountRemaining > 0 ? amountRemaining.toFixed(2) : '',
                              },
                            ])
                          }
                          className="p-1 rounded text-ink-muted hover:text-brand-600 hover:bg-brand-100 dark:hover:bg-gray-600 transition disabled:opacity-50 disabled:cursor-not-allowed"
                          disabled={addPaymentDisabled}
                          title={addPaymentReason ?? 'Add payment method'}
                          aria-label="Add payment method"
                        >
                          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                          </svg>
                        </button>
                      )
                    })()}
                  </div>
                ))}
                {paymentRows.length === 0 && (() => {
                  const addPaymentDisabled =
                    saving || flatPaymentMethods.length === 0 || paymentMatchesTotal
                  const addPaymentReason = addPaymentDisabled
                    ? saving
                      ? 'Saving…'
                      : flatPaymentMethods.length === 0
                        ? 'No payment methods configured'
                        : 'Payment total already matches order total'
                    : undefined
                  return (
                    <div className="flex items-center gap-3 flex-wrap">
                      <button
                        type="button"
                        onClick={() =>
                          setPaymentRows((prev) => [
                            ...prev,
                            {
                              payment_method_id: firstUnusedMethodId ?? 0,
                              amount: amountRemaining > 0 ? amountRemaining.toFixed(2) : '',
                            },
                          ])
                        }
                        className="p-1 rounded text-ink-muted hover:text-brand-600 hover:bg-brand-100 dark:hover:bg-gray-600 transition disabled:opacity-50 disabled:cursor-not-allowed"
                        disabled={addPaymentDisabled}
                        title={addPaymentReason ?? 'Add payment method'}
                        aria-label="Add payment method"
                      >
                        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                        </svg>
                      </button>
                    </div>
                  )
                })()}
              </div>
            )
          })()}
        </div>
      </section>

      <section className="bg-white dark:bg-gray-800 rounded-xl border border-brand-200/80 dark:border-gray-700 shadow-sm overflow-hidden">
        <div className="p-4 border-b border-brand-200/80 flex justify-between items-center">
          <h2 className="font-medium text-ink">Items</h2>
          <button
            type="button"
            onClick={addItem}
            className="text-sm px-3 py-1.5 bg-brand-100 text-brand-700 rounded-lg hover:bg-brand-200 transition"
          >
            Add item
          </button>
        </div>
        {(() => {
          const itemIdToTracking = shipments.reduce<Record<number, string>>((acc, s) => {
            const tn = s.tracking_number?.trim()
            if (tn) for (const si of s.shipment_items || []) acc[si.item_id] = tn
            return acc
          }, {})
          const getTracking = (itemId: number) => itemIdToTracking[itemId] ?? '—'
          return (
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-brand-50 dark:bg-gray-700/50 border-b border-brand-200/80 dark:border-gray-700">
              <tr>
                <th className="text-left py-2 px-4 text-xs font-medium text-ink-muted">Qty</th>
                <th className="text-left py-2 px-4 text-xs font-medium text-ink-muted">Description</th>
                <th className="text-left py-2 pl-4 pr-1 text-xs font-medium text-ink-muted w-32">Cost</th>
                <th className="text-left py-2 pl-1 pr-4 text-xs font-medium text-ink-muted w-32">Payout</th>
                <th className="text-left py-2 px-4 text-xs font-medium text-ink-muted">Tracking</th>
                <th className="text-left py-2 px-4 text-xs font-medium text-ink-muted">Status</th>
                <th className="w-24 text-right pr-2">
                  <button
                    type="button"
                    onClick={addItem}
                    className="p-1.5 rounded text-ink-muted hover:text-brand-600 hover:bg-brand-100 dark:hover:bg-gray-600 transition"
                    title="Add line item"
                    aria-label="Add line item"
                  >
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                    </svg>
                  </button>
                </th>
              </tr>
            </thead>
            <tbody>
              {order.items.length === 0 ? (
                <tr>
                  <td colSpan={7} className="py-8 text-center text-ink-muted text-sm">
                    No items. Add one above.
                  </td>
                </tr>
              ) : (
                order.items.map((item) => (
                  <tr key={item.id} className="border-b border-brand-100 last:border-0">
                    <td className="py-2 px-4">
                      <input
                        type="number"
                        min={1}
                        className="w-14 rounded border border-brand-200 px-2 py-1 text-sm"
                        value={item.quantity ?? 1}
                        onChange={(e) =>
                          setOrder((o) => {
                            const v = Math.max(1, parseInt(e.target.value, 10) || 1)
                            return o ? { ...o, items: o.items.map((i) => (i.id === item.id ? { ...i, quantity: v } : i)) } : o
                          })
                        }
                        onBlur={(e) => {
                          const v = Math.max(1, parseInt(e.target.value, 10) || 1)
                          updateItem(item.id, { quantity: v })
                        }}
                      />
                    </td>
                    <td className="py-2 px-4">
                      <input
                        type="text"
                        className="w-full rounded border border-brand-200 px-2 py-1 text-sm"
                        value={item.description ?? ''}
                        onChange={(e) =>
                          setOrder((o) =>
                            o ? { ...o, items: o.items.map((i) => (i.id === item.id ? { ...i, description: e.target.value } : i)) } : o
                          )
                        }
                        onBlur={(e) => updateItem(item.id, { description: e.target.value })}
                        placeholder="Description"
                      />
                    </td>
                    <td className="py-2 pl-4 pr-1">
                      <input
                        type="number"
                        step="0.01"
                        className="w-28 min-w-[7rem] rounded border border-brand-200 px-2 py-1 text-sm"
                        value={item.price_paid ?? ''}
                        onChange={(e) =>
                          setOrder((o) =>
                            o ? { ...o, items: o.items.map((i) => (i.id === item.id ? { ...i, price_paid: e.target.value } : i)) } : o
                          )
                        }
                        onBlur={(e) => updateItem(item.id, { price_paid: e.target.value || null })}
                        placeholder="0"
                      />
                    </td>
                    <td className="py-2 pl-1 pr-4">
                      <input
                        type="number"
                        step="0.01"
                        className="w-28 min-w-[7rem] rounded border border-brand-200 px-2 py-1 text-sm"
                        value={item.price_sold ?? ''}
                        onChange={(e) =>
                          setOrder((o) =>
                            o ? { ...o, items: o.items.map((i) => (i.id === item.id ? { ...i, price_sold: e.target.value } : i)) } : o
                          )
                        }
                        onBlur={(e) => updateItem(item.id, { price_sold: e.target.value || null })}
                        placeholder="0"
                      />
                    </td>
                    <td className="py-2 px-4 text-sm text-ink-muted max-w-[12rem]">
                      {(() => {
                        const tn = getTracking(item.id)
                        if (tn === '—') return '—'
                        const info = getTrackingInfo(tn)
                        if (info) {
                          return (
                            <a
                              href={info.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-brand-600 dark:text-brand-400 hover:underline truncate block"
                              title={`Track via ${info.carrier}`}
                            >
                              {tn}
                            </a>
                          )
                        }
                        return <span className="truncate block" title={tn}>{tn}</span>
                      })()}
                    </td>
                    <td className="py-2 px-4">
                      <select
                        className="rounded border border-brand-200 px-2 py-1 text-sm"
                        value={item.status}
                        onChange={(e) => {
                          const v = e.target.value as ItemStatus
                          updateItem(item.id, { status: v })
                          setOrder((o) =>
                            o ? { ...o, items: o.items.map((i) => (i.id === item.id ? { ...i, status: v } : i)) } : o
                          )
                        }}
                      >
                        {STATUS_OPTIONS.map((s) => (
                          <option key={s} value={s}>
                            {STATUS_LABELS[s]}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="py-2 px-2">
                      <div className="flex gap-1 items-center">
                        {(item.quantity ?? 1) > 1 && (
                          <button
                            type="button"
                            onClick={() => setSplitModalItem(item)}
                            className="p-1 rounded text-ink-muted hover:text-red-600 hover:bg-brand-100 dark:hover:bg-gray-600 transition"
                            title="Split for separate shipping"
                            aria-label="Split for separate shipping"
                          >
                            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}>
                              <circle cx="6" cy="7" r="3" />
                              <circle cx="6" cy="17" r="3" />
                              <path d="M8.6 8.6l10.4 10.4M8.6 15.4l10.4 -10.4" />
                            </svg>
                          </button>
                        )}
                        <button
                          type="button"
                          onClick={() => setConfirmRemoveItem(item.id)}
                          className="p-1 rounded text-ink-muted hover:text-red-600 hover:bg-brand-100 dark:hover:bg-gray-600 transition"
                          title="Remove item"
                          aria-label="Remove item"
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
          )
        })()}
        {order.items.length > 0 && (() => {
          const { totalPaid, totalSold } = orderTotals(order.items)
          return (
            <div className="border-t border-brand-200/80 dark:border-gray-700 p-4 bg-brand-50/50 dark:bg-gray-700/30">
              <h3 className="text-sm font-medium text-ink-muted mb-2">Order summary</h3>
              <dl className="flex gap-6 flex-wrap text-sm">
                <div>
                  <dt className="text-ink-muted">Total price paid</dt>
                  <dd className="font-mono font-medium text-ink">${totalPaid.toFixed(2)}</dd>
                </div>
                <div>
                  <dt className="text-ink-muted">Total price sold</dt>
                  <dd className="font-mono font-medium text-ink">${totalSold.toFixed(2)}</dd>
                </div>
              </dl>
            </div>
          )
        })()}
      </section>

      <section className="bg-white dark:bg-gray-800 rounded-xl border border-brand-200/80 dark:border-gray-700 shadow-sm p-6">
        <h2 className="text-sm font-medium text-ink-muted mb-2">Notes</h2>
        <textarea
          className="w-full rounded-lg border border-brand-200 px-3 py-2 text-ink min-h-[80px]"
          value={order.notes ?? ''}
          onChange={(e) => setOrder((o) => (o ? { ...o, notes: e.target.value } : o))}
          onBlur={(e) => updateOrderNotes(e.target.value)}
        />
      </section>

      <ConfirmDialog
        open={confirmRemoveItem !== null}
        message="Remove this item from the order?"
        confirmLabel="Remove"
        danger
        onConfirm={() => {
          if (confirmRemoveItem !== null) {
            deleteItem(confirmRemoveItem)
            setConfirmRemoveItem(null)
          }
        }}
        onCancel={() => setConfirmRemoveItem(null)}
      />
      {splitModalItem && (
        <SplitItemModal
          item={splitModalItem}
          onConfirm={(keepQuantity) =>
            splitItem(splitModalItem.id, keepQuantity, splitModalItem.quantity ?? 1)
          }
          onClose={() => setSplitModalItem(null)}
        />
      )}
    </div>
  )
}

function SplitItemModal({
  item,
  onConfirm,
  onClose,
}: {
  item: Item
  onConfirm: (keepQuantity: number) => void
  onClose: () => void
}) {
  const qty = item.quantity ?? 1
  const [keepQuantity, setKeepQuantity] = useState(1)
  const splitOff = Math.max(0, qty - keepQuantity)
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-white dark:bg-gray-800 rounded-xl shadow-xl p-6 max-w-md w-full mx-4 border border-brand-200/80 dark:border-gray-700"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-lg font-medium text-ink mb-2">Split for separate shipping</h3>
        <p className="text-sm text-ink-muted mb-4">
          Split {qty}× {item.description || 'Item'} into two lines so they can ship with different tracking numbers.
        </p>
        <div className="flex gap-4 items-center mb-6">
          <label className="flex items-center gap-2">
            <span className="text-sm text-ink-muted">Keep</span>
            <input
              type="number"
              min={1}
              max={Math.max(1, qty - 1)}
              value={keepQuantity}
              onChange={(e) => setKeepQuantity(Math.max(1, Math.min(qty - 1, parseInt(e.target.value, 10) || 1)))}
              className="w-16 rounded border border-brand-200 px-2 py-1.5 text-sm"
            />
            <span className="text-sm text-ink-muted">here</span>
          </label>
          <span className="text-ink-muted">→</span>
          <span className="text-sm text-ink-muted">Split off {splitOff} as new line</span>
        </div>
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-1.5 border border-brand-300 rounded-lg text-sm"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => onConfirm(keepQuantity)}
            disabled={splitOff < 1}
            className="px-3 py-1.5 bg-brand-600 text-white rounded-lg text-sm hover:bg-brand-700 disabled:opacity-50"
          >
            Split
          </button>
        </div>
      </div>
    </div>
  )
}

function NewOrderForm({
  stores,
  accountsByStore,
  loadAccountsForStore,
  groups,
  paymentMethods,
  onSubmit,
  saving,
  onCancel,
  onStoreCreated,
  onAccountCreated,
  onGroupCreated,
  onPaymentMethodCreated,
}: {
  stores: Store[]
  accountsByStore: Record<number, StoreAccount[]>
  loadAccountsForStore: (storeId: number) => Promise<void>
  groups: BuyingGroup[]
  paymentMethods: (PaymentMethod | PaymentMethodNested)[]
  onSubmit: (payload: {
    store_id: number
    store_account_id?: number | null
    store_order_number?: string | null
    purchase_date?: string | null
    notes?: string
    buying_group_id?: number | null
    payment_methods: { payment_method_id: number; amount?: string }[]
    items: { price_paid?: string; price_sold?: string; status?: ItemStatus; quantity?: number; description?: string }[]
  }) => void
  saving: boolean
  onCancel: () => void
  onStoreCreated: (store: Store) => void
  onAccountCreated: (account: StoreAccount) => void
  onGroupCreated: (group: BuyingGroup) => void
  onPaymentMethodCreated: (pm: PaymentMethod | PaymentMethodNested) => void
}) {
  const [storeId, setStoreId] = useState(0)
  const [storeAccountId, setStoreAccountId] = useState<number | null>(null)
  const [storeOrderNumber, setStoreOrderNumber] = useState('')
  const [purchaseDate, setPurchaseDate] = useState(nowDatetimeLocal())
  const [notes, setNotes] = useState('')
  const [buyingGroupId, setBuyingGroupId] = useState<number | null>(null)
  const [selectedPayments, setSelectedPayments] = useState<{ payment_method_id: number; amount: string }[]>([
    { payment_method_id: 0, amount: '' },
  ])
  const [items, setItems] = useState<{ price_paid?: string; price_sold?: string; status?: ItemStatus; quantity?: number; description?: string }[]>(() => [{}])

  const handleStoreChange = (id: number) => {
    setStoreId(id)
    setStoreAccountId(null)
    loadAccountsForStore(id)
  }

  const addPayment = () => {
    setSelectedPayments((p) => [...p, { payment_method_id: 0, amount: '' }])
  }
  const removePayment = (idx: number) => {
    if (selectedPayments.length <= 1) return
    setSelectedPayments((p) => p.filter((_, i) => i !== idx))
  }
  const addItem = () => setItems((i) => [...i, {}])
  const removeItem = (idx: number) => setItems((i) => i.filter((_, j) => j !== idx))

  const validPayments = selectedPayments.filter((p) => p.payment_method_id > 0)

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!storeId || !buyingGroupId || validPayments.length === 0) return
    onSubmit({
      store_id: storeId,
      store_account_id: storeAccountId,
      store_order_number: storeOrderNumber.trim() || null,
      purchase_date: purchaseDate || null,
      notes: notes || undefined,
      buying_group_id: buyingGroupId ?? null,
      payment_methods: validPayments.map(({ payment_method_id, amount }) => ({
        payment_method_id,
        amount: amount || undefined,
      })),
      items: items.map((it) => ({ ...it, status: it.status ?? 'purchased' })),
    })
  }

  return (
    <div className="space-y-8">
      <div className="flex items-center gap-4">
        <Link to="/" className="text-ink-muted hover:text-ink">
          ← Orders
        </Link>
        <h1 className="text-2xl font-semibold text-ink">New order</h1>
      </div>
      <form onSubmit={handleSubmit} className="space-y-6 max-w-2xl xl:max-w-4xl">
        <div>
          <label className="block text-sm font-medium text-ink mb-1">Order number (from store)</label>
          <input
            type="text"
            className="rounded-lg border border-brand-200 px-3 py-2 w-full max-w-md"
            placeholder="e.g. 112-3456789-1234567"
            value={storeOrderNumber}
            onChange={(e) => setStoreOrderNumber(e.target.value)}
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-ink mb-1">Purchase date &amp; time</label>
          <input
            type="datetime-local"
            className="rounded-lg border border-brand-200 px-3 py-2"
            value={purchaseDate}
            onChange={(e) => setPurchaseDate(e.target.value)}
          />
        </div>
        <div className="flex gap-4 flex-wrap">
          <div className="min-w-[200px]">
            <SearchableCombobox<Store>
              label="Store (required)"
              options={stores}
              value={stores.find((s) => s.id === storeId) ?? null}
              onChange={(s) => handleStoreChange(s?.id ?? 0)}
              onCreate={async (name) => {
                const s = await api.post<Store>('/stores', { name })
                onStoreCreated(s)
                return s
              }}
              placeholder="Type to search stores…"
              required
            />
          </div>
          <div className="min-w-[200px]">
            <SearchableCombobox<StoreAccount>
              label="Account (optional)"
              options={accountsByStore[storeId] ?? []}
              value={
                (accountsByStore[storeId] ?? []).find((a) => a.id === storeAccountId) ?? null
              }
              onChange={(a) => setStoreAccountId(a?.id ?? null)}
              onCreate={
                storeId
                  ? async (name) => {
                      const a = await api.post<StoreAccount>(`/stores/${storeId}/accounts`, {
                        name,
                      })
                      onAccountCreated(a)
                      return a
                    }
                  : undefined
              }
              placeholder="Type to search accounts…"
              allowEmpty
              disabled={!storeId}
            />
          </div>
          <div className="min-w-[200px]">
            <SearchableCombobox<BuyingGroup>
              label="Buying group (required)"
              options={groups}
              value={groups.find((g) => g.id === buyingGroupId) ?? null}
              onChange={(g) => setBuyingGroupId(g?.id ?? null)}
              onCreate={async (name) => {
                const g = await api.post<BuyingGroup>('/buying-groups', { name })
                onGroupCreated(g)
                return g
              }}
              placeholder="Type to search buying groups…"
              required
            />
          </div>
        </div>
        <div>
          <div className="flex justify-between items-center mb-2">
            <label className="text-sm font-medium text-ink">Payment methods (at least one required)</label>
            <button type="button" onClick={addPayment} className="text-sm text-brand-600 hover:underline">
              Add payment method
            </button>
          </div>
          {selectedPayments.map((p, idx) => {
            const pmOptions = paymentMethods
              .filter(
                (pm) =>
                  pm.id === p.payment_method_id ||
                  !selectedPayments.some((x, i) => i !== idx && x.payment_method_id === pm.id)
              )
              .map((pm) => ({ id: pm.id, name: pm.label }))
            return (
            <div key={idx} className="flex gap-2 items-center mb-2">
              <div className="flex-1 min-w-0">
                <SearchableCombobox<{ id: number; name: string }>
                  options={pmOptions}
                  value={p.payment_method_id ? pmOptions.find((o) => o.id === p.payment_method_id) ?? null : null}
                  onChange={(opt) =>
                    setSelectedPayments((prev) =>
                      prev.map((x, i) => (i === idx ? { ...x, payment_method_id: opt?.id ?? 0 } : x))
                    )
                  }
                  onCreate={async (label) => {
                    const pm = await api.post<PaymentMethod>('/payment-methods', { label })
                    onPaymentMethodCreated(pm)
                    return { id: pm.id, name: pm.label }
                  }}
                  placeholder="Type to search payment methods…"
                />
              </div>
              <input
                type="text"
                placeholder="Amount"
                className="w-24 rounded border border-brand-200 px-2 py-1.5"
                value={p.amount}
                onChange={(e) =>
                  setSelectedPayments((prev) => prev.map((x, i) => (i === idx ? { ...x, amount: e.target.value } : x)))
                }
              />
              <button
                type="button"
                onClick={() => removePayment(idx)}
                disabled={selectedPayments.length <= 1}
                className="text-red-600 text-sm disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Remove
              </button>
            </div>
            )
          })}
        </div>
        <div>
          <div className="flex justify-between items-center mb-2">
            <label className="text-sm font-medium text-ink">Items</label>
            <button type="button" onClick={addItem} className="text-sm text-brand-600 hover:underline">
              Add item
            </button>
          </div>
          {items.map((it, idx) => (
            <div key={idx} className="flex flex-wrap gap-2 items-center mb-0.5 p-1.5 bg-brand-50/50 dark:bg-gray-700/30 rounded-lg">
              <input
                type="number"
                min={1}
                placeholder="Qty"
                className="w-14 rounded border border-brand-200 px-2 py-1.5"
                value={it.quantity ?? 1}
                onChange={(e) =>
                  setItems((prev) =>
                    prev.map((x, i) => (i === idx ? { ...x, quantity: Math.max(1, parseInt(e.target.value, 10) || 1) } : x))
                  )
                }
              />
              <input
                type="text"
                placeholder="Description"
                className="rounded border border-brand-200 px-2 py-1.5 flex-1 min-w-[120px]"
                value={it.description ?? ''}
                onChange={(e) =>
                  setItems((prev) => prev.map((x, i) => (i === idx ? { ...x, description: e.target.value } : x)))
                }
              />
              <input
                type="number"
                step="0.01"
                placeholder="Paid"
                className="w-20 rounded border border-brand-200 px-2 py-1.5"
                value={it.price_paid ?? ''}
                onChange={(e) =>
                  setItems((prev) => prev.map((x, i) => (i === idx ? { ...x, price_paid: e.target.value } : x)))
                }
              />
              <input
                type="number"
                step="0.01"
                placeholder="Sold"
                className="w-20 rounded border border-brand-200 px-2 py-1.5"
                value={it.price_sold ?? ''}
                onChange={(e) =>
                  setItems((prev) => prev.map((x, i) => (i === idx ? { ...x, price_sold: e.target.value } : x)))
                }
              />
              <select
                className="rounded border border-brand-200 px-2 py-1.5"
                value={it.status ?? 'purchased'}
                onChange={(e) =>
                  setItems((prev) => prev.map((x, i) => (i === idx ? { ...x, status: e.target.value as ItemStatus } : x)))
                }
              >
                {STATUS_OPTIONS.map((s) => (
                  <option key={s} value={s}>
                    {STATUS_LABELS[s]}
                  </option>
                ))}
              </select>
              <button type="button" onClick={() => removeItem(idx)} className="text-red-600 text-sm">
                Remove
              </button>
            </div>
          ))}
        </div>
        <div>
          <label className="block text-sm font-medium text-ink mb-1">Notes</label>
          <textarea
            className="w-full rounded-lg border border-brand-200 px-3 py-2"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={2}
          />
        </div>
        <div className="flex gap-3">
          <button
            type="submit"
            disabled={saving || !storeId || !buyingGroupId || validPayments.length === 0}
            className="px-4 py-2 bg-brand-600 text-white rounded-lg font-medium hover:bg-brand-700 disabled:opacity-50"
          >
            {saving ? 'Creating…' : 'Create order'}
          </button>
          <button type="button" onClick={onCancel} className="px-4 py-2 border border-brand-300 rounded-lg">
            Cancel
          </button>
        </div>
      </form>
    </div>
  )
}
