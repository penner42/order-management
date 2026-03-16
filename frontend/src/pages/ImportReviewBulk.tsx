import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { Store } from '../api/types'

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

function decodeBulkHash(): NormalizedPayload[] {
  const hash = window.location.hash.slice(1)
  if (!hash) return []
  try {
    const json = decodeURIComponent(escape(atob(hash)))
    const data = JSON.parse(json) as { orders?: NormalizedPayload[] }
    if (!data || !Array.isArray(data.orders)) return []
    return data.orders
  } catch {
    return []
  }
}

export default function ImportReviewBulk() {
  const payloads = useMemo(() => decodeBulkHash(), [])
  const [stores, setStores] = useState<Store[]>([])
  const [diffs, setDiffs] = useState<Record<number, OrderDiff | null>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [applyingByIndex, setApplyingByIndex] = useState<Record<number, boolean>>({})
  const [appliedByIndex, setAppliedByIndex] = useState<Record<number, boolean>>({})
  const [applyErrorByIndex, setApplyErrorByIndex] = useState<Record<number, string | null>>({})

  useEffect(() => {
    if (payloads.length === 0) {
      setLoading(false)
      return
    }
    setLoading(true)
    setError(null)
    Promise.all([
      api.get<Store[]>('/stores'),
      Promise.all(
        payloads.map((p, idx) =>
          api
            .post<{ diff: OrderDiff }>('/integrations/stores/orders/diff', p)
            .then((res) => ({ idx, diff: res.diff }))
        )
      ),
    ])
      .then(async ([storesList, diffResults]) => {
        setStores(storesList)
        const nextDiffs: Record<number, OrderDiff | null> = {}
        for (const r of diffResults) {
          nextDiffs[r.idx] = r.diff
        }
        setDiffs(nextDiffs)
      })
      .catch((err: unknown) => {
        console.error(err)
        setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => setLoading(false))
  }, [payloads])

  if (payloads.length === 0) {
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
      await api.post('/integrations/stores/orders/apply', {
        payload,
        store_account_id: null,
        buying_group_id: null,
        item_payouts: null,
        payment_methods: null,
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
        {payloads.map((p, idx) => {
          const diff = diffs[idx]
          const externalOrder = p.externalOrder || {}
          const storeName: string = p.store || ''
          const orderId: string = externalOrder.id || ''
          const isExisting = diff && diff.is_existing_order === true
          const hasChanges = diff && diff.has_changes
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
          const applyError = applyErrorByIndex[idx] || null

          const items: NormalizedItem[] = p.items ?? []
          const shipments: NormalizedShipment[] = p.shipments ?? []
          const shipmentsById = new Map((shipments as NormalizedShipment[]).map((s) => [s.shipmentId, s]))
          const customer = p.customer
          const address = p.shippingAddress

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
                      {store && (
                        <span className="inline-flex items-center rounded-full bg-brand-100 text-brand-800 dark:bg-gray-700 dark:text-gray-100 px-2 py-0.5 text-[11px]">
                          {store.name}
                        </span>
                      )}
                    </div>
                    <div className="text-xs text-ink-muted dark:text-gray-400">
                      {isExisting
                        ? hasChanges
                          ? 'Existing order with differences'
                          : 'Existing order with no differences'
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
                      onClick={() => handleApplySingle(idx)}
                      disabled={applying || applied}
                      className={`inline-flex items-center justify-center px-3 py-1.5 text-sm font-medium rounded-md ${
                        applied
                          ? 'bg-gray-300 text-gray-600 dark:bg-gray-700 dark:text-gray-300 cursor-default'
                          : 'bg-brand-600 text-white hover:bg-brand-700 disabled:opacity-60 disabled:cursor-not-allowed'
                      }`}
                    >
                      {applying
                        ? 'Saving…'
                        : applied
                          ? isExisting
                            ? 'Update Complete'
                            : 'Import Complete'
                          : isExisting
                            ? 'Update order'
                            : 'Import order'}
                    </button>
                  </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-[320px,minmax(0,1fr)] gap-4">
                  {/* Left: customer & shipping info */}
                  <div className="flex flex-col gap-2.5 rounded-lg border border-brand-200/70 dark:border-gray-700 bg-brand-50/40 dark:bg-gray-900/40 px-3 py-2.5">
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
                      <table className="w-full text-xs md:text-sm">
                        <thead>
                          <tr className="bg-brand-100/50 dark:bg-gray-800/70 text-left border-b border-brand-200 dark:border-gray-700">
                            <th className="py-1.5 px-2 font-medium text-ink-muted w-10">
                              Qty
                            </th>
                            <th className="py-1.5 px-2 font-medium text-ink-muted">
                              Description
                            </th>
                            <th className="py-1.5 px-2 font-medium text-ink-muted">
                              Tracking
                            </th>
                            <th className="py-1.5 px-2 font-medium text-ink-muted whitespace-nowrap">
                              Unit cost
                            </th>
                            <th className="py-1.5 px-2 font-medium text-ink-muted whitespace-nowrap">
                              Line total
                            </th>
                          </tr>
                        </thead>
                        <tbody>
                          {items.length === 0 ? (
                            <tr>
                              <td
                                colSpan={5}
                                className="py-3 px-2 text-center text-ink-muted dark:text-gray-400"
                              >
                                No items in this order.
                              </td>
                            </tr>
                          ) : (
                            items.map((item, itemIdx) => {
                              const qty = item.quantities?.ordered ?? 1
                              const unitCost = item.pricing?.unitPrice ?? null
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
                              const shipmentStatus =
                                firstShipment?.status?.message ??
                                firstShipment?.status?.rawStatusType ??
                                null
                              const deliveryDate = firstShipment?.deliveryDate
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
                                    {qty}
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
                                        <span className="block text-xs md:text-sm text-ink dark:text-gray-200 truncate max-w-[16rem] md:max-w-[20rem]">
                                          {item.name || '(unnamed item)'}
                                          {diffInfo?.status === 'new' && (
                                            <span className="ml-1.5 inline-flex items-center px-1.5 py-0.5 rounded text-[9px] md:text-[10px] font-semibold uppercase bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-400">
                                              New
                                            </span>
                                          )}
                                        </span>
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
                                    {fmtMoney(unitCost)}
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
                                    {fmtMoney(lineTotal)}
                                  </td>
                                </tr>
                              )
                            })
                          )}
                        </tbody>
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
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

