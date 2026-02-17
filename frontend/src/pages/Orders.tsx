import React, { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { SearchableCombobox } from '../components/SearchableCombobox'
import type { Order, BuyingGroup, Shipment, Store, StoreAccount, PaymentMethod, PaymentMethodNested, Item, ItemStatus } from '../api/types'
import { getTrackingInfo } from '../utils/tracking'

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
  returned: 'Returned',
  refunded: 'Refunded',
}

/** Progression from shipped through paid (advance-to-next-status flow) */
const STATUS_PROGRESSION: ItemStatus[] = [
  'shipped',
  'submitted',
  'delivered',
  'scanned',
  'payment_requested',
  'payment_sent',
  'payment_received',
]

function getNextStatus(current: ItemStatus): ItemStatus | null {
  const idx = STATUS_PROGRESSION.indexOf(current)
  if (idx < 0 || idx >= STATUS_PROGRESSION.length - 1) return null
  return STATUS_PROGRESSION[idx + 1]
}

/** Map each status to its corresponding date field (set when advancing) */
const STATUS_TO_DATE_FIELD: Record<string, keyof Item> = {
  shipped: 'shipped_at',
  submitted: 'submitted_at',
  delivered: 'delivered_at',
  scanned: 'scanned_at',
  payment_requested: 'payment_requested_at',
  payment_sent: 'payment_sent_at',
  payment_received: 'payment_received_at',
}

export default function Orders() {
  const [orders, setOrders] = useState<Order[]>([])
  const [groups, setGroups] = useState<BuyingGroup[]>([])
  const [loading, setLoading] = useState(true)
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set())
  const [selectedItemIds, setSelectedItemIds] = useState<Set<number>>(new Set())
  const [trackingInput, setTrackingInput] = useState('')
  const [shipping, setShipping] = useState(false)
  const [trackingEdits, setTrackingEdits] = useState<Record<number, string>>({})
  const [savingTrackingId, setSavingTrackingId] = useState<number | null>(null)

  const [shipments, setShipments] = useState<Shipment[]>([])
  const [copyingId, setCopyingId] = useState<number | null>(null)
  const [stores, setStores] = useState<Store[]>([])
  const [paymentMethods, setPaymentMethods] = useState<PaymentMethod[]>([])
  const [accountsByStore, setAccountsByStore] = useState<Record<number, StoreAccount[]>>({})
  const [orderEdits, setOrderEdits] = useState<Record<number, { store_order_number?: string }>>({})
  const [savingOrderId, setSavingOrderId] = useState<number | null>(null)
  const [itemEdits, setItemEdits] = useState<Record<number, { quantity?: number; description?: string; price_paid?: string; price_sold?: string; status?: ItemStatus }>>({})
  const [paymentEdits, setPaymentEdits] = useState<Record<number, { payment_method_id: number; amount: string }[]>>({})
  const [savingItemId, setSavingItemId] = useState<number | null>(null)
  const [deletingItemId, setDeletingItemId] = useState<number | null>(null)
  const [confirmDeleteItemId, setConfirmDeleteItemId] = useState<number | null>(null)
  const [advancingGroupKey, setAdvancingGroupKey] = useState<string | null>(null)
  const hasSetInitialExpanded = useRef(false)
  const paymentSaveTimeouts = useRef<Record<number, ReturnType<typeof setTimeout>>>({})
  const navigate = useNavigate()

  const loadAccountsForStore = (storeId: number) =>
    api.get<StoreAccount[]>(`/stores/${storeId}/accounts`).then((list) => {
      setAccountsByStore((prev) => ({ ...prev, [storeId]: list }))
    })

  useEffect(() => {
    Promise.all([
      api.get<Order[]>('/orders'),
      api.get<BuyingGroup[]>('/buying-groups'),
      api.get<Shipment[]>('/shipments'),
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

  /** Flat list of payment methods (parents + sub-methods) for order expanded view selection */
  const flatPaymentMethods = useMemo(
    () => paymentMethods.flatMap((pm) => [pm, ...(pm.sub_methods ?? [])]),
    [paymentMethods]
  )

  /** Options for payment combobox: id + name. Sub-methods get "Parent — Sub" so search by main method shows sub-methods. */
  const paymentMethodOptions = useMemo(() => {
    return flatPaymentMethods.map((pm: PaymentMethod | PaymentMethodNested) => {
      const parent =
        pm.parent_id != null ? paymentMethods.find((p) => p.id === pm.parent_id) : null
      const name = parent ? `${parent.label} — ${pm.label}` : pm.label
      return { id: pm.id, name }
    })
  }, [paymentMethods, flatPaymentMethods])

  useEffect(() => {
    if (!loading && orders.length > 0 && !hasSetInitialExpanded.current) {
      hasSetInitialExpanded.current = true
      setExpandedIds(new Set(orders.map((o) => o.id)))
    }
  }, [loading, orders])

  const itemIdToTracking = shipments.reduce<Record<number, string>>((acc, s) => {
    const tn = s.tracking_number?.trim()
    if (tn)
      for (const si of s.shipment_items || []) acc[si.item_id] = tn
    return acc
  }, {})
  const getTracking = (itemId: number) => itemIdToTracking[itemId] ?? ''

  const getShipmentForItem = (itemId: number) =>
    shipments.find((s) => s.shipment_items?.some((si) => si.item_id === itemId))

  type ItemGroup = { key: string; label: string; trackingNumber: string | null; shipment: Shipment | null; items: Item[] }
  const groupOrderItemsByShipment = (order: Order): ItemGroup[] => {
    const itemIdToShipment = new Map<number, { shipment: Shipment; trackingNumber: string }>()
    for (const s of shipments) {
      const tn = s.tracking_number?.trim() ?? ''
      for (const si of s.shipment_items ?? []) {
        itemIdToShipment.set(si.item_id, { shipment: s, trackingNumber: tn })
      }
    }
    const byKey = new Map<string | number, ItemGroup>()
    for (const item of order.items ?? []) {
      const info = itemIdToShipment.get(item.id)
      const key = info ? info.shipment.id : 'unshipped'
      if (!byKey.has(key)) {
        byKey.set(key, {
          key: String(key),
          label: info ? 'Shipment' : 'Unshipped',
          trackingNumber: info?.trackingNumber ?? null,
          shipment: info?.shipment ?? null,
          items: [],
        })
      }
      byKey.get(key)!.items.push(item)
    }
    const unshipped = byKey.get('unshipped')
    const shipped = Array.from(byKey.entries())
      .filter(([k]) => k !== 'unshipped')
      .sort(([a], [b]) => (a as number) - (b as number))
      .map(([, g]) => g)
    return [...(unshipped ? [unshipped] : []), ...shipped]
  }

  const removeItemFromShipment = async (shipment: Shipment, itemId: number) => {
    const remaining = (shipment.shipment_items ?? [])
      .filter((si) => si.item_id !== itemId)
      .map((si) => si.item_id)
    if (remaining.length === 0) {
      await api.delete(`/shipments/${shipment.id}`)
    } else {
      await api.patch(`/shipments/${shipment.id}`, { item_ids: remaining })
    }
  }

  const saveItemTracking = async (itemId: number, newValue: string) => {
    const trimmed = newValue.trim()
    setSavingTrackingId(itemId)
    try {
      const shipment = getShipmentForItem(itemId)
      if (trimmed) {
        if (shipment) {
          await removeItemFromShipment(shipment, itemId)
          await api.post('/shipments', { item_ids: [itemId], tracking_number: trimmed })
        } else {
          await api.post('/shipments', { item_ids: [itemId], tracking_number: trimmed })
        }
      } else {
        if (shipment) {
          await removeItemFromShipment(shipment, itemId)
        }
      }
      const [ordersData, shipmentsData] = await Promise.all([
        api.get<Order[]>('/orders'),
        api.get<Shipment[]>('/shipments'),
      ])
      setOrders(ordersData)
      setShipments(shipmentsData)
      setTrackingEdits((prev) => {
        const next = { ...prev }
        delete next[itemId]
        return next
      })
    } catch (e) {
      console.error(e)
    } finally {
      setSavingTrackingId(null)
    }
  }

  const getGroupName = (id: number | null) => (id ? groups.find((g) => g.id === id)?.name ?? '—' : '—')
  const orderGroupNames = (o: Order) => {
    const ids = [...new Set((o.items ?? []).map((i) => i.buying_group_id).filter((id): id is number => id != null))]
    if (ids.length === 0) return '—'
    return ids.map(getGroupName).filter((n) => n !== '—').join(', ') || '—'
  }
  const toggleExpanded = (id: number) => {
    setExpandedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }
  const toggleItemSelect = (itemId: number, items: { id: number }[]) => {
    setSelectedItemIds((prev) => {
      const next = new Set(prev)
      if (next.has(itemId)) next.delete(itemId)
      else next.add(itemId)
      return next
    })
  }
  const toggleSelectAll = (items: { id: number }[]) => {
    const ids = items.map((i) => i.id)
    const allSelected = ids.every((id) => selectedItemIds.has(id))
    setSelectedItemIds(allSelected ? new Set() : new Set(ids))
  }
  const shipSelected = async () => {
    const ids = [...selectedItemIds]
    const tracking = trackingInput.trim()
    if (ids.length === 0 || !tracking) return
    setShipping(true)
    try {
      await api.post('/shipments', { item_ids: ids, tracking_number: tracking })
      const [ordersData, shipmentsData] = await Promise.all([
        api.get<Order[]>('/orders'),
        api.get<Shipment[]>('/shipments'),
      ])
      setOrders(ordersData)
      setShipments(shipmentsData)
      setSelectedItemIds(new Set())
      setTrackingInput('')
    } catch (e) {
      console.error(e)
    } finally {
      setShipping(false)
    }
  }

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
      const payload = payment_methods.map((pm) => ({
        payment_method_id: pm.payment_method_id,
        amount: pm.amount?.trim() || null,
      }))
      const updated = await api.patch<Order>(`/orders/${orderId}`, { payment_methods: payload })
      setOrders((prev) => prev.map((o) => (o.id === orderId ? updated : o)))
    } catch (e) {
      console.error(e)
    } finally {
      setSavingOrderId(null)
    }
  }

  // Autosave payment edits when valid and different from server (debounced per order)
  useEffect(() => {
    const orderIds = Object.keys(paymentEdits).map(Number)
    orderIds.forEach((orderId) => {
      if (savingOrderId === orderId) return
      const order = orders.find((o) => o.id === orderId)
      if (!order) return
      const rows = paymentEdits[orderId]
      if (!rows?.length) return
      const totalPaid = orderTotals(order.items ?? [])
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
        rows.some(
          (r, i) =>
            serverRows[i]?.payment_method_id !== r.payment_method_id ||
            (serverRows[i]?.amount ?? '') !== (r.amount ?? '')
        )
      if (!hasChanges) return
      const t = paymentSaveTimeouts.current[orderId]
      if (t) clearTimeout(t)
      paymentSaveTimeouts.current[orderId] = setTimeout(() => {
        delete paymentSaveTimeouts.current[orderId]
        updateOrderPayments(
          orderId,
          rows.map((r) => ({ payment_method_id: r.payment_method_id, amount: r.amount }))
        ).then(() => {
          setPaymentEdits((prev) => {
            const next = { ...prev }
            delete next[orderId]
            return next
          })
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

  const updateItem = async (itemId: number, data: Partial<Item>) => {
    setSavingItemId(itemId)
    try {
      const updated = await api.patch<Item>(`/items/${itemId}`, data)
      setOrders((prev) =>
        prev.map((o) =>
          o.items?.some((i) => i.id === itemId)
            ? { ...o, items: o.items.map((i) => (i.id === itemId ? updated : i)) }
            : o
        )
      )
      setItemEdits((prev) => {
        const next = { ...prev }
        delete next[itemId]
        return next
      })
    } catch (e) {
      console.error(e)
    } finally {
      setSavingItemId(null)
    }
  }

  const advanceShipmentToNextStatus = async (group: { key: string; items: Item[] }) => {
    const toUpdate = group.items.filter((item) => getNextStatus(item.status))
    if (toUpdate.length === 0) return
    setAdvancingGroupKey(group.key)
    const now = new Date().toISOString().slice(0, 19)
    try {
      for (const item of toUpdate) {
        const next = getNextStatus(item.status)
        if (next) {
          const dateField = STATUS_TO_DATE_FIELD[next]
          const payload: Partial<Item> = { status: next }
          if (dateField) payload[dateField] = now
          await updateItem(item.id, payload)
        }
      }
    } finally {
      setAdvancingGroupKey(null)
    }
  }

  const addItem = async (order: Order) => {
    try {
      const newItem = await api.post<Item>('/items', {
        order_id: order.id,
        status: 'purchased',
      })
      setOrders((prev) =>
        prev.map((o) => (o.id === order.id ? { ...o, items: [...(o.items ?? []), newItem] } : o))
      )
    } catch (e) {
      console.error(e)
    }
  }

  const deleteItem = async (itemId: number) => {
    setDeletingItemId(itemId)
    try {
      await api.delete(`/items/${itemId}`)
      const [ordersData, shipmentsData] = await Promise.all([
        api.get<Order[]>('/orders'),
        api.get<Shipment[]>('/shipments'),
      ])
      setOrders(ordersData)
      setShipments(shipmentsData)
      setSelectedItemIds((prev) => {
        const next = new Set(prev)
        next.delete(itemId)
        return next
      })
      setItemEdits((prev) => {
        const next = { ...prev }
        delete next[itemId]
        return next
      })
      setTrackingEdits((prev) => {
        const next = { ...prev }
        delete next[itemId]
        return next
      })
    } catch (e) {
      console.error(e)
    } finally {
      setDeletingItemId(null)
    }
  }

  const parseDecimal = (s: string | null | undefined): number => {
    if (s == null || String(s).trim() === '') return 0
    const n = parseFloat(String(s))
    return Number.isNaN(n) ? 0 : n
  }
  const orderTotals = (items: { price_paid?: string | null; price_sold?: string | null; quantity?: number }[]) => {
    let totalPaid = 0
    for (const item of items) {
      const qty = Math.max(0, item.quantity ?? 1)
      totalPaid += parseDecimal(item.price_paid) * qty
    }
    return totalPaid
  }

  const nowIso = () => new Date().toISOString().slice(0, 19)
  const copyOrder = async (o: Order, e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (!o.items?.length) return
    setCopyingId(o.id)
    try {
      const payload = {
        store_id: o.store_id,
        store_account_id: o.store_account_id ?? null,
        store_order_number: null,
        purchase_date: o.purchase_date ?? nowIso(),
        notes: o.notes ?? undefined,
        buying_group_id: o.buying_group_id ?? null,
        payment_methods: (o.order_payments ?? []).map((op) => ({
          payment_method_id: op.payment_method_id,
          amount: op.amount ?? undefined,
        })),
        items: o.items.map((item) => ({
          price_paid: item.price_paid ?? undefined,
          price_sold: item.price_sold ?? undefined,
          status: item.status,
          quantity: item.quantity ?? 1,
          description: item.description ?? undefined,
        })),
      }
      const created = await api.post<Order>('/orders', payload)
      navigate(`/orders/${created.id}`)
    } catch (err) {
      console.error(err)
    } finally {
      setCopyingId(null)
    }
  }

  if (loading) return <div className="text-ink-muted">Loading orders…</div>

  return (
    <div>
      <div className="flex items-center justify-between mb-8">
        <h1 className="text-2xl font-semibold text-ink">Orders</h1>
        <Link
          to="/orders/new"
          className="px-4 py-2 bg-brand-600 text-white rounded-lg font-medium hover:bg-brand-700 transition"
        >
          New order
        </Link>
      </div>
      <div className="bg-white dark:bg-gray-800 rounded-xl border border-brand-200/80 dark:border-gray-700 shadow-sm overflow-hidden">
        <table className="w-full">
          <thead className="bg-brand-100/50 dark:bg-gray-700/50 border-b border-brand-200/80 dark:border-gray-700">
            <tr>
              <th className="w-10 text-left py-3 px-2" />
              <th className="text-left py-3 px-4 text-sm font-medium text-ink">Order</th>
              <th className="text-left py-3 px-4 text-sm font-medium text-ink">Store</th>
              <th className="text-left py-3 px-4 text-sm font-medium text-ink">Items</th>
              <th className="text-left py-3 px-4 text-sm font-medium text-ink">Buying group</th>
              <th className="text-left py-3 px-4 text-sm font-medium text-ink">Created</th>
              <th className="w-12 text-right py-3 px-2" />
            </tr>
          </thead>
          <tbody>
            {orders.length === 0 ? (
              <tr>
                <td colSpan={7} className="py-12 text-center text-ink-muted">
                  No orders yet. Create one to get started.
                </td>
              </tr>
            ) : (
              orders.map((o) => (
                <React.Fragment key={o.id}>
                  <tr
                    className="border-b border-brand-100 last:border-0 hover:bg-brand-50/50 dark:hover:bg-gray-600 cursor-pointer"
                    onClick={() => toggleExpanded(o.id)}
                  >
                    <td className="py-3 px-2 text-ink-muted">
                      <span className="inline-block transition-transform" style={{ transform: expandedIds.has(o.id) ? 'rotate(90deg)' : 'none' }}>
                        ▶
                      </span>
                    </td>
                    <td className="py-3 px-4" onClick={(e) => e.stopPropagation()}>
                      <div className="flex items-center gap-2">
                        <Link
                          to={`/orders/${o.id}`}
                          title="Edit order"
                          onClick={(e) => e.stopPropagation()}
                          className="shrink-0 p-1 rounded text-ink-muted hover:text-brand-600 hover:bg-brand-50 dark:hover:bg-gray-600 dark:hover:text-brand-400 transition"
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
                          className="flex-1 min-w-[5rem] h-6 rounded border border-transparent bg-transparent px-1 py-0 text-sm font-medium text-brand-700 dark:text-brand-400 focus:border-brand-300 focus:bg-white dark:focus:bg-gray-700 focus:outline-none focus:ring-1 focus:ring-brand-500 disabled:opacity-60"
                          onClick={(e) => e.stopPropagation()}
                        />
                      </div>
                    </td>
                    <td className="py-3 px-4 text-sm" onClick={(e) => e.stopPropagation()}>
                      <div className="flex flex-wrap gap-2 items-center">
                        <div className="min-w-[140px]">
                          <SearchableCombobox<Store>
                            inputClassName="h-6 py-0 px-2 text-sm rounded border border-brand-200 dark:border-gray-600 w-full min-w-0 text-ink focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent disabled:opacity-60 disabled:cursor-not-allowed dark:bg-gray-700"
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
                        <div className="min-w-[100px]">
                          <SearchableCombobox<StoreAccount>
                            inputClassName="h-6 py-0 px-2 text-sm rounded border border-brand-200 dark:border-gray-600 w-full min-w-0 text-ink focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent disabled:opacity-60 disabled:cursor-not-allowed dark:bg-gray-700"
                            options={accountsByStore[o.store_id] ?? []}
                            value={
                              (accountsByStore[o.store_id] ?? []).find((a) => a.id === o.store_account_id) ?? null
                            }
                            onChange={(a) =>
                              updateOrder(o.id, { store_account_id: a?.id ?? null })
                            }
                            onCreate={
                              o.store_id
                                ? async (name) => {
                                    const a = await api.post<StoreAccount>(
                                      `/stores/${o.store_id}/accounts`,
                                      { name }
                                    )
                                    setAccountsByStore((prev) => ({
                                      ...prev,
                                      [o.store_id]: [...(prev[o.store_id] ?? []), a],
                                    }))
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
                      </div>
                    </td>
                    <td className="py-3 px-4 text-ink-muted">{o.items?.reduce((sum, i) => sum + (i.quantity ?? 1), 0) ?? 0} items</td>
                    <td className="py-3 px-4 text-sm" onClick={(e) => e.stopPropagation()}>
                      <div className="min-w-[120px]">
                        <SearchableCombobox<BuyingGroup>
                          inputClassName="h-6 py-0 px-2 text-sm rounded border border-brand-200 dark:border-gray-600 w-full min-w-0 text-ink focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent disabled:opacity-60 disabled:cursor-not-allowed dark:bg-gray-700"
                          options={groups}
                          value={groups.find((g) => g.id === o.buying_group_id) ?? null}
                          onChange={(g) =>
                            updateOrder(o.id, { buying_group_id: g?.id ?? null })
                          }
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
                    </td>
                    <td className="py-3 px-4 text-sm text-ink-muted">
                      {new Date(o.created_at).toLocaleString()}
                    </td>
                    <td className="py-3 px-2 text-right">
                      {o.items && o.items.length > 0 ? (
                        <button
                          type="button"
                          title="Copy order"
                          onClick={(e) => copyOrder(o, e)}
                          disabled={copyingId === o.id}
                          className="p-1.5 rounded text-ink-muted hover:text-brand-600 hover:bg-brand-50 dark:hover:bg-gray-600 dark:hover:text-brand-400 transition disabled:opacity-50"
                        >
                          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                          </svg>
                        </button>
                      ) : null}
                    </td>
                  </tr>
                  {expandedIds.has(o.id) && (
                    <tr className="bg-brand-50/50 dark:bg-gray-600/80 border-b border-brand-100 dark:border-gray-700" onClick={(e) => e.stopPropagation()}>
                      <td colSpan={7} className="py-0 px-4 pb-3 pt-0">
                        <div className="space-y-4 pt-3">
                          <div>
                            {(() => {
                              const rows =
                                paymentEdits[o.id] ??
                                (o.order_payments ?? []).map((op) => ({
                                  payment_method_id: op.payment_method_id,
                                  amount: op.amount != null ? String(op.amount) : '',
                                }))
                              const totalPaid = orderTotals(o.items ?? [])
                              const paymentSum = rows.reduce((s, r) => s + parseDecimal(r.amount), 0)
                              const paymentMatchesTotal =
                                rows.length > 0 && Math.round(paymentSum * 100) === Math.round(totalPaid * 100)
                              const usedIds = new Set(rows.map((r) => r.payment_method_id))
                              const firstUnused =
                                flatPaymentMethods.find((pm) => !usedIds.has(pm.id)) ?? flatPaymentMethods[0]
                              const amountRemaining = Math.max(0, totalPaid - paymentSum)
                              const addPaymentDisabled =
                                savingOrderId === o.id ||
                                paymentSum >= totalPaid ||
                                flatPaymentMethods.length === 0
                              const isLastRow = (idx: number) => idx === rows.length - 1
                              return (
                                <div className="space-y-1">
                                  <p className="text-xs font-medium text-ink-muted mb-1">
                                    Payment — Order total: <span className="font-mono text-ink">${totalPaid.toFixed(2)}</span>
                                    {rows.length > 0 && (
                                      <>
                                        {' — '}
                                        Payment total: <span className="font-mono text-ink">${paymentSum.toFixed(2)}</span>
                                        {!paymentMatchesTotal && (
                                          <span className="text-amber-600 ml-1">(should equal order total)</span>
                                        )}
                                      </>
                                    )}
                                  </p>
                                  {rows.map((row, idx) => (
                                    <div key={idx} className="flex items-center gap-2 flex-wrap">
                                      <div className="min-w-[140px]">
                                        <SearchableCombobox<{ id: number; name: string }>
                                          inputClassName="h-6 py-0 px-2 text-sm rounded border border-brand-200 dark:border-gray-600 w-full min-w-0 text-ink focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent disabled:opacity-60 disabled:cursor-not-allowed dark:bg-gray-700"
                                          options={paymentMethodOptions}
                                          value={
                                            row.payment_method_id
                                              ? paymentMethodOptions.find((opt) => opt.id === row.payment_method_id) ?? null
                                              : null
                                          }
                                          onChange={(opt) =>
                                            setPaymentEdits((prev) => ({
                                              ...prev,
                                              [o.id]: rows.map((r, i) =>
                                                i === idx ? { ...r, payment_method_id: opt?.id ?? 0 } : r
                                              ),
                                            }))
                                          }
                                          onCreate={async (label) => {
                                            const pm = await api.post<PaymentMethod>('/payment-methods', { label })
                                            setPaymentMethods((prev) => [...prev, pm].sort((a, b) => a.label.localeCompare(b.label)))
                                            setPaymentEdits((prev) => ({
                                              ...prev,
                                              [o.id]: rows.map((r, i) =>
                                                i === idx ? { ...r, payment_method_id: pm.id } : r
                                              ),
                                            }))
                                            return { id: pm.id, name: pm.label }
                                          }}
                                          placeholder="Type to search or add payment method…"
                                        />
                                      </div>
                                      <input
                                        type="text"
                                        placeholder="Amount"
                                        value={row.amount}
                                        onChange={(e) => {
                                          const raw = e.target.value
                                          const otherSum = rows.reduce(
                                            (s, r, i) => (i === idx ? s : s + parseDecimal(r.amount)),
                                            0
                                          )
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
                                            [o.id]: rows.map((r, i) => (i === idx ? { ...r, amount } : r)),
                                          }))
                                        }}
                                        className="w-24 h-6 rounded border border-brand-200 dark:border-gray-600 px-2 py-0 text-sm font-mono bg-white dark:bg-gray-700"
                                      />
                                      <button
                                        type="button"
                                        onClick={() =>
                                          setPaymentEdits((prev) => ({
                                            ...prev,
                                            [o.id]: rows.filter((_, i) => i !== idx),
                                          }))
                                        }
                                        className="p-0.5 rounded text-ink-muted hover:text-ink hover:bg-brand-100 dark:hover:bg-gray-600 transition"
                                        title="Remove payment method"
                                        aria-label="Remove payment method"
                                      >
                                        <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
                                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                                        </svg>
                                      </button>
                                      {isLastRow(idx) && (
                                        <button
                                          type="button"
                                          title="Add payment method"
                                          disabled={addPaymentDisabled}
                                          onClick={() =>
                                            setPaymentEdits((prev) => ({
                                              ...prev,
                                              [o.id]: [
                                                ...rows,
                                                {
                                                  payment_method_id: firstUnused?.id ?? 0,
                                                  amount: amountRemaining > 0 ? amountRemaining.toFixed(2) : '',
                                                },
                                              ],
                                            }))
                                          }
                                          className="p-0.5 rounded text-ink-muted hover:text-brand-600 hover:bg-brand-100 dark:hover:bg-gray-600 disabled:opacity-50 disabled:cursor-not-allowed"
                                        >
                                          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                                          </svg>
                                        </button>
                                      )}
                                    </div>
                                  ))}
                                  {rows.length === 0 && (
                                    <div className="flex items-center gap-2 flex-wrap">
                                      <button
                                        type="button"
                                        title="Add payment method"
                                        disabled={addPaymentDisabled}
                                        onClick={() =>
                                          setPaymentEdits((prev) => ({
                                            ...prev,
                                            [o.id]: [
                                              {
                                                payment_method_id: firstUnused?.id ?? 0,
                                                amount: amountRemaining > 0 ? amountRemaining.toFixed(2) : '',
                                              },
                                            ],
                                          }))
                                        }
                                        className="p-0.5 rounded text-ink-muted hover:text-brand-600 hover:bg-brand-100 dark:hover:bg-gray-600 disabled:opacity-50 disabled:cursor-not-allowed"
                                      >
                                        <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
                                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                                        </svg>
                                      </button>
                                    </div>
                                  )}
                                </div>
                              )
                            })()}
                          </div>
                        </div>
                        {o.items && o.items.length > 0 ? (
                          <div className="mt-4">
                            <div className="rounded-lg border border-brand-200/80 dark:border-gray-700 overflow-hidden bg-white dark:bg-gray-800">
                              <table className="w-full text-sm">
                                <thead>
                                  <tr className="bg-brand-100/50 dark:bg-gray-700/50 text-left">
                                    <th className="w-8 py-2 px-2">
                                      <input
                                        type="checkbox"
                                        checked={o.items.every((i) => selectedItemIds.has(i.id)) && o.items.length > 0}
                                        onChange={() => toggleSelectAll(o.items)}
                                        onClick={(e) => e.stopPropagation()}
                                        className="rounded border-brand-300 text-brand-600 focus:ring-brand-500"
                                      />
                                    </th>
                                    <th className="py-2 px-2 font-medium text-ink-muted w-12">Qty</th>
                                    <th className="py-2 px-2 font-medium text-ink-muted">Description</th>
                                    <th className="py-2 px-2 font-medium text-ink-muted">Tracking</th>
                                    <th className="py-2 pl-2 pr-2 font-medium text-ink-muted w-0 whitespace-nowrap">Cost</th>
                                    <th className="py-2 pl-0 pr-2 font-medium text-ink-muted w-0 whitespace-nowrap">Payout</th>
                                    <th className="py-2 px-2 font-medium text-ink-muted">Status</th>
                                    <th className="w-8 py-2 px-1.5 text-right">
                                      <button
                                        type="button"
                                        onClick={(e) => {
                                          e.stopPropagation()
                                          addItem(o)
                                        }}
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
                                  {groupOrderItemsByShipment(o).map((group) => (
                                    <React.Fragment key={group.key}>
                                      {group.items.length > 0 && (
                                        <tr className="border-t border-brand-100 dark:border-gray-700 bg-brand-50/50 dark:bg-gray-700/30">
                                          <td colSpan={8} className="py-1.5 px-2 text-xs font-medium text-ink-muted">
                                            {group.label === 'Unshipped'
                                              ? 'Unshipped'
                                              : (
                                                  <>
                                                    Shipment
                                                    {group.trackingNumber && (
                                                      <span className="ml-2 font-mono text-ink">
                                                        {group.trackingNumber}
                                                      </span>
                                                    )}
                                                    {(() => {
                                                      const nextStatuses = group.items
                                                        .map((item) => getNextStatus(item.status))
                                                        .filter((s): s is ItemStatus => s != null)
                                                      const lowestNext =
                                                        nextStatuses.length > 0
                                                          ? STATUS_PROGRESSION[
                                                              Math.min(
                                                                ...nextStatuses.map((s) => STATUS_PROGRESSION.indexOf(s))
                                                              )
                                                            ]
                                                          : null
                                                      const label = lowestNext ? STATUS_LABELS[lowestNext] ?? lowestNext : null
                                                      return label ? (
                                                        <button
                                                          type="button"
                                                          onClick={(e) => {
                                                            e.stopPropagation()
                                                            advanceShipmentToNextStatus(group)
                                                          }}
                                                          disabled={advancingGroupKey === group.key}
                                                          className="ml-3 px-2 py-0.5 text-xs font-medium rounded bg-brand-600 text-white hover:bg-brand-700 disabled:opacity-50 disabled:cursor-not-allowed transition"
                                                          title={`Advance items to ${label}`}
                                                        >
                                                          {advancingGroupKey === group.key
                                                            ? 'Advancing…'
                                                            : `Mark shipment as ${label}`}
                                                        </button>
                                                      ) : null
                                                    })()}
                                                  </>
                                                )}
                                          </td>
                                        </tr>
                                      )}
                                      {group.items.map((item) => (
                                    <tr key={item.id} className="border-t border-brand-100 dark:border-gray-700">
                                      <td className="py-2 px-2">
                                        <input
                                          type="checkbox"
                                          checked={selectedItemIds.has(item.id)}
                                          onChange={() => toggleItemSelect(item.id, o.items)}
                                          onClick={(e) => e.stopPropagation()}
                                          className="rounded border-brand-300 text-brand-600 focus:ring-brand-500"
                                        />
                                      </td>
                                      <td className="py-2 px-2">
                                        <input
                                          type="number"
                                          min={1}
                                          value={itemEdits[item.id]?.quantity ?? (item.quantity ?? 1)}
                                          onChange={(e) => {
                                            const n = parseInt(e.target.value, 10)
                                            if (!Number.isNaN(n) && n >= 1)
                                              setItemEdits((prev) => ({ ...prev, [item.id]: { ...prev[item.id], quantity: n } }))
                                          }}
                                          onBlur={() => {
                                            const v = itemEdits[item.id]?.quantity
                                            if (v != null && v !== (item.quantity ?? 1)) updateItem(item.id, { quantity: v })
                                          }}
                                          disabled={savingItemId === item.id}
                                          className="w-12 h-6 rounded border border-brand-200 dark:border-gray-600 bg-white dark:bg-gray-700 px-1 py-0 text-sm text-center focus:border-brand-500 focus:outline-none disabled:opacity-60"
                                        />
                                      </td>
                                      <td className="py-2 px-2">
                                        <input
                                          type="text"
                                          value={itemEdits[item.id]?.description ?? (item.description ?? '')}
                                          onChange={(e) =>
                                            setItemEdits((prev) => ({ ...prev, [item.id]: { ...prev[item.id], description: e.target.value } }))
                                          }
                                          onBlur={() => {
                                            const v = (itemEdits[item.id]?.description ?? item.description ?? '').trim()
                                            if (v !== (item.description ?? '')) updateItem(item.id, { description: v || null })
                                          }}
                                          placeholder="Description"
                                          disabled={savingItemId === item.id}
                                          className="w-full min-w-[7rem] h-6 rounded border border-brand-200 dark:border-gray-600 bg-white dark:bg-gray-700 px-2 py-0 text-sm focus:border-brand-500 focus:outline-none disabled:opacity-60"
                                        />
                                      </td>
                                      <td className="py-2 px-2">
                                        <div className="flex items-stretch min-w-0 h-6 rounded border border-brand-200 dark:border-gray-600 bg-white dark:bg-gray-700 focus-within:border-brand-500 focus-within:outline-none mb-1">
                                          <input
                                            type="text"
                                            value={trackingEdits[item.id] ?? getTracking(item.id)}
                                            onChange={(e) =>
                                              setTrackingEdits((prev) => ({ ...prev, [item.id]: e.target.value }))
                                            }
                                            onBlur={(e) => {
                                              const v = e.target.value
                                              const current = getTracking(item.id)
                                              if (v.trim() !== current.trim() || (v === '' && current !== '')) {
                                                saveItemTracking(item.id, v)
                                              } else {
                                                setTrackingEdits((prev) => {
                                                  const next = { ...prev }
                                                  delete next[item.id]
                                                  return next
                                                })
                                              }
                                            }}
                                            onClick={(e) => e.stopPropagation()}
                                            placeholder=""
                                            disabled={savingTrackingId === item.id}
                                            className="flex-1 min-w-[6rem] h-6 border-0 rounded-l bg-white dark:bg-gray-700 px-2 py-0 text-sm focus:ring-0 focus:outline-none disabled:opacity-60"
                                          />
                                          {(() => {
                                            const raw = trackingEdits[item.id] ?? getTracking(item.id)
                                            const info = raw ? getTrackingInfo(raw) : null
                                            if (!info) return null
                                            return (
                                              <a
                                                href={info.url}
                                                target="_blank"
                                                rel="noopener noreferrer"
                                                onClick={(e) => e.stopPropagation()}
                                                className="shrink-0 flex items-center gap-1 h-6 border-l border-brand-200 dark:border-gray-600 pl-2 pr-2 py-0 text-xs font-medium text-brand-600 dark:text-brand-400 hover:bg-brand-50 dark:hover:bg-gray-700/50"
                                                title={`Track via ${info.carrier}`}
                                              >
                                                <span className="whitespace-nowrap">{info.carrier}</span>
                                                <svg className="w-3.5 h-3.5 shrink-0 opacity-70" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
                                                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                                                </svg>
                                              </a>
                                            )
                                          })()}
                                        </div>
                                      </td>
                                      <td className="py-2 pl-2 pr-2 w-0 align-top">
                                        <input
                                          type="text"
                                          value={itemEdits[item.id]?.price_paid ?? (item.price_paid ?? '')}
                                          onChange={(e) =>
                                            setItemEdits((prev) => ({ ...prev, [item.id]: { ...prev[item.id], price_paid: e.target.value } }))
                                          }
                                          onBlur={() => {
                                            const v = (itemEdits[item.id]?.price_paid ?? item.price_paid ?? '').trim()
                                            if (v !== (item.price_paid ?? '')) updateItem(item.id, { price_paid: v || null })
                                          }}
                                          placeholder="0.00"
                                          disabled={savingItemId === item.id}
                                          className="w-20 h-6 rounded border border-brand-200 dark:border-gray-600 bg-white dark:bg-gray-700 px-1.5 py-0 text-sm font-mono focus:border-brand-500 focus:outline-none disabled:opacity-60"
                                        />
                                      </td>
                                      <td className="py-2 pl-0 pr-2 w-0 align-top">
                                        <input
                                          type="text"
                                          value={itemEdits[item.id]?.price_sold ?? (item.price_sold ?? '')}
                                          onChange={(e) =>
                                            setItemEdits((prev) => ({ ...prev, [item.id]: { ...prev[item.id], price_sold: e.target.value } }))
                                          }
                                          onBlur={() => {
                                            const v = (itemEdits[item.id]?.price_sold ?? item.price_sold ?? '').trim()
                                            if (v !== (item.price_sold ?? '')) updateItem(item.id, { price_sold: v || null })
                                          }}
                                          placeholder="0.00"
                                          disabled={savingItemId === item.id}
                                          className="w-20 h-6 rounded border border-brand-200 dark:border-gray-600 bg-white dark:bg-gray-700 px-1.5 py-0 text-sm font-mono focus:border-brand-500 focus:outline-none disabled:opacity-60"
                                        />
                                      </td>
                                      <td className="py-2 px-2">
                                        <select
                                          value={itemEdits[item.id]?.status ?? item.status}
                                          onChange={(e) => {
                                            const s = e.target.value as ItemStatus
                                            setItemEdits((prev) => ({ ...prev, [item.id]: { ...prev[item.id], status: s } }))
                                            updateItem(item.id, { status: s })
                                          }}
                                          disabled={savingItemId === item.id}
                                          className="min-w-[6.5rem] h-6 rounded border border-brand-200 dark:border-gray-600 bg-white dark:bg-gray-700 px-1.5 py-0 text-sm focus:border-brand-500 focus:outline-none disabled:opacity-60"
                                        >
                                          {Object.entries(STATUS_LABELS).map(([val, lbl]) => (
                                            <option key={val} value={val}>
                                              {lbl}
                                            </option>
                                          ))}
                                        </select>
                                      </td>
                                      <td className="py-2 px-1.5 text-right w-8">
                                        <button
                                          type="button"
                                          onClick={(e) => {
                                            e.stopPropagation()
                                            setConfirmDeleteItemId(item.id)
                                          }}
                                          disabled={deletingItemId === item.id}
                                          className="p-1.5 rounded text-ink-muted hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 transition disabled:opacity-50 disabled:cursor-not-allowed"
                                          title="Delete line item"
                                          aria-label="Delete line item"
                                        >
                                          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                                          </svg>
                                        </button>
                                      </td>
                                    </tr>
                                      ))}
                                    </React.Fragment>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                            <div className="mt-2 flex items-center gap-3" onClick={(e) => e.stopPropagation()}>
                              <input
                                type="text"
                                placeholder="Tracking number"
                                value={trackingInput}
                                onChange={(e) => setTrackingInput(e.target.value)}
                                className="h-6 rounded-lg border border-brand-200 dark:border-gray-600 px-3 py-0 text-sm w-48 focus:border-brand-500 focus:ring-1 focus:ring-brand-500 focus:outline-none bg-white dark:bg-gray-700"
                              />
                              <button
                                type="button"
                                disabled={selectedItemIds.size === 0 || !trackingInput.trim() || shipping}
                                onClick={() => shipSelected()}
                                className="text-sm px-3 py-1.5 bg-brand-600 text-white rounded-lg hover:bg-brand-700 disabled:opacity-50 disabled:cursor-not-allowed transition"
                              >
                                {shipping ? 'Shipping…' : 'Ship selected'}
                              </button>
                            </div>
                          </div>
                        ) : (
                          <div className="py-3 text-sm text-ink-muted">No line items.</div>
                        )}
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              ))
            )}
          </tbody>
        </table>
      </div>
      <ConfirmDialog
        open={confirmDeleteItemId !== null}
        message="Delete this line item? This cannot be undone."
        confirmLabel="Delete"
        danger
        onConfirm={() => {
          if (confirmDeleteItemId !== null) {
            deleteItem(confirmDeleteItemId)
            setConfirmDeleteItemId(null)
          }
        }}
        onCancel={() => setConfirmDeleteItemId(null)}
      />
    </div>
  )
}
