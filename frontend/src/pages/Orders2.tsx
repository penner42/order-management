/**
 * Test page: Option 2 layout — horizontal strip per order.
 * Left: vertical order box (order #, store, account, group, date, payment).
 * Right: full line-items table (one row per item).
 */
import React, { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { SearchableCombobox } from '../components/SearchableCombobox'
import type {
  Order,
  BuyingGroup,
  Shipment,
  Store,
  StoreAccount,
  PaymentMethod,
  PaymentMethodNested,
  Item,
  ItemStatus,
} from '../api/types'
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
  return_started: 'Return started',
  return_sent: 'Return sent',
  return_received: 'Return received',
  return_refunded: 'Refunded',
  returned: 'Returned',
  refunded: 'Refunded',
}

const STATUS_PROGRESSION: ItemStatus[] = [
  'shipped',
  'submitted',
  'delivered',
  'scanned',
  'payment_requested',
  'payment_sent',
  'payment_received',
]

const STATUS_TO_DATE_FIELD: Record<string, keyof Item> = {
  shipped: 'shipped_at',
  submitted: 'submitted_at',
  delivered: 'delivered_at',
  scanned: 'scanned_at',
  payment_requested: 'payment_requested_at',
  payment_sent: 'payment_sent_at',
  payment_received: 'payment_received_at',
}

function getNextStatus(current: ItemStatus): ItemStatus | null {
  const idx = STATUS_PROGRESSION.indexOf(current)
  if (idx < 0 || idx >= STATUS_PROGRESSION.length - 1) return null
  return STATUS_PROGRESSION[idx + 1]
}

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

export default function Orders2() {
  const [orders, setOrders] = useState<Order[]>([])
  const [groups, setGroups] = useState<BuyingGroup[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedItemIds, setSelectedItemIds] = useState<Set<number>>(new Set())
  const [bulkActionByOrder, setBulkActionByOrder] = useState<Record<number, { action: string; tracking: string; shippedAt: string }>>({})
  const [bulkActionShippingOrderId, setBulkActionShippingOrderId] = useState<number | null>(null)
  const [bulkStatusModal, setBulkStatusModal] = useState<{ order: Order; itemIds: number[] } | null>(null)
  const [submitShipmentModal, setSubmitShipmentModal] = useState<{
    group: { key: string; label: string; trackingNumber: string | null; items: Item[] }
  } | null>(null)
  const [trackingEdits, setTrackingEdits] = useState<Record<number, string>>({})
  const [savingTrackingId, setSavingTrackingId] = useState<number | null>(null)
  const [shipments, setShipments] = useState<Shipment[]>([])
  const [copyingId, setCopyingId] = useState<number | null>(null)
  const [stores, setStores] = useState<Store[]>([])
  const [paymentMethods, setPaymentMethods] = useState<PaymentMethod[]>([])
  const [accountsByStore, setAccountsByStore] = useState<Record<number, StoreAccount[]>>({})
  const [orderEdits, setOrderEdits] = useState<Record<number, { store_order_number?: string; shipping?: string; sales_tax?: string }>>({})
  const [savingOrderId, setSavingOrderId] = useState<number | null>(null)
  const [itemEdits, setItemEdits] = useState<Record<number, { quantity?: number; description?: string; price_paid?: string; price_sold?: string; status?: ItemStatus }>>({})
  const [paymentEdits, setPaymentEdits] = useState<Record<number, { payment_method_id: number; amount: string }[]>>({})
  const [savingItemId, setSavingItemId] = useState<number | null>(null)
  const [deletingItemId, setDeletingItemId] = useState<number | null>(null)
  const [confirmDeleteItemId, setConfirmDeleteItemId] = useState<number | null>(null)
  const [advancingGroupKey, setAdvancingGroupKey] = useState<string | null>(null)
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
      api.get<Shipment[]>('/shipments'),
      api.get<Store[]>('/stores'),
      api.get<PaymentMethod[]>('/payment-methods'),
    ])
      .then(async ([ordersData, groupsData, shipmentsData, storesData, pmData]) => {
        setOrders(ordersData)
        setGroups(groupsData)
        setShipments(shipmentsData)
        setStores(storesData)
        setPaymentMethods(pmData)
        const allAccountsByStore: Record<number, StoreAccount[]> = {}
        await Promise.all(
          storesData.map((store) =>
            api.get<StoreAccount[]>(`/stores/${store.id}/accounts`).then((list) => {
              allAccountsByStore[store.id] = list
            })
          )
        )
        setAccountsByStore(allAccountsByStore)
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
    if (remaining.length === 0) await api.delete(`/shipments/${shipment.id}`)
    else await api.patch(`/shipments/${shipment.id}`, { item_ids: remaining })
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
      } else if (shipment) {
        await removeItemFromShipment(shipment, itemId)
      }
      const [ordersData, shipmentsData] = await Promise.all([
        api.get<Order[]>(defaultOrdersPath()),
        api.get<Shipment[]>('/shipments'),
      ])
      setOrders(ordersData)
      setShipments(shipmentsData)
      setTrackingEdits((prev) => {
        const next = { ...prev }; delete next[itemId]; return next
      })
    } catch (e) {
      console.error(e)
    } finally {
      setSavingTrackingId(null)
    }
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
  const getSelectedIdsForOrder = (order: Order) =>
    (order.items ?? []).filter((i) => selectedItemIds.has(i.id)).map((i) => i.id)

  const getBulkActionState = (orderId: number) =>
    bulkActionByOrder[orderId] ?? { action: '', tracking: '', shippedAt: new Date().toISOString().slice(0, 10) }
  const setBulkActionState = (orderId: number, patch: Partial<{ action: string; tracking: string; shippedAt: string }>) => {
    setBulkActionByOrder((prev) => {
      const current = prev[orderId] ?? { action: '', tracking: '', shippedAt: new Date().toISOString().slice(0, 10) }
      return { ...prev, [orderId]: { ...current, ...patch } }
    })
  }

  const applyBulkInputTracking = async (order: Order) => {
    const ids = getSelectedIdsForOrder(order)
    if (ids.length === 0) return
    const state = getBulkActionState(order.id)
    const tracking = state.tracking.trim()
    if (!tracking) return
    setBulkActionShippingOrderId(order.id)
    try {
      await api.post('/shipments', {
        item_ids: ids,
        tracking_number: tracking,
        shipped_at: state.shippedAt ? `${state.shippedAt}T00:00:00.000Z` : undefined,
      })
      const [ordersData, shipmentsData] = await Promise.all([
        api.get<Order[]>(defaultOrdersPath()),
        api.get<Shipment[]>('/shipments'),
      ])
      setOrders(ordersData)
      setShipments(shipmentsData)
      setSelectedItemIds((prev) => {
        const next = new Set(prev); ids.forEach((id) => next.delete(id)); return next
      })
      setBulkActionByOrder((prev) => {
        const next = { ...prev }; delete next[order.id]; return next
      })
    } catch (e) {
      console.error(e)
    } finally {
      setBulkActionShippingOrderId(null)
    }
  }

  const mergeUpdatedItemsIntoOrders = (updatedItems: Item[]) => {
    if (updatedItems.length === 0) return
    setOrders((prev) =>
      prev.map((o) => {
        const itemIds = new Set(updatedItems.filter((i) => i.order_id === o.id).map((i) => i.id))
        if (itemIds.size === 0) return o
        return {
          ...o,
          items: (o.items ?? []).map((i) => (itemIds.has(i.id) ? updatedItems.find((u) => u.id === i.id)! : i)),
        }
      })
    )
  }

  const applySubmitShipment = async (group: { key: string; items: Item[] }, submissionId: string) => {
    const now = new Date().toISOString().slice(0, 19)
    const toUpdate = group.items.filter((item) => getNextStatus(item.status) === 'submitted')
    if (toUpdate.length === 0) return
    setAdvancingGroupKey(group.key)
    try {
      const res = await api.post<{ items: Item[] }>('/items/bulk-update', {
        updates: toUpdate.map((item) => ({
          item_id: item.id,
          status: 'submitted',
          submitted_at: now,
          submission_id: submissionId.trim() || null,
        })),
      })
      mergeUpdatedItemsIntoOrders(res.items)
      setSubmitShipmentModal(null)
    } catch (e) {
      console.error(e)
    } finally {
      setAdvancingGroupKey(null)
    }
  }

  const applyBulkReceived = async (itemIds: number[], receiptIds: Record<number, string>) => {
    const now = new Date().toISOString().slice(0, 19)
    try {
      const res = await api.post<{ items: Item[] }>('/items/bulk-update', {
        updates: itemIds.map((itemId) => ({
          item_id: itemId,
          status: 'delivered',
          delivered_at: now,
          receipt_id: (receiptIds[itemId] ?? '').trim() || null,
        })),
      })
      mergeUpdatedItemsIntoOrders(res.items)
      setSelectedItemIds((prev) => {
        const next = new Set(prev); itemIds.forEach((id) => next.delete(id)); return next
      })
      setBulkStatusModal(null)
    } catch (e) {
      console.error(e)
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
      setItemEdits((prev) => { const next = { ...prev }; delete next[itemId]; return next })
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
      const newItem = await api.post<Item>('/items', { order_id: order.id, status: 'purchased' })
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
        api.get<Order[]>(defaultOrdersPath()),
        api.get<Shipment[]>('/shipments'),
      ])
      setOrders(ordersData)
      setShipments(shipmentsData)
      setSelectedItemIds((prev) => { const next = new Set(prev); next.delete(itemId); return next })
      setItemEdits((prev) => { const next = { ...prev }; delete next[itemId]; return next })
      setTrackingEdits((prev) => { const next = { ...prev }; delete next[itemId]; return next })
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
  const orderTotals = (
    items: { price_paid?: string | null; price_sold?: string | null; quantity?: number }[],
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
          <h1 className="text-xl font-semibold text-ink dark:text-gray-100">Orders (Option 2)</h1>
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
            const lineItemsHeight = itemCount * 32 + 40 // approx row height + header

            return (
              <div
                key={o.id}
                className="flex gap-0 border-2 border-brand-400 dark:border-gray-400 rounded-xl overflow-hidden bg-brand-50/50 dark:bg-gray-600/80"
                style={{ minHeight: Math.max(120, lineItemsHeight) }}
              >
                {/* Left: vertical order box */}
                <div
                  className="w-[400px] shrink-0 flex flex-col gap-2 p-3 border-r-2 border-brand-400 dark:border-gray-400 bg-white/80 dark:bg-gray-700/80 overflow-hidden"
                  style={{ minHeight: lineItemsHeight }}
                >
                  <div className="flex items-center gap-1.5">
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
                      className="w-24 h-6 rounded border border-transparent bg-transparent px-1 py-0 text-sm font-medium text-brand-700 dark:text-brand-400 focus:border-brand-300 focus:bg-white dark:focus:bg-gray-700 focus:outline-none focus:ring-1 focus:ring-brand-500 disabled:opacity-60"
                    />
                    <div className="min-w-0 flex-1">
                      <SearchableCombobox<{ id: number; name: string; type: 'store' | 'account'; storeId?: number; accountId?: number }>
                        inputClassName="h-6 py-0 px-2 text-sm rounded border border-brand-200 dark:border-gray-600 w-full min-w-0 text-ink focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent disabled:opacity-60 dark:bg-gray-700"
                        options={(() => {
                          const result: { id: number; name: string; type: 'store' | 'account'; storeId?: number; accountId?: number }[] = []
                          for (const store of stores) {
                            result.push({
                              id: store.id,
                              name: store.name,
                              type: 'store',
                              storeId: store.id,
                            })
                            const accounts = accountsByStore[store.id] ?? []
                            for (const account of accounts) {
                              result.push({
                                id: account.id,
                                name: `${store.name} (${account.name})`,
                                type: 'account',
                                storeId: store.id,
                                accountId: account.id,
                              })
                            }
                          }
                          return result
                        })()}
                        value={
                          o.store_id
                            ? o.store_account_id
                              ? {
                                  id: o.store_account_id,
                                  name: `${stores.find((s) => s.id === o.store_id)?.name ?? ''} (${(accountsByStore[o.store_id] ?? []).find((a) => a.id === o.store_account_id)?.name ?? ''})`,
                                  type: 'account',
                                  storeId: o.store_id,
                                  accountId: o.store_account_id,
                                }
                              : {
                                  id: o.store_id,
                                  name: stores.find((s) => s.id === o.store_id)?.name ?? '',
                                  type: 'store',
                                  storeId: o.store_id,
                                }
                            : null
                        }
                        onChange={(item) => {
                          if (!item) return
                          updateOrder(o.id, {
                            store_id: item.storeId,
                            store_account_id: item.accountId ?? null,
                          })
                          if (item.storeId && !accountsByStore[item.storeId]) {
                            loadAccountsForStore(item.storeId)
                          }
                        }}
                        onCreate={async (name) => {
                          const s = await api.post<Store>('/stores', { name })
                          setStores((prev) => [...prev, s].sort((a, b) => a.name.localeCompare(b.name)))
                          updateOrder(o.id, { store_id: s.id, store_account_id: null })
                          loadAccountsForStore(s.id)
                          return {
                            id: s.id,
                            name: s.name,
                            type: 'store',
                            storeId: s.id,
                          }
                        }}
                        renderOption={(opt) => {
                          if (opt.type === 'account') {
                            return opt.name
                          }
                          const hasAccounts = (accountsByStore[opt.storeId!] ?? []).length > 0
                          return (
                            <div className="flex items-center justify-between gap-2 w-full">
                              <span>{opt.name}</span>
                              {!hasAccounts && (
                                <button
                                  type="button"
                                  onClick={(e) => {
                                    e.stopPropagation()
                                    const accountName = prompt('Account name:')
                                    if (accountName?.trim()) {
                                      api.post<StoreAccount>(`/stores/${opt.storeId}/accounts`, { name: accountName.trim() })
                                        .then((a) => {
                                          setAccountsByStore((prev) => ({ ...prev, [opt.storeId!]: [...(prev[opt.storeId!] ?? []), a] }))
                                        })
                                        .catch(console.error)
                                    }
                                  }}
                                  className="text-xs text-brand-600 dark:text-brand-400 hover:underline whitespace-nowrap shrink-0"
                                >
                                  + account
                                </button>
                              )}
                            </div>
                          )
                        }}
                        placeholder="Store or account…"
                      />
                    </div>
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
                              {
                                payment_method_id: firstUnused?.id ?? 0,
                                amount: amountRemaining > 0 ? amountRemaining.toFixed(2) : '',
                              },
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

                {/* Right: line items table */}
                <div className="flex-1 min-w-0 overflow-x-auto">
                  {itemCount === 0 ? (
                    <div className="p-4 text-sm text-ink-muted flex items-center gap-2">
                      No line items.
                      <button
                        type="button"
                        onClick={() => addItem(o)}
                        className="text-brand-600 dark:text-brand-400 hover:underline"
                      >
                        Add item
                      </button>
                    </div>
                  ) : (
                    <div className="rounded-lg border-2 border-brand-400 dark:border-gray-400 overflow-hidden">
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="bg-brand-100/50 dark:bg-gray-700/50 text-left">
                            <th className="w-8 py-1 px-2">
                              <input
                                type="checkbox"
                                checked={o.items!.every((i) => selectedItemIds.has(i.id)) && o.items!.length > 0}
                                onChange={() => toggleSelectAll(o.items!)}
                                className="rounded border-brand-300 text-brand-600 focus:ring-brand-500"
                              />
                            </th>
                            <th className="py-1 px-2 font-medium text-ink-muted w-12">Qty</th>
                            <th className="py-1 px-2 font-medium text-ink-muted">Description</th>
                            <th className="py-1 px-2 font-medium text-ink-muted">Tracking</th>
                            <th className="py-1 px-2 font-medium text-ink-muted w-0 whitespace-nowrap">Cost</th>
                            <th className="py-1 px-2 font-medium text-ink-muted w-0 whitespace-nowrap">Payout</th>
                            <th className="py-1 px-2 font-medium text-ink-muted">Status</th>
                            <th className="w-8 py-1 px-1.5 text-right">
                              <button
                                type="button"
                                onClick={() => addItem(o)}
                                className="p-1.5 rounded text-ink-muted hover:text-brand-600 hover:bg-brand-100 dark:hover:bg-gray-600"
                                title="Add line item"
                                aria-label="Add line item"
                              >
                                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                                </svg>
                              </button>
                            </th>
                          </tr>
                        </thead>
                        <tbody>
                          {groupOrderItemsByShipment(o).map((group) =>
                            group.items.map((item, itemIndex) => {
                              const isFirstInGroup = itemIndex === 0
                              const nextStatuses = group.items
                                .map((i) => getNextStatus(i.status))
                                .filter((s): s is ItemStatus => s != null)
                              const lowestNext =
                                nextStatuses.length > 0
                                  ? STATUS_PROGRESSION[Math.min(...nextStatuses.map((s) => STATUS_PROGRESSION.indexOf(s)))]
                                  : null
                              const advanceLabel = lowestNext ? STATUS_LABELS[lowestNext] ?? lowestNext : null
                              const isSubmitted = lowestNext === 'submitted'
                              const trackingRaw = trackingEdits[item.id] ?? getTracking(item.id)
                              const trackingInfo = trackingRaw ? getTrackingInfo(trackingRaw) : null
                              return (
                                <tr key={item.id} className="bg-transparent">
                                  <td className="py-1 px-2">
                                    <input
                                      type="checkbox"
                                      checked={selectedItemIds.has(item.id)}
                                      onChange={() => toggleItemSelect(item.id, o.items!)}
                                      className="rounded border-brand-300 text-brand-600 focus:ring-brand-500"
                                    />
                                  </td>
                                  <td className="py-1 px-2">
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
                                      className="w-12 h-5 rounded border border-brand-200 dark:border-gray-600 !bg-brand-50/50 dark:!bg-gray-600/80 px-1 py-0 text-sm text-center focus:border-brand-500 focus:outline-none disabled:opacity-60"
                                    />
                                  </td>
                                  <td className="py-1 px-2">
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
                                      className="w-full min-w-[7rem] h-5 rounded border border-brand-200 dark:border-gray-600 !bg-brand-50/50 dark:!bg-gray-600/80 px-2 py-0 text-sm focus:border-brand-500 focus:outline-none disabled:opacity-60"
                                    />
                                  </td>
                                  <td className="py-1 px-2">
                                    <div className="flex items-stretch min-w-0 h-5 rounded border border-brand-200 dark:border-gray-600 !bg-brand-50/50 dark:!bg-gray-600/80 focus-within:border-brand-500">
                                      <input
                                        type="text"
                                        value={trackingRaw}
                                        onChange={(e) => setTrackingEdits((prev) => ({ ...prev, [item.id]: e.target.value }))}
                                        onBlur={(e) => {
                                          const v = e.target.value
                                          const current = getTracking(item.id)
                                          if (v.trim() !== current.trim() || (v === '' && current !== '')) saveItemTracking(item.id, v)
                                          else setTrackingEdits((prev) => { const next = { ...prev }; delete next[item.id]; return next })
                                        }}
                                        placeholder=""
                                        disabled={savingTrackingId === item.id}
                                        className="flex-1 min-w-[5rem] h-5 border-0 rounded-l !bg-brand-50/50 dark:!bg-gray-600/80 px-2 py-0 text-sm focus:ring-0 focus:outline-none disabled:opacity-60"
                                      />
                                      {trackingInfo && (
                                        <a
                                          href={trackingInfo.url}
                                          target="_blank"
                                          rel="noopener noreferrer"
                                          className="shrink-0 flex items-center gap-1 h-5 border-l border-brand-200 dark:border-gray-600 pl-2 pr-2 py-0 text-xs font-medium text-brand-600 dark:text-brand-400 hover:bg-brand-50 dark:hover:bg-gray-700/50"
                                          title={`Track via ${trackingInfo.carrier}`}
                                        >
                                          <span className="whitespace-nowrap">{trackingInfo.carrier}</span>
                                          <svg className="w-3.5 h-3.5 shrink-0 opacity-70" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                                          </svg>
                                        </a>
                                      )}
                                    </div>
                                  </td>
                                  <td className="py-1 px-2">
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
                                      className="w-20 h-5 rounded border border-brand-200 dark:border-gray-600 !bg-brand-50/50 dark:!bg-gray-600/80 px-1.5 py-0 text-sm font-mono focus:border-brand-500 focus:outline-none disabled:opacity-60"
                                    />
                                  </td>
                                  <td className="py-1 px-2">
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
                                      className="w-20 h-5 rounded border border-brand-200 dark:border-gray-600 !bg-brand-50/50 dark:!bg-gray-600/80 px-1.5 py-0 text-sm font-mono focus:border-brand-500 focus:outline-none disabled:opacity-60"
                                    />
                                  </td>
                                  <td className="py-1 px-2">
                                    <select
                                      value={itemEdits[item.id]?.status ?? item.status}
                                      onChange={(e) => {
                                        const s = e.target.value as ItemStatus
                                        setItemEdits((prev) => ({ ...prev, [item.id]: { ...prev[item.id], status: s } }))
                                        updateItem(item.id, { status: s })
                                      }}
                                      disabled={savingItemId === item.id}
                                      className="min-w-[6.5rem] h-5 rounded border border-brand-200 dark:border-gray-600 !bg-brand-50/50 dark:!bg-gray-600/80 px-1.5 py-0 text-sm focus:border-brand-500 focus:outline-none disabled:opacity-60"
                                    >
                                      {Object.entries(STATUS_LABELS).map(([val, lbl]) => (
                                        <option key={val} value={val}>
                                          {lbl}
                                        </option>
                                      ))}
                                    </select>
                                  </td>
                                  <td className="py-1 px-1.5 text-right w-8">
                                    <div className="flex items-center justify-end gap-0.5">
                                      {isFirstInGroup && advanceLabel && (
                                        <button
                                          type="button"
                                          onClick={() => {
                                            if (isSubmitted) setSubmitShipmentModal({ group })
                                            else advanceShipmentToNextStatus(group)
                                          }}
                                          disabled={advancingGroupKey === group.key}
                                          className="p-1 rounded text-xs font-medium bg-brand-600 text-white hover:bg-brand-700 disabled:opacity-50"
                                          title={isSubmitted ? 'Mark as Submitted' : `Advance to ${advanceLabel}`}
                                          aria-label={isSubmitted ? 'Mark as Submitted' : `Advance to ${advanceLabel}`}
                                        >
                                          {advancingGroupKey === group.key ? '…' : advanceLabel}
                                        </button>
                                      )}
                                      <button
                                        type="button"
                                        onClick={() => setConfirmDeleteItemId(item.id)}
                                        disabled={deletingItemId === item.id}
                                        className="p-1 rounded text-ink-muted hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 disabled:opacity-50"
                                        title="Delete line item"
                                        aria-label="Delete line item"
                                      >
                                        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                                        </svg>
                                      </button>
                                    </div>
                                  </td>
                                </tr>
                              )
                            })
                          )}
                        </tbody>
                      </table>
                      {getSelectedIdsForOrder(o).length > 0 && (
                        <div className="mt-2 px-2 pb-2 flex flex-wrap items-center gap-3">
                          <select
                            value={getBulkActionState(o.id).action}
                            onChange={(e) => {
                              const v = e.target.value
                              if (v === 'mark_received') {
                                const ids = getSelectedIdsForOrder(o)
                                if (ids.length > 0) {
                                  setBulkStatusModal({ order: o, itemIds: ids })
                                  setBulkActionState(o.id, { action: '' })
                                }
                              } else setBulkActionState(o.id, { action: v })
                            }}
                            className="h-6 rounded-lg border border-brand-200 dark:border-gray-600 px-2 py-0 text-sm !bg-brand-50/50 dark:!bg-gray-600/80"
                          >
                            <option value="">Choose action…</option>
                            <option value="input_tracking">Input Tracking</option>
                            <option value="mark_received">Mark as Received</option>
                          </select>
                          {getBulkActionState(o.id).action === 'input_tracking' && (
                            <>
                              <input
                                type="text"
                                placeholder="Tracking number"
                                value={getBulkActionState(o.id).tracking}
                                onChange={(e) => setBulkActionState(o.id, { tracking: e.target.value })}
                                className="h-6 rounded-lg border border-brand-200 dark:border-gray-600 px-3 py-0 text-sm w-48 !bg-brand-50/50 dark:!bg-gray-600/80"
                              />
                              <input
                                type="date"
                                value={getBulkActionState(o.id).shippedAt}
                                onChange={(e) => setBulkActionState(o.id, { shippedAt: e.target.value })}
                                className="h-6 rounded-lg border border-brand-200 dark:border-gray-600 px-2 py-0 text-sm"
                              />
                              <button
                                type="button"
                                disabled={!getBulkActionState(o.id).tracking.trim() || bulkActionShippingOrderId === o.id}
                                onClick={() => applyBulkInputTracking(o)}
                                className="text-sm px-3 py-1.5 bg-brand-600 text-white rounded-lg hover:bg-brand-700 disabled:opacity-50"
                              >
                                {bulkActionShippingOrderId === o.id ? 'Applying…' : 'Apply'}
                              </button>
                            </>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>
            )
          })
        )}
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
      {bulkStatusModal && (
        <BulkStatusModal
          order={bulkStatusModal.order}
          itemIds={bulkStatusModal.itemIds}
          onApply={(receiptIds) => applyBulkReceived(bulkStatusModal.itemIds, receiptIds)}
          onClose={() => setBulkStatusModal(null)}
        />
      )}
      {submitShipmentModal && (
        <SubmitShipmentModal
          group={submitShipmentModal.group}
          onApply={(submissionId) => applySubmitShipment(submitShipmentModal.group, submissionId)}
          onClose={() => setSubmitShipmentModal(null)}
          applying={advancingGroupKey === submitShipmentModal.group.key}
        />
      )}
    </div>
  )
}

function BulkStatusModal({
  order,
  itemIds,
  onApply,
  onClose,
}: {
  order: Order
  itemIds: number[]
  onApply: (receiptIds: Record<number, string>) => Promise<void>
  onClose: () => void
}) {
  const items = (order.items ?? []).filter((i) => itemIds.includes(i.id))
  const [receiptIds, setReceiptIds] = useState<Record<number, string>>(() =>
    items.reduce<Record<number, string>>((acc, i) => {
      acc[i.id] = i.receipt_id ?? ''
      return acc
    }, {})
  )
  const [applying, setApplying] = useState(false)
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-white dark:bg-gray-800 rounded-xl shadow-xl p-6 max-w-2xl w-full mx-4 border border-brand-200/80 dark:border-gray-700 max-h-[80vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-lg font-medium text-ink mb-4">
          Mark {items.length} item{items.length !== 1 ? 's' : ''} as Received
        </h3>
        <div className="overflow-auto flex-1 min-h-0">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left border-b border-brand-200 dark:border-gray-600">
                <th className="py-2 px-2 font-medium text-ink-muted w-12">Qty</th>
                <th className="py-2 px-2 font-medium text-ink-muted">Description</th>
                <th className="py-2 px-2 font-medium text-ink-muted min-w-[10rem]">Receipt ID</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id} className="border-b border-brand-100 dark:border-gray-700 last:border-0">
                  <td className="py-2 px-2">{item.quantity ?? 1}</td>
                  <td className="py-2 px-2 text-ink">{item.description || '—'}</td>
                  <td className="py-2 px-2">
                    <input
                      type="text"
                      value={receiptIds[item.id] ?? ''}
                      onChange={(e) => setReceiptIds((prev) => ({ ...prev, [item.id]: e.target.value }))}
                      placeholder="Receipt ID (optional)"
                      className="w-full min-w-[10rem] h-8 rounded border border-brand-200 dark:border-gray-600 px-2 py-1 text-sm bg-white dark:bg-gray-700 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="flex justify-end gap-2 mt-4 pt-4 border-t border-brand-200 dark:border-gray-600">
          <button type="button" onClick={onClose} className="px-3 py-1.5 border border-brand-300 dark:border-gray-600 rounded-lg text-sm text-ink hover:bg-brand-50 dark:hover:bg-gray-700">
            Cancel
          </button>
          <button
            type="button"
            onClick={async () => {
              setApplying(true)
              try {
                await onApply(receiptIds)
              } finally {
                setApplying(false)
              }
            }}
            disabled={applying}
            className="px-3 py-1.5 bg-brand-600 text-white rounded-lg text-sm hover:bg-brand-700 disabled:opacity-50"
          >
            {applying ? 'Applying…' : 'Apply'}
          </button>
        </div>
      </div>
    </div>
  )
}

function SubmitShipmentModal({
  group,
  onApply,
  onClose,
  applying,
}: {
  group: { key: string; label: string; trackingNumber: string | null; items: Item[] }
  onApply: (submissionId: string) => Promise<void>
  onClose: () => void
  applying: boolean
}) {
  const [submissionId, setSubmissionId] = useState('')
  const itemCount = group.items.length
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-white dark:bg-gray-800 rounded-xl shadow-xl p-6 max-w-md w-full mx-4 border border-brand-200/80 dark:border-gray-700"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-lg font-medium text-ink mb-2">Mark shipment as Submitted</h3>
        <p className="text-sm text-ink-muted mb-4">
          {itemCount} item{itemCount !== 1 ? 's' : ''} in this shipment
          {group.trackingNumber && <span className="ml-1 font-mono text-ink">({group.trackingNumber})</span>}
        </p>
        <label className="block text-sm font-medium text-ink mb-2">Submission ID (optional)</label>
        <input
          type="text"
          value={submissionId}
          onChange={(e) => setSubmissionId(e.target.value)}
          placeholder="ID the buying group assigns to this submission"
          className="w-full h-10 rounded-lg border border-brand-200 dark:border-gray-600 px-3 py-2 text-sm bg-white dark:bg-gray-700 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500 mb-6"
        />
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className="px-3 py-1.5 border border-brand-300 dark:border-gray-600 rounded-lg text-sm text-ink hover:bg-brand-50 dark:hover:bg-gray-700">
            Cancel
          </button>
          <button
            type="button"
            onClick={async () => await onApply(submissionId)}
            disabled={applying}
            className="px-3 py-1.5 bg-brand-600 text-white rounded-lg text-sm hover:bg-brand-700 disabled:opacity-50"
          >
            {applying ? 'Applying…' : 'Apply'}
          </button>
        </div>
      </div>
    </div>
  )
}
