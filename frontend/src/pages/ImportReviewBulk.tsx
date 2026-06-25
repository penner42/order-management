import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import type { BuyingGroup, PaymentMethod, Store } from '../api/types'
import { autoMatchBuyingGroupIdForImport } from '../utils/buyingGroupMatch'
import { getDefaultItemPayout, getDefaultOrderTotal } from '../utils/importDefaults'

type NormalizedPayload = any

interface NormalizedItem {
  logicalItemId?: string | null
  externalSku?: string | null
  name?: string | null
  productUrl?: string | null
  imageUrl?: string | null
  variants?: { name: string | null; value: string | null }[]
  quantities?: { ordered?: number | null }
  pricing?: {
    unitPrice?: number | null
    linePrice?: number | null
    lineTotal?: number | null
    strikethroughPrice?: number | null
  }
  status?: { rawStatusCode?: string | null; normalizedStatus?: string | null }
  shipments?: { shipmentId?: string | null; quantity?: number | null }[]
  returnability?: { isReturnable?: boolean; returnEligibilityMessage?: string | null }
}

interface NormalizedShipment {
  shipmentId?: string | null
  trackingNumber?: string | null
  trackingUrl?: string | null
  deliveryDate?: string | null
  fulfillmentType?: string | null
  status?: { rawStatusType?: string | null; message?: string | null }
}

interface OrderDiff {
  is_existing_order: boolean
  has_changes?: boolean
  order?: Record<string, { current: string | null; incoming: string | null }>
  items?: {
    matched: Array<{
      name: string
      current: { quantity: number; price_paid: number | null; statuses: string[] }
      incoming: { quantity: number; unit_price: number | null; line_total?: number | null }
      changes: string[]
      detailed_changes?: { field: string; before: unknown; after: unknown }[]
    }>
    added: Array<{ name: string; quantity: number; unit_price: number | null; line_total?: number | null }>
    unmatched_existing: Array<{
      description: string
      quantity: number
      price_paid: number | null
      statuses: string[]
    }>
  }
  shipments?: {
    matched: Array<{
      tracking_number: string
      current: { delivered_at: string | null }
      incoming: {
        delivery_date: string | null
        status_message: string | null
        status_code?: string | null
      }
      changes: string[]
      detailed_changes?: { field: string; before: unknown; after: unknown }[]
    }>
    added: Array<{
      tracking_number: string
      delivery_date: string | null
      status_message: string | null
      status_code?: string | null
    }>
    unmatched_existing: Array<{
      tracking_number: string
      delivered_at: string | null
    }>
  }
}

interface ItemDiffInfo {
  status: 'new' | 'unchanged' | 'changed'
  changes: string[]
  currentQuantity?: number
  currentPrice?: number | null
  detailedChanges?: { field: string; before: unknown; after: unknown }[]
}

function normalizeTrackingForDisplay(
  storeName: string,
  externalOrderId: string | undefined | null,
  trackingNumber: string | undefined | null
): string | null {
  if (!trackingNumber) return null
  const storeLower = (storeName || '').trim().toLowerCase()
  const compact = trackingNumber.replace(/\s+/g, '')
  if (storeLower === 'walmart' && compact.length === 20 && compact.startsWith('555') && /^\d+$/.test(compact)) {
    return externalOrderId && externalOrderId.trim() ? externalOrderId.trim() : trackingNumber
  }
  return trackingNumber
}

function fmtMoney(v: number | null | undefined): string {
  if (v == null) return '—'
  return `$${v.toFixed(2)}`
}

function coerceNumber(v: unknown): number | null {
  if (v == null) return null
  if (typeof v === 'number' && Number.isFinite(v)) return v
  if (typeof v === 'string') {
    const trimmed = v.trim()
    if (!trimmed) return null
    const n = Number(trimmed.replace(/[^0-9.-]/g, ''))
    return Number.isFinite(n) ? n : null
  }
  return null
}

function getFirstNumber(obj: unknown, paths: string[]): number | null {
  if (!obj || typeof obj !== 'object') return null
  for (const path of paths) {
    const parts = path.split('.').filter(Boolean)
    let cur: any = obj as any
    let ok = true
    for (const part of parts) {
      if (cur && typeof cur === 'object' && part in cur) {
        cur = cur[part]
      } else {
        ok = false
        break
      }
    }
    if (!ok) continue
    const n = coerceNumber(cur)
    if (n != null) return n
  }
  return null
}

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    })
  } catch {
    return iso
  }
}

function extractLast4FromOrderPaymentMethods(orderPayload: NormalizedPayload): string | null {
  const externalPaymentMethods = Array.isArray((orderPayload as any)?.paymentMethods)
    ? ((orderPayload as any).paymentMethods as any[])
    : []
  if (externalPaymentMethods.length === 0) return null

  for (const pm of externalPaymentMethods) {
    if (pm && typeof pm === 'object') {
      const directLast4 = typeof pm.last4 === 'string' ? pm.last4.trim() : ''
      if (/^\d{4}$/.test(directLast4)) return directLast4

      const description = typeof pm.description === 'string' ? pm.description : ''
      const descMatch = /(\d{4})\b/.exec(description)
      if (descMatch) return descMatch[1]
    }
  }

  return null
}

function coerceStatusText(v: unknown): string {
  if (v == null) return ''
  if (typeof v === 'string') return v
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  try {
    if (typeof v === 'object') {
      const maybe = (v as any).status ?? (v as any).state ?? (v as any).code ?? (v as any).type
      if (typeof maybe === 'string') return maybe
      if (typeof maybe === 'number' || typeof maybe === 'boolean') return String(maybe)
    }
  } catch {
    // ignore
  }
  return ''
}

function isCanceledOrderPayload(p: NormalizedPayload): boolean {
  const externalOrder = p?.externalOrder ?? null
  const candidates: unknown[] = [
    p?.status,
    p?.orderStatus,
    externalOrder?.status,
    externalOrder?.orderStatus,
    externalOrder?.fulfillmentStatus,
    externalOrder?.state,
    externalOrder?.statusType,
  ]

  for (const c of candidates) {
    const s = coerceStatusText(c).trim().toLowerCase()
    if (!s) continue
    if (s.includes('canceled') || s.includes('cancelled')) return true
  }

  try {
    const items: any[] = Array.isArray(p?.items) ? p.items : []
    for (const item of items) {
      const s = coerceStatusText(item?.status ?? item?.status?.normalizedStatus ?? item?.status?.rawStatusCode)
        .trim()
        .toLowerCase()
      if (s.includes('canceled') || s.includes('cancelled')) return true
    }
  } catch {
    // ignore
  }

  return false
}

export default function ImportReviewBulk() {
  const [searchParams] = useSearchParams()
  const token = searchParams.get('token') || ''
  const [payloads, setPayloads] = useState<NormalizedPayload[]>([])
  const [stores, setStores] = useState<Store[]>([])
  const [buyingGroups, setBuyingGroups] = useState<BuyingGroup[]>([])
  const [paymentMethods, setPaymentMethods] = useState<PaymentMethod[]>([])
  const [diffs, setDiffs] = useState<Record<number, OrderDiff | null>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [applyingByIndex, setApplyingByIndex] = useState<Record<number, boolean>>({})
  const [appliedByIndex, setAppliedByIndex] = useState<Record<number, boolean>>({})
  const [applyErrorByIndex, setApplyErrorByIndex] = useState<Record<number, string | null>>({})
  const [collapsedByIndex, setCollapsedByIndex] = useState<Record<number, boolean>>({})
  const [selectedBuyingGroupIdByIndex, setSelectedBuyingGroupIdByIndex] = useState<Record<number, number | null>>({})
  const [selectedPaymentMethodIdByIndex, setSelectedPaymentMethodIdByIndex] = useState<Record<number, number | null>>({})

  useEffect(() => {
    if (!token) {
      setLoading(false)
      setPayloads([])
      return
    }
    setLoading(true)
    setError(null)
    api
      .get<{ orders: NormalizedPayload[] }>(`/integrations/stores/orders/bulk-session/${encodeURIComponent(token)}`)
      .then((res) => {
        const orders = Array.isArray(res.orders) ? res.orders : []
        setPayloads(orders)
        if (orders.length === 0) {
          setDiffs({})
          return
        }
        return Promise.all([
          api.get<Store[]>('/stores'),
          api.get<BuyingGroup[]>('/buying-groups'),
          api.get<PaymentMethod[]>('/payment-methods'),
          api.post<{ diffs: OrderDiff[] }>('/integrations/stores/orders/diff-bulk', { orders }),
        ]).then(async ([storesList, groups, methods, diffRes]) => {
          setStores(storesList)
          setBuyingGroups(groups)
          setPaymentMethods(methods)
          const nextDiffs: Record<number, OrderDiff | null> = {}
          const diffsArr = Array.isArray(diffRes.diffs) ? diffRes.diffs : []
          for (let i = 0; i < orders.length; i++) {
            nextDiffs[i] = diffsArr[i] ?? null
          }
          setDiffs(nextDiffs)
        })
      })
      .catch((err: unknown) => {
        console.error(err)
        setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => setLoading(false))
  }, [token])

  const flattenedPaymentMethods = useMemo(
    () =>
      paymentMethods.flatMap((pm) => {
        const rows = [{ id: pm.id, label: pm.label }]
        if (pm.sub_methods && pm.sub_methods.length > 0) {
          for (const sub of pm.sub_methods) {
            rows.push({
              id: sub.id,
              label: `${pm.label} — ${sub.label}`,
            })
          }
        }
        return rows
      }),
    [paymentMethods]
  )

  function setPayloadItemEdits(
    orderIndex: number,
    itemIndex: number,
    next: { qty?: number | null; description?: string | null; unitCost?: number | null }
  ) {
    setPayloads((prev) => {
      const out = [...prev]
      const order = out[orderIndex]
      if (!order) return prev
      const items = Array.isArray((order as any).items) ? ([...(order as any).items] as any[]) : []
      const item = items[itemIndex]
      if (!item || typeof item !== 'object') return prev
      const updatedItem: any = { ...item }
      if ('qty' in next) {
        const quantities =
          updatedItem.quantities && typeof updatedItem.quantities === 'object'
            ? { ...updatedItem.quantities }
            : {}
        quantities.ordered = next.qty == null ? null : next.qty
        updatedItem.quantities = quantities
      }
      if ('description' in next) {
        updatedItem.name = next.description == null ? null : next.description
      }
      if ('unitCost' in next) {
        const pricing =
          updatedItem.pricing && typeof updatedItem.pricing === 'object'
            ? { ...updatedItem.pricing }
            : {}
        pricing.unitPrice = next.unitCost == null ? null : next.unitCost
        updatedItem.pricing = pricing
      }
      items[itemIndex] = updatedItem
      out[orderIndex] = { ...(order as any), items }
      return out
    })
  }

  useEffect(() => {
    if (!payloads || payloads.length === 0) return
    setCollapsedByIndex((prev) => {
      const next = { ...prev }
      for (let i = 0; i < payloads.length; i++) {
        if (typeof next[i] === 'boolean') continue
        next[i] = isCanceledOrderPayload(payloads[i]) === true
      }
      return next
    })
  }, [payloads])

  useEffect(() => {
    if (!payloads || payloads.length === 0 || flattenedPaymentMethods.length === 0) return

    setSelectedPaymentMethodIdByIndex((prev) => {
      let changed = false
      const next = { ...prev }

      for (let i = 0; i < payloads.length; i++) {
        if (next[i] != null) continue

        const payload = payloads[i]
        const last4 = extractLast4FromOrderPaymentMethods(payload)
        if (!last4) continue

        const match = flattenedPaymentMethods.find((m) => {
          const labelMatch = /(\d{4})\b/.exec(m.label)
          return labelMatch && labelMatch[1] === last4
        })
        if (!match) continue

        next[i] = match.id
        changed = true
      }

      return changed ? next : prev
    })
  }, [payloads, flattenedPaymentMethods])

  useEffect(() => {
    if (!payloads || payloads.length === 0 || buyingGroups.length === 0) return

    setSelectedBuyingGroupIdByIndex((prev) => {
      let changed = false
      const next = { ...prev }

      for (let i = 0; i < payloads.length; i++) {
        if (next[i] != null) continue
        const matchId = autoMatchBuyingGroupIdForImport(payloads[i], buyingGroups)
        if (matchId == null) continue
        next[i] = matchId
        changed = true
      }

      return changed ? next : prev
    })
  }, [payloads, buyingGroups])

  if (!token || payloads.length === 0) {
    return (
      <div className="max-w-2xl mx-auto">
        <h1 className="text-xl font-semibold text-ink dark:text-gray-100 mb-2">
          Bulk import review
        </h1>
        <p className="text-ink-muted dark:text-gray-400 mb-4">
          No orders were attached to this link. Use the browser extension bulk import from the Walmart orders page.
        </p>
        <Link
          to="/"
          className="text-brand-600 dark:text-brand-400 hover:underline text-sm"
        >
          Back to Orders
        </Link>
      </div>
    )
  }

  if (loading) {
    return (
      <div>
        <p className="text-ink-muted dark:text-gray-400">Loading bulk review…</p>
      </div>
    )
  }

  function findStoreForPayload(p: NormalizedPayload): Store | null {
    const storeName: string = p.store || ''
    const name = storeName.trim().toLowerCase()
    return stores.find((s) => s.name.toLowerCase() === name) ?? null
  }

  async function handleApplySingle(index: number) {
    const payload = payloads[index]
    if (!payload || applyingByIndex[index] || appliedByIndex[index]) return
    setApplyingByIndex((prev) => ({ ...prev, [index]: true }))
    setApplyErrorByIndex((prev) => ({ ...prev, [index]: null }))
    try {
      const buyingGroupId = selectedBuyingGroupIdByIndex[index] ?? null
      const paymentMethodId = selectedPaymentMethodIdByIndex[index] ?? null
      const items = Array.isArray((payload as any).items) ? ((payload as any).items as NormalizedItem[]) : []
      const itemPayouts = items.map((item) => getDefaultItemPayout(item))
      const orderTotal = getDefaultOrderTotal(payload)
      await api.post('/integrations/stores/orders/apply', {
        payload,
        store_account_id: null,
        buying_group_id: buyingGroupId,
        item_payouts: itemPayouts,
        payment_methods:
          paymentMethodId != null
            ? [{ payment_method_id: paymentMethodId, amount: orderTotal }]
            : null,
      })
      setAppliedByIndex((prev) => ({ ...prev, [index]: true }))
    } catch (err) {
      console.error(err)
      setApplyErrorByIndex((prev) => ({
        ...prev,
        [index]: err instanceof Error ? err.message : String(err),
      }))
    } finally {
      setApplyingByIndex((prev) => ({ ...prev, [index]: false }))
    }
  }

  const indexedPayloads = payloads.map((p, index) => ({
    payload: p,
    index,
    isCanceled: isCanceledOrderPayload(p),
  }))

  const sortedPayloads = [...indexedPayloads].sort((a, b) => {
    if (a.isCanceled === b.isCanceled) return 0
    return a.isCanceled ? 1 : -1
  })

  const firstCanceledIndex = sortedPayloads.findIndex((x) => x.isCanceled)

  return (
    <div>
      <div className="flex items-center justify-between gap-4 mb-6">
        <div>
          <h1 className="text-xl font-semibold text-ink dark:text-gray-100">
            Bulk import review ({payloads.length} orders)
          </h1>
          <p className="text-sm text-ink-muted dark:text-gray-400 mt-1">
            Review each order below and apply them individually.
          </p>
        </div>
        <Link
          to="/"
          className="text-sm text-brand-600 dark:text-brand-400 hover:underline whitespace-nowrap"
        >
          Back to Orders
        </Link>
      </div>

      {error && (
        <p className="mb-3 text-sm text-red-600 dark:text-red-400">{error}</p>
      )}

      <div className="space-y-6">
        {sortedPayloads.map((entry, renderIdx) => {
          const p = entry.payload
          const idx = entry.index
          const isCanceled = entry.isCanceled
          const diff = diffs[idx]
          const externalOrder = p.externalOrder || {}
          const storeName: string = p.store || ''
          const orderId: string = externalOrder.id || ''
          const isExisting = diff && diff.is_existing_order === true
          const store = findStoreForPayload(p)
          const itemChangesCount =
            diff && diff.items
              ? (diff.items.matched.filter((m) => m.changes.length > 0).length || 0) +
                (diff.items.added.length || 0)
              : 0
          const shipmentChangesCount =
            diff && diff.shipments
              ? (diff.shipments.matched.filter((m) => m.changes.length > 0).length || 0) +
                (diff.shipments.added.length || 0)
              : 0
          const applying = applyingByIndex[idx] === true
          const applied = appliedByIndex[idx] === true
          const hasTrackingUpdates = shipmentChangesCount > 0
          const hasNoChanges = isExisting && !hasTrackingUpdates
          const applyError = applyErrorByIndex[idx] || null
          const collapsed = collapsedByIndex[idx] === true

          const items: NormalizedItem[] = p.items ?? []
          const shipments: NormalizedShipment[] = p.shipments ?? []
          const shipmentsById = new Map((shipments as NormalizedShipment[]).map((s) => [s.shipmentId, s]))
          const customer = p.customer
          const address = p.shippingAddress

          const showCanceledSeparator = firstCanceledIndex >= 0 && renderIdx === firstCanceledIndex

          const itemDiffMap = new Map<string, ItemDiffInfo>()
          if (isExisting && diff?.items) {
            for (const m of diff.items.matched) {
              itemDiffMap.set(m.name, {
                status: m.changes.length > 0 ? 'changed' : 'unchanged',
                changes: m.changes,
                currentQuantity: m.current.quantity,
                currentPrice: m.current.price_paid,
                detailedChanges: m.detailed_changes,
              })
            }
            for (const a of diff.items.added) {
              itemDiffMap.set(a.name, { status: 'new', changes: [] })
            }
          }

          const shipmentDiffMap = new Map<
            string,
            {
              status: 'new' | 'unchanged' | 'changed'
              changes: string[]
              detailedChanges?: { field: string; before: unknown; after: unknown }[]
            }
          >()
          if (isExisting && diff?.shipments) {
            for (const m of diff.shipments.matched) {
              shipmentDiffMap.set(m.tracking_number, {
                status: m.changes.length > 0 ? 'changed' : 'unchanged',
                changes: m.changes,
                detailedChanges: m.detailed_changes,
              })
            }
            for (const a of diff.shipments.added) {
              if (a.tracking_number) {
                shipmentDiffMap.set(a.tracking_number, { status: 'new', changes: [] })
              }
            }
          }

          return (
            <>
              {showCanceledSeparator && (
                <div className="pt-2 border-t border-dashed border-brand-200 dark:border-gray-700 text-xs text-ink-muted dark:text-gray-400">
                  Canceled orders
                </div>
              )}
              <div
                key={idx}
                className="border border-brand-300 dark:border-gray-700 rounded-xl bg-white/80 dark:bg-gray-800/80 p-4"
              >
              <div className="flex flex-col gap-4">
                <div className="flex items-start justify-between gap-4">
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <div className="text-sm font-semibold text-ink dark:text-gray-100">
                        {storeName} #{orderId}
                      </div>
                      {isCanceled && (
                        <span className="inline-flex items-center rounded-full bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300 px-2 py-0.5 text-[11px]">
                          Canceled
                        </span>
                      )}
                      {store && (
                        <span className="inline-flex items-center rounded-full bg-brand-100 text-brand-800 dark:bg-gray-700 dark:text-gray-100 px-2 py-0.5 text-[11px]">
                          {store.name}
                        </span>
                      )}
                    </div>
                    <div className="text-xs text-ink-muted dark:text-gray-400">
                      {isExisting
                        ? hasTrackingUpdates
                          ? 'Existing order — tracking updates available'
                          : 'Existing order — no tracking updates'
                        : 'New order'}
                    </div>
                    {(itemChangesCount > 0 || shipmentChangesCount > 0) && (
                      <div className="flex flex-wrap gap-3 text-[11px] mt-1">
                        {itemChangesCount > 0 && (
                          <span className="text-amber-700 dark:text-amber-300">
                            {itemChangesCount} item change(s)
                          </span>
                        )}
                        {shipmentChangesCount > 0 && (
                          <span className="text-amber-700 dark:text-amber-300">
                            {shipmentChangesCount} shipment change(s)
                          </span>
                        )}
                      </div>
                    )}
                    {applyError && (
                      <div className="text-xs text-red-600 dark:text-red-400 mt-1">
                        {applyError}
                      </div>
                    )}
                  </div>
                  <div className="flex flex-col items-end gap-2">
                    <button
                      type="button"
                      onClick={() =>
                        setCollapsedByIndex((prev) => ({ ...prev, [idx]: !(prev[idx] === true) }))
                      }
                      className="text-xs text-ink-muted dark:text-gray-300 hover:underline"
                      aria-label={collapsed ? 'Expand order details' : 'Collapse order details'}
                    >
                      {collapsed ? 'Expand' : 'Collapse'}
                    </button>
                    <button
                      type="button"
                      onClick={() => handleApplySingle(idx)}
                      disabled={Boolean(applying || applied || hasNoChanges)}
                      className={`inline-flex items-center justify-center px-3 py-1.5 text-sm font-medium rounded-md ${
                        applied
                          ? 'bg-gray-300 text-gray-600 dark:bg-gray-700 dark:text-gray-300 cursor-default'
                          : hasNoChanges
                            ? 'bg-gray-200 text-gray-500 dark:bg-gray-800 dark:text-gray-400 cursor-not-allowed'
                            : 'bg-brand-600 text-white hover:bg-brand-700 disabled:opacity-60 disabled:cursor-not-allowed'
                      }`}
                    >
                      {applying
                        ? 'Saving…'
                        : applied
                          ? isExisting
                            ? 'Tracking updated'
                            : 'Import Complete'
                          : isExisting
                            ? 'Update tracking'
                            : 'Import order'}
                    </button>
                  </div>
                </div>

                {!collapsed && (
                  <div className="grid grid-cols-1 md:grid-cols-[320px,minmax(0,1fr)] gap-4">
                  {/* Left: customer & shipping info */}
                  <div className="flex flex-col gap-2.5 rounded-lg border border-brand-200/70 dark:border-gray-700 bg-brand-50/40 dark:bg-gray-900/40 px-3 py-2.5">
                    {buyingGroups.length > 0 && (
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-ink-muted shrink-0 w-16">Buying group</span>
                        <select
                          value={selectedBuyingGroupIdByIndex[idx] ?? ''}
                          onChange={(e) =>
                            setSelectedBuyingGroupIdByIndex((prev) => ({
                              ...prev,
                              [idx]: e.target.value ? Number(e.target.value) : null,
                            }))
                          }
                          className="text-sm rounded border border-brand-200 dark:border-gray-600 dark:bg-gray-800 px-2 py-1 text-ink dark:text-gray-200 min-w-0"
                        >
                          <option value="">None</option>
                          {buyingGroups.map((g) => (
                            <option key={g.id} value={g.id}>
                              {g.name}
                            </option>
                          ))}
                        </select>
                      </div>
                    )}
                    {flattenedPaymentMethods.length > 0 && (
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-ink-muted shrink-0 w-16">Payment</span>
                        <select
                          value={selectedPaymentMethodIdByIndex[idx] ?? ''}
                          onChange={(e) =>
                            setSelectedPaymentMethodIdByIndex((prev) => ({
                              ...prev,
                              [idx]: e.target.value ? Number(e.target.value) : null,
                            }))
                          }
                          className="text-sm rounded border border-brand-200 dark:border-gray-600 dark:bg-gray-800 px-2 py-1 text-ink dark:text-gray-200 min-w-0"
                        >
                          <option value="">None</option>
                          {flattenedPaymentMethods.map((m) => (
                            <option key={m.id} value={m.id}>
                              {m.label}
                            </option>
                          ))}
                        </select>
                      </div>
                    )}
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-ink-muted shrink-0 w-16">Date</span>
                      <span className="text-sm font-mono text-ink dark:text-gray-200">
                        {fmtDate(externalOrder.orderDate)}
                      </span>
                    </div>
                    {customer && (customer.firstName || customer.lastName || customer.email) && (
                      <div className="flex items-start gap-2">
                        <span className="text-xs text-ink-muted shrink-0 w-16 pt-0.5">
                          Customer
                        </span>
                        <div className="text-sm text-ink dark:text-gray-200">
                          {[customer.firstName, customer.lastName]
                            .filter(Boolean)
                            .join(' ') || null}
                          {customer.email && (
                            <span className="block text-xs text-ink-muted dark:text-gray-400">
                              {customer.email}
                            </span>
                          )}
                        </div>
                      </div>
                    )}
                    {address && (address.addressLine1 || address.city) && (
                      <div className="flex items-start gap-2">
                        <span className="text-xs text-ink-muted shrink-0 w-16 pt-0.5">
                          Ship to
                        </span>
                        <div className="text-sm text-ink dark:text-gray-200 leading-snug">
                          {address.fullName && (
                            <span className="block font-medium">{address.fullName}</span>
                          )}
                          {address.addressLine1 && (
                            <span className="block">{address.addressLine1}</span>
                          )}
                          {address.addressLine2 && (
                            <span className="block">{address.addressLine2}</span>
                          )}
                          {(address.city || address.state || address.postalCode) && (
                            <span className="block">
                              {[address.city, address.state]
                                .filter(Boolean)
                                .join(', ')}
                              {address.postalCode ? ` ${address.postalCode}` : ''}
                            </span>
                          )}
                        </div>
                      </div>
                    )}
                    {externalOrder.url && (
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-ink-muted shrink-0 w-16">Link</span>
                        <a
                          href={externalOrder.url}
                          target="_blank"
                          rel="noreferrer"
                          className="text-sm text-brand-600 dark:text-brand-400 hover:underline truncate"
                          aria-label="Open order on store site"
                        >
                          <svg
                            className="w-4 h-4 inline-block"
                            fill="none"
                            stroke="currentColor"
                            viewBox="0 0 24 24"
                          >
                            <path
                              strokeLinecap="round"
                              strokeLinejoin="round"
                              strokeWidth={2}
                              d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"
                            />
                          </svg>
                        </a>
                      </div>
                    )}
                  </div>

                  {/* Right: items table & shipments summary */}
                  <div className="flex flex-col gap-3 min-w-0">
                    <div className="overflow-x-auto rounded-lg border border-brand-200/70 dark:border-gray-700 bg-white/70 dark:bg-gray-900/40">
                      <table className="w-full table-fixed text-xs md:text-sm">
                        <colgroup>
                          <col className="w-16" />
                          <col />
                          <col className="w-44" />
                          <col className="w-24" />
                          <col className="w-24" />
                          <col className="w-24" />
                          <col className="w-24" />
                          <col className="w-24" />
                        </colgroup>
                        <thead>
                          <tr className="bg-brand-100/50 dark:bg-gray-800/70 text-left border-b border-brand-200 dark:border-gray-700">
                            <th className="py-1.5 px-2 font-medium text-ink-muted w-12">
                              Qty
                            </th>
                            <th className="py-1.5 px-2 font-medium text-ink-muted">
                              Description
                            </th>
                            <th className="py-1.5 px-2 font-medium text-ink-muted">
                              Tracking
                            </th>
                            <th className="py-1.5 px-2 font-medium text-ink-muted w-0 whitespace-nowrap text-right">
                              Unit cost
                            </th>
                            <th className="py-1.5 px-2 font-medium text-ink-muted w-0 whitespace-nowrap text-right">
                              Subtotal
                            </th>
                            <th className="py-1.5 px-2 font-medium text-ink-muted w-0 whitespace-nowrap text-right">
                              Shipping
                            </th>
                            <th className="py-1.5 px-2 font-medium text-ink-muted w-0 whitespace-nowrap text-right">
                              Tax
                            </th>
                            <th className="py-1.5 px-2 font-medium text-ink-muted w-0 whitespace-nowrap text-right">
                              Line total
                            </th>
                          </tr>
                        </thead>
                        <tbody>
                          {items.length === 0 ? (
                            <tr>
                              <td
                                colSpan={8}
                                className="py-3 px-2 text-center text-ink-muted dark:text-gray-400"
                              >
                                No items in this order.
                              </td>
                            </tr>
                          ) : (
                            items.map((item, itemIdx) => {
                              const qty = item.quantities?.ordered ?? 1
                              const unitCost = item.pricing?.unitPrice ?? null
                              const subtotal =
                                unitCost != null ? unitCost * (typeof qty === 'number' ? qty : 1) : null

                              const itemShipping =
                                getFirstNumber(item as any, [
                                  'pricing.shipping',
                                  'pricing.shippingPrice',
                                  'pricing.shippingTotal',
                                  'shipping',
                                  'shippingTotal',
                                ]) ?? null

                              const itemTax =
                                getFirstNumber(item as any, [
                                  'pricing.tax',
                                  'pricing.salesTax',
                                  'pricing.taxTotal',
                                  'sales_tax',
                                  'salesTax',
                                  'tax',
                                  'taxTotal',
                                ]) ?? null

                              const lineTotal =
                                item.pricing?.lineTotal ??
                                item.pricing?.linePrice ??
                                (unitCost != null ? unitCost * (qty ?? 1) : null)
                              const shipSlices = item.shipments ?? []
                              const firstShipment = shipSlices[0]
                                ? shipmentsById.get(shipSlices[0].shipmentId ?? '')
                                : undefined
                              const trackingNumberRaw = firstShipment?.trackingNumber ?? null
                              const trackingNumber = normalizeTrackingForDisplay(
                                storeName,
                                orderId,
                                trackingNumberRaw
                              )
                              const trackingUrl = firstShipment?.trackingUrl ?? null
                              const diffInfo = isExisting
                                ? itemDiffMap.get((item.name || '').trim())
                                : undefined

                              return (
                                <tr
                                  key={item.logicalItemId ?? itemIdx}
                                  className={`${
                                    itemIdx % 2 === 0
                                      ? 'bg-white/60 dark:bg-gray-900/40'
                                      : 'bg-brand-50/30 dark:bg-gray-800/40'
                                  }${
                                    diffInfo?.status === 'new'
                                      ? ' border-l-[3px] border-l-emerald-400 dark:border-l-emerald-500'
                                      : diffInfo?.status === 'changed'
                                        ? ' border-l-[3px] border-l-amber-400 dark:border-l-amber-500'
                                        : ''
                                  }`}
                                >
                                  <td className="py-1.5 px-2 text-center font-mono text-xs md:text-sm">
                                    <input
                                      type="number"
                                      inputMode="numeric"
                                      min={0}
                                      value={typeof qty === 'number' && Number.isFinite(qty) ? String(qty) : ''}
                                      onChange={(e) => {
                                        const raw = e.target.value
                                        const n = raw === '' ? null : Number(raw)
                                        setPayloadItemEdits(idx, itemIdx, {
                                          qty: n != null && Number.isFinite(n) ? n : null,
                                        })
                                      }}
                                      className="w-12 text-center font-mono text-xs md:text-sm rounded border border-brand-200 dark:border-gray-600 dark:bg-gray-800 px-1 py-0.5 text-ink dark:text-gray-200 tabular-nums"
                                      aria-label={`Quantity for ${(item.name || '').slice(0, 30)}`}
                                    />
                                    {diffInfo?.changes.includes('quantity') && (
                                      <span className="block text-[10px] text-amber-600 dark:text-amber-400">
                                        was {diffInfo.currentQuantity}
                                      </span>
                                    )}
                                    {diffInfo?.detailedChanges &&
                                      diffInfo.detailedChanges.some((c) => c.field === 'quantity') &&
                                      typeof diffInfo.currentQuantity === 'number' &&
                                      typeof qty === 'number' && (
                                        <span className="block text-[10px] text-amber-700 dark:text-amber-300">
                                          {diffInfo.currentQuantity} → {qty}
                                        </span>
                                      )}
                                  </td>
                                  <td className="py-1.5 px-2">
                                    <div className="flex items-center gap-2 min-w-0">
                                      {item.imageUrl && (
                                        <img
                                          src={item.imageUrl}
                                          alt=""
                                          className="w-7 h-7 md:w-8 md:h-8 rounded object-cover shrink-0 border border-brand-200 dark:border-gray-600"
                                        />
                                      )}
                                      <div className="min-w-0">
                                        <div className="flex items-center gap-1.5 min-w-0">
                                          <input
                                            type="text"
                                            value={item.name ?? ''}
                                            onChange={(e) => setPayloadItemEdits(idx, itemIdx, { description: e.target.value })}
                                            className="w-full min-w-0 text-xs md:text-sm text-ink dark:text-gray-200 rounded border border-brand-200 dark:border-gray-600 dark:bg-gray-800 px-2 py-1"
                                            aria-label={`Description for item ${itemIdx + 1}`}
                                          />
                                          {diffInfo?.status === 'new' && (
                                            <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[9px] md:text-[10px] font-semibold uppercase bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-400 shrink-0">
                                              New
                                            </span>
                                          )}
                                        </div>
                                        {item.variants &&
                                          item.variants.length > 0 && (
                                            <span className="block text-[10px] md:text-xs text-ink-muted dark:text-gray-400 truncate">
                                              {item.variants
                                                .filter((v) => v.name && v.value)
                                                .map((v) => `${v.name}: ${v.value}`)
                                                .join(', ')}
                                            </span>
                                          )}
                                      </div>
                                    </div>
                                  </td>
                                  <td className="py-1.5 px-2">
                                {trackingNumber ? (
                                      trackingUrl ? (
                                        <a
                                          href={trackingUrl}
                                          target="_blank"
                                          rel="noreferrer"
                                          className="text-xs md:text-sm text-brand-600 dark:text-brand-400 hover:underline font-mono"
                                          aria-label="Track shipment"
                                        >
                                          {trackingNumber}
                                        </a>
                                      ) : (
                                        <span className="text-xs md:text-sm font-mono text-ink dark:text-gray-200">
                                          {trackingNumber}
                                        </span>
                                      )
                                    ) : (
                                      <span className="text-[10px] md:text-xs text-ink-muted dark:text-gray-500">
                                        —
                                      </span>
                                    )}
                                  </td>
                                  <td className="py-1.5 px-2 text-right font-mono text-xs md:text-sm tabular-nums whitespace-nowrap">
                                    <input
                                      type="text"
                                      inputMode="decimal"
                                      value={unitCost == null ? '' : String(unitCost)}
                                      onChange={(e) => setPayloadItemEdits(idx, itemIdx, { unitCost: coerceNumber(e.target.value) })}
                                      className="w-20 text-right font-mono text-xs md:text-sm rounded border border-brand-200 dark:border-gray-600 dark:bg-gray-800 px-2 py-0.5 text-ink dark:text-gray-200 tabular-nums"
                                      aria-label={`Unit cost for ${(item.name || '').slice(0, 30)}`}
                                    />
                                    {diffInfo?.changes.includes('price') && (
                                      <span className="block text-[10px] text-amber-600 dark:text-amber-400">
                                        was {fmtMoney(diffInfo.currentPrice ?? null)}
                                      </span>
                                    )}
                                    {diffInfo?.detailedChanges &&
                                      diffInfo.detailedChanges.some((c) => c.field === 'price') &&
                                      typeof diffInfo.currentPrice === 'number' &&
                                      typeof unitCost === 'number' && (
                                        <span className="block text-[10px] text-amber-700 dark:text-amber-300">
                                          {fmtMoney(diffInfo.currentPrice)} → {fmtMoney(unitCost)}
                                        </span>
                                      )}
                                  </td>
                                  <td className="py-1.5 px-2 text-right font-mono text-xs md:text-sm tabular-nums whitespace-nowrap">
                                    {fmtMoney(subtotal)}
                                  </td>
                                  <td className="py-1.5 px-2 text-right font-mono text-xs md:text-sm tabular-nums whitespace-nowrap">
                                    {fmtMoney(itemShipping)}
                                  </td>
                                  <td className="py-1.5 px-2 text-right font-mono text-xs md:text-sm tabular-nums whitespace-nowrap">
                                    {fmtMoney(itemTax)}
                                  </td>
                                  <td className="py-1.5 px-2 text-right font-mono text-xs md:text-sm tabular-nums whitespace-nowrap">
                                    {fmtMoney(lineTotal)}
                                  </td>
                                </tr>
                              )
                            })
                          )}
                        </tbody>
                        {items.length > 0 && (
                          <tfoot>
                            {(() => {
                              const itemsSubtotal = items.reduce((sum, it) => {
                                const q = (it as any)?.quantities?.ordered
                                const qtyN = typeof q === 'number' && Number.isFinite(q) ? q : 1
                                const unit = coerceNumber((it as any)?.pricing?.unitPrice)
                                return sum + (unit != null ? unit * qtyN : 0)
                              }, 0)

                              const orderShipping =
                                getFirstNumber(p as any, [
                                  'totals.shipping',
                                  'totals.shippingTotal',
                                  'totals.totalShipping',
                                  'externalOrder.shipping',
                                  'externalOrder.shippingTotal',
                                ]) ?? 0

                              const orderTax =
                                getFirstNumber(p as any, [
                                  'totals.tax',
                                  'totals.salesTax',
                                  'totals.taxTotal',
                                  'totals.totalTax',
                                  'externalOrder.tax',
                                  'externalOrder.salesTax',
                                  'externalOrder.taxTotal',
                                ]) ?? 0

                              const orderDiscount =
                                Math.max(
                                  0,
                                  getFirstNumber(p as any, [
                                    'orderDiscount',
                                    'totals.discount',
                                    'totals.orderDiscount',
                                    'externalOrder.discount',
                                    'externalOrder.orderDiscount',
                                  ]) ?? 0
                                ) || 0

                              const orderTotal = itemsSubtotal + orderShipping + orderTax - orderDiscount

                              const rowClass =
                                'bg-brand-50/60 dark:bg-gray-900/50 border-t border-brand-200 dark:border-gray-700'

                              return (
                                <>
                                  <tr className={rowClass}>
                                    <td colSpan={7} className="py-2 px-2 text-right text-xs text-ink-muted dark:text-gray-400">
                                      Subtotal
                                    </td>
                                    <td className="py-2 px-2 text-right font-mono text-xs md:text-sm tabular-nums whitespace-nowrap">
                                      {fmtMoney(itemsSubtotal)}
                                    </td>
                                  </tr>
                                  <tr className={rowClass}>
                                    <td colSpan={7} className="py-2 px-2 text-right text-xs text-ink-muted dark:text-gray-400">
                                      Order discount
                                    </td>
                                    <td className="py-2 px-2 text-right font-mono text-xs md:text-sm tabular-nums whitespace-nowrap">
                                      {orderDiscount > 0 ? `−${fmtMoney(orderDiscount)}` : '—'}
                                    </td>
                                  </tr>
                                  <tr className={rowClass}>
                                    <td colSpan={7} className="py-2 px-2 text-right text-xs font-semibold text-ink dark:text-gray-200">
                                      Order total
                                    </td>
                                    <td className="py-2 px-2 text-right font-mono text-xs md:text-sm font-semibold tabular-nums whitespace-nowrap text-ink dark:text-gray-100">
                                      {fmtMoney(orderTotal)}
                                    </td>
                                  </tr>
                                </>
                              )
                            })()}
                          </tfoot>
                        )}
                      </table>
                    </div>

                    {shipments.length > 0 && (
                      <div>
                        <h3 className="text-xs font-semibold text-ink dark:text-gray-200 mb-1.5">
                          Shipments ({shipments.length})
                        </h3>
                        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2.5">
                          {shipments.map((s, sIdx) => {
                            const displayTracking = normalizeTrackingForDisplay(
                              storeName,
                              orderId,
                              s.trackingNumber ?? null
                            )
                            const shipDiff =
                              s.trackingNumber && isExisting
                                ? shipmentDiffMap.get(s.trackingNumber)
                                : undefined
                            return (
                              <div
                                key={s.shipmentId ?? sIdx}
                                className={`bg-white dark:bg-gray-900 rounded-md border px-2.5 py-2 text-xs space-y-1 ${
                                  shipDiff?.status === 'new'
                                    ? 'border-emerald-300 dark:border-emerald-600/50'
                                    : shipDiff?.status === 'changed'
                                      ? 'border-amber-300 dark:border-amber-600/50'
                                      : 'border-brand-200/80 dark:border-gray-700'
                                }`}
                              >
                                {shipDiff?.status === 'new' && (
                                  <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-semibold uppercase bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-400">
                                    New
                                  </span>
                                )}
                                {shipDiff?.status === 'changed' && (
                                  <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-semibold uppercase bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-400">
                                    Updated
                                  </span>
                                )}
                                {displayTracking && (
                                  <div className="flex items-center gap-1.5">
                                    <span className="text-[10px] text-ink-muted shrink-0">
                                      Tracking:
                                    </span>
                                    {s.trackingUrl ? (
                                      <a
                                        href={s.trackingUrl}
                                        target="_blank"
                                        rel="noreferrer"
                                        className="font-mono text-brand-600 dark:text-brand-400 hover:underline"
                                        aria-label="Track shipment"
                                      >
                                        {displayTracking}
                                      </a>
                                    ) : (
                                      <span className="font-mono">
                                        {displayTracking}
                                      </span>
                                    )}
                                  </div>
                                )}
                                {s.status?.message && (
                                  <div className="text-[11px] text-ink-muted dark:text-gray-400">
                                    {s.status.message}
                                  </div>
                                )}
                                {s.deliveryDate && (
                                  <div className="text-[11px] text-ink-muted dark:text-gray-400">
                                    Delivered: {fmtDate(s.deliveryDate)}
                                  </div>
                                )}
                                {shipDiff?.detailedChanges && shipDiff.detailedChanges.length > 0 && (
                                  <div className="mt-0.5 space-y-0.5">
                                    {shipDiff.detailedChanges.map((c, i) => {
                                      if (c.field === 'delivery_date') {
                                        return (
                                          <div
                                            key={i}
                                            className="text-[10px] text-amber-700 dark:text-amber-300"
                                          >
                                            Delivery date changed:{' '}
                                            {c.before ? fmtDate(String(c.before)) : '—'} →{' '}
                                            {c.after ? fmtDate(String(c.after)) : '—'}
                                          </div>
                                        )
                                      }
                                      if (c.field === 'status') {
                                        return (
                                          <div
                                            key={i}
                                            className="text-[10px] text-amber-700 dark:text-amber-300"
                                          >
                                            Status updated: {String(c.before ?? '—')} →{' '}
                                            {String(c.after ?? '—')}
                                          </div>
                                        )
                                      }
                                      return null
                                    })}
                                  </div>
                                )}
                                {s.fulfillmentType && (
                                  <div className="text-[11px] text-ink-muted dark:text-gray-400 capitalize">
                                    {s.fulfillmentType}
                                  </div>
                                )}
                              </div>
                            )
                          })}
                        </div>
                      </div>
                    )}
                  </div>
                </div>
                )}
              </div>
            </div>
          </>
          )
        })}
      </div>
    </div>
  )
}

