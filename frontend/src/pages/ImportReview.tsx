import { useEffect, useState, useMemo } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type {
  BuyingGroup,
  Store,
  StoreAccount,
  PaymentMethod,
} from '../api/types'

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

interface NormalizedPayload {
  store?: string
  externalOrder?: {
    id?: string
    orderDate?: string | null
    url?: string | null
  }
  customer?: { email?: string | null; firstName?: string | null; lastName?: string | null }
  shippingAddress?: {
    fullName?: string | null
    addressLine1?: string | null
    addressLine2?: string | null
    city?: string | null
    state?: string | null
    postalCode?: string | null
  }
  shipments?: NormalizedShipment[]
  items?: NormalizedItem[]
  totals?: { itemCount?: number | null; subtotal?: number | null; grandTotal?: number | null }
  paymentMethods?: {
    description?: string | null
    cardType?: string | null
    paymentType?: string | null
    last4?: string | null
  }[]
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

function decodeHashPayload(): NormalizedPayload | null {
  const hash = window.location.hash.slice(1)
  if (!hash) return null
  try {
    const json = decodeURIComponent(escape(atob(hash)))
    const data = JSON.parse(json) as NormalizedPayload
    if (!data || !data.externalOrder) return null
    return data
  } catch {
    return null
  }
}

export default function ImportReview() {
  const navigate = useNavigate()
  const payload = useMemo(() => decodeHashPayload(), [])

  const [diff, setDiff] = useState<OrderDiff | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [applying, setApplying] = useState(false)
  const [stores, setStores] = useState<Store[]>([])
  const [accountsByStore, setAccountsByStore] = useState<Record<number, StoreAccount[]>>({})
  const [selectedAccountId, setSelectedAccountId] = useState<number | null>(null)
  const [accountAutoMatched, setAccountAutoMatched] = useState(false)
  const [buyingGroups, setBuyingGroups] = useState<BuyingGroup[]>([])
  const [selectedBuyingGroupId, setSelectedBuyingGroupId] = useState<number | null>(null)
  const [itemPayouts, setItemPayouts] = useState<string[]>([])
  const [paymentMethods, setPaymentMethods] = useState<PaymentMethod[]>([])
  const [selectedPaymentMethodId, setSelectedPaymentMethodId] = useState<number | null>(null)
  const [paymentAmount, setPaymentAmount] = useState<string>('')
  const [paymentAutoMatched, setPaymentAutoMatched] = useState(false)

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

  useEffect(() => {
    if (!payload) {
      setLoading(false)
      return
    }
    setLoading(true)
    setError(null)
    Promise.all([
      api.post<{ diff: OrderDiff }>('/integrations/stores/orders/diff', payload),
      api.get<Store[]>('/stores'),
      api.get<BuyingGroup[]>('/buying-groups'),
      api.get<PaymentMethod[]>('/payment-methods'),
    ])
      .then(async ([diffRes, storesList, groups, methods]) => {
        setDiff(diffRes.diff)
        setStores(storesList)
        setBuyingGroups(groups)
        setPaymentMethods(methods)
        const byStore: Record<number, StoreAccount[]> = {}
        await Promise.all(
          storesList.map((s) =>
            api.get<StoreAccount[]>(`/stores/${s.id}/accounts`).then((list) => {
              byStore[s.id] = list
            })
          )
        )
        setAccountsByStore(byStore)
        const items = payload.items ?? []
        setItemPayouts(items.map(() => ''))
      })
      .catch((err: unknown) => {
        console.error(err)
        setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => setLoading(false))
  }, [payload])

  const storeName = payload?.store ?? ''
  const externalOrderId = payload?.externalOrder?.id ?? ''
  const externalOrderUrl = payload?.externalOrder?.url ?? null

  const matchedStore = useMemo(() => {
    const name = storeName.trim().toLowerCase()
    return stores.find((s) => s.name.toLowerCase() === name) ?? null
  }, [storeName, stores])

  const storeAccounts = useMemo(() => {
    if (!matchedStore) return []
    return accountsByStore[matchedStore.id] ?? []
  }, [matchedStore, accountsByStore])

  // Auto-match account by email
  useEffect(() => {
    if (!payload || storeAccounts.length === 0) return
    const email = payload.customer?.email?.trim().toLowerCase()
    if (!email) return
    const match = storeAccounts.find((a) => a.name.trim().toLowerCase() === email)
    if (match) {
      setSelectedAccountId(match.id)
      setAccountAutoMatched(true)
    }
  }, [payload, storeAccounts])

  // Prefill payment total
  useEffect(() => {
    if (!payload) return
    if (paymentAmount.trim()) return
    const items = payload.items ?? []
    let total = payload.totals?.grandTotal ?? payload.totals?.subtotal ?? null
    if (total == null && items.length > 0) {
      total = items.reduce((sum, item) => {
        const qty = item.quantities?.ordered ?? 1
        const price =
          item.pricing?.lineTotal ??
          item.pricing?.linePrice ??
          item.pricing?.unitPrice ??
          0
        return sum + (price ?? 0) * (qty ?? 1)
      }, 0)
    }
    if (typeof total === 'number' && total > 0) {
      setPaymentAmount(total.toFixed(2))
    }
  }, [payload, paymentAmount])

  // Auto-match payment method by last4
  useEffect(() => {
    if (!payload || flattenedPaymentMethods.length === 0) return
    const externalPaymentMethods = payload.paymentMethods ?? []
    if (externalPaymentMethods.length === 0) return
    let last4: string | null = null
    for (const pm of externalPaymentMethods) {
      if (pm.last4 && pm.last4.length === 4) {
        last4 = pm.last4
        break
      }
      const desc = pm.description ?? ''
      const m = /(\d{4})\b/.exec(desc)
      if (m) {
        last4 = m[1]
        break
      }
    }
    if (!last4) return
    const match = flattenedPaymentMethods.find((m) => {
      const labelMatch = /(\d{4})\b/.exec(m.label)
      return labelMatch && labelMatch[1] === last4
    })
    if (!match) return
    setSelectedPaymentMethodId(match.id)
    if (paymentAmount.trim()) {
      setPaymentAutoMatched(true)
    }
  }, [payload, flattenedPaymentMethods, paymentAmount])

  // Auto-match buying group by address name
  useEffect(() => {
    if (!payload || buyingGroups.length === 0) return
    const addressName = payload.shippingAddress?.fullName?.trim()
    if (!addressName) return
    const addressNameLower = addressName.toLowerCase()
    const matching = buyingGroups.filter((g) => {
      const groupName = g.name.trim()
      if (!groupName) return false
      return addressNameLower.includes(groupName.toLowerCase())
    })
    if (matching.length === 0) return
    const best = matching.reduce((a, b) =>
      (a.name.length >= b.name.length ? a : b)
    )
    setSelectedBuyingGroupId(best.id)
  }, [payload, buyingGroups])

  async function handleApply() {
    if (!payload || applying) return
    setApplying(true)
    setError(null)
    try {
      const payouts: (number | null)[] = itemPayouts.map((s) => {
        const t = s.trim()
        if (!t) return null
        const n = Number(t)
        return Number.isFinite(n) ? n : null
      })
      let paymentPayload: { payment_method_id: number; amount: number }[] | undefined
      const trimmedAmount = paymentAmount.trim()
      if (selectedPaymentMethodId != null && trimmedAmount) {
        const n = Number(trimmedAmount)
        if (Number.isFinite(n) && n > 0) {
          paymentPayload = [{ payment_method_id: selectedPaymentMethodId, amount: n }]
        }
      }
      await api.post('/integrations/stores/orders/apply', {
        payload,
        store_account_id: selectedAccountId,
        buying_group_id: selectedBuyingGroupId,
        item_payouts: payouts,
        payment_methods: paymentPayload,
      })
      navigate('/', {
        state: { orderSearch: externalOrderId },
      })
    } catch (err) {
      console.error(err)
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setApplying(false)
    }
  }

  if (!payload) {
    return (
      <div className="max-w-2xl mx-auto">
        <h1 className="text-xl font-semibold text-ink dark:text-gray-100 mb-2">
          Import review
        </h1>
        <p className="text-ink-muted dark:text-gray-400 mb-4">
          No order data in this link. Use the Order Manager browser extension on
          a store order page: get order details, then click &ldquo;Send to Order
          Manager&rdquo;.
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
        <p className="text-ink-muted dark:text-gray-400">Loading…</p>
      </div>
    )
  }

  const p = payload
  const items = p.items ?? []
  const shipments = p.shipments ?? []
  const shipmentsById = new Map(shipments.map((s) => [s.shipmentId, s]))
  const customer = p.customer
  const address = p.shippingAddress

  const isExistingOrder = diff?.is_existing_order === true

  const itemDiffMap = new Map<string, ItemDiffInfo>()
  if (isExistingOrder && diff?.items) {
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
  if (isExistingOrder && diff?.shipments) {
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
    <div>
      <div className="flex items-center justify-between gap-4 mb-6">
        <div>
          <h1 className="text-xl font-semibold text-ink dark:text-gray-100">
            Import review — {storeName} #{externalOrderId}
          </h1>
          <p className="text-sm text-ink-muted dark:text-gray-400 mt-1">
            {isExistingOrder
              ? 'This order already exists — review the changes below.'
              : 'Review this store order before importing. Nothing has been saved yet.'}
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

      {isExistingOrder && (
        <div className={`mb-6 rounded-lg border px-4 py-3 ${
          diff?.has_changes
            ? 'border-amber-300 bg-amber-50 dark:border-amber-700/50 dark:bg-amber-950/30'
            : 'border-emerald-300 bg-emerald-50 dark:border-emerald-700/50 dark:bg-emerald-950/30'
        }`}>
          <div className="flex items-start gap-2.5">
            <svg className={`w-5 h-5 mt-0.5 shrink-0 ${
              diff?.has_changes ? 'text-amber-600 dark:text-amber-400' : 'text-emerald-600 dark:text-emerald-400'
            }`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <div>
              <p className={`text-sm font-medium ${
                diff?.has_changes ? 'text-amber-800 dark:text-amber-200' : 'text-emerald-800 dark:text-emerald-200'
              }`}>
                {diff?.has_changes
                  ? 'Differences detected between this import and the existing order.'
                  : 'No differences — this import matches the existing order.'}
              </p>
              {diff?.has_changes && (
                <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-xs">
                  {(diff.items?.matched.filter(m => m.changes.length > 0).length ?? 0) > 0 && (
                    <span className="text-amber-700 dark:text-amber-300">
                      {diff.items!.matched.filter(m => m.changes.length > 0).length} item(s) changed
                    </span>
                  )}
                  {(diff.items?.added.length ?? 0) > 0 && (
                    <span className="text-emerald-700 dark:text-emerald-300">
                      {diff.items!.added.length} new item(s)
                    </span>
                  )}
                  {(diff.items?.unmatched_existing.length ?? 0) > 0 && (
                    <span className="text-gray-500 dark:text-gray-400">
                      {diff.items!.unmatched_existing.length} existing item(s) not in import
                    </span>
                  )}
                  {(diff.shipments?.added.length ?? 0) > 0 && (
                    <span className="text-emerald-700 dark:text-emerald-300">
                      {diff.shipments!.added.length} new shipment(s)
                    </span>
                  )}
                  {(diff.shipments?.matched.filter(m => m.changes.length > 0).length ?? 0) > 0 && (
                    <span className="text-amber-700 dark:text-amber-300">
                      {diff.shipments!.matched.filter(m => m.changes.length > 0).length} shipment(s) updated
                    </span>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      <div className="flex gap-0 border-2 border-brand-400 dark:border-gray-400 rounded-xl overflow-hidden bg-brand-50/50 dark:bg-gray-600/80 mb-6">
        {/* Left: order info box */}
        <div className="w-[340px] shrink-0 flex flex-col gap-2.5 p-4 border-r-2 border-brand-400 dark:border-gray-400 bg-white/80 dark:bg-gray-700/80">
          <div className="flex items-center gap-2">
            <span className="text-xs text-ink-muted shrink-0 w-16">Order #</span>
            <span className="text-sm font-medium text-brand-700 dark:text-brand-400">
              {externalOrderId}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-ink-muted shrink-0 w-16">Store</span>
            <span className="text-sm text-ink dark:text-gray-200 capitalize">
              {storeName}
            </span>
          </div>
          {storeAccounts.length > 0 && (
            <div className="flex items-center gap-2">
              <span className="text-xs text-ink-muted shrink-0 w-16">Account</span>
              <div className="flex items-center gap-1.5">
                <select
                  value={selectedAccountId ?? ''}
                  onChange={(e) => {
                    setSelectedAccountId(e.target.value ? Number(e.target.value) : null)
                    setAccountAutoMatched(false)
                  }}
                  className="text-sm rounded border border-brand-200 dark:border-gray-600 dark:bg-gray-800 px-2 py-1 text-ink dark:text-gray-200"
                >
                  <option value="">None</option>
                  {storeAccounts.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name}
                    </option>
                  ))}
                </select>
                {accountAutoMatched && selectedAccountId != null && (
                  <span className="text-[10px] text-emerald-600 dark:text-emerald-400 font-medium whitespace-nowrap">
                    matched by email
                  </span>
                )}
              </div>
            </div>
          )}
          {flattenedPaymentMethods.length > 0 && (
            <div className="flex items-start gap-2">
              <span className="text-xs text-ink-muted shrink-0 w-16 pt-0.5">Payment</span>
              <div className="flex flex-col gap-1.5">
                <select
                  value={selectedPaymentMethodId ?? ''}
                  onChange={(e) => {
                    const v = e.target.value ? Number(e.target.value) : null
                    setSelectedPaymentMethodId(v)
                    setPaymentAutoMatched(false)
                  }}
                  className="text-sm rounded border border-brand-200 dark:border-gray-600 dark:bg-gray-800 px-2 py-1 text-ink dark:text-gray-200"
                >
                  <option value="">None</option>
                  {flattenedPaymentMethods.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.label}
                    </option>
                  ))}
                </select>
                <div className="flex items-center gap-1.5">
                  <span className="text-xs text-ink-muted">Total</span>
                  <input
                    type="text"
                    inputMode="decimal"
                    placeholder="—"
                    value={paymentAmount}
                    onChange={(e) => {
                      setPaymentAmount(e.target.value)
                      setPaymentAutoMatched(false)
                    }}
                    className="w-24 text-right text-sm font-mono rounded border border-brand-200 dark:border-gray-600 dark:bg-gray-800 px-1.5 py-1 text-ink dark:text-gray-200 tabular-nums"
                    aria-label="Order total for selected payment method"
                  />
                  {paymentAutoMatched && selectedPaymentMethodId != null && (
                    <span className="text-[10px] text-emerald-600 dark:text-emerald-400 font-medium whitespace-nowrap">
                      matched by last 4
                    </span>
                  )}
                </div>
              </div>
            </div>
          )}
          {buyingGroups.length > 0 && (
            <div className="flex items-center gap-2">
              <span className="text-xs text-ink-muted shrink-0 w-16">Buying group</span>
              <select
                value={selectedBuyingGroupId ?? ''}
                onChange={(e) => setSelectedBuyingGroupId(e.target.value ? Number(e.target.value) : null)}
                className="text-sm rounded border border-brand-200 dark:border-gray-600 dark:bg-gray-800 px-2 py-1 text-ink dark:text-gray-200"
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
          <div className="flex items-center gap-2">
            <span className="text-xs text-ink-muted shrink-0 w-16">Date</span>
            <span className="text-sm font-mono text-ink dark:text-gray-200">
              {fmtDate(p.externalOrder?.orderDate)}
            </span>
          </div>
          {customer && (customer.firstName || customer.lastName || customer.email) && (
            <div className="flex items-start gap-2">
              <span className="text-xs text-ink-muted shrink-0 w-16 pt-0.5">Customer</span>
              <div className="text-sm text-ink dark:text-gray-200">
                {[customer.firstName, customer.lastName].filter(Boolean).join(' ') || null}
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
              <span className="text-xs text-ink-muted shrink-0 w-16 pt-0.5">Ship to</span>
              <div className="text-sm text-ink dark:text-gray-200 leading-snug">
                {address.fullName && <span className="block font-medium">{address.fullName}</span>}
                {address.addressLine1 && <span className="block">{address.addressLine1}</span>}
                {address.addressLine2 && <span className="block">{address.addressLine2}</span>}
                {(address.city || address.state || address.postalCode) && (
                  <span className="block">
                    {[address.city, address.state].filter(Boolean).join(', ')}
                    {address.postalCode ? ` ${address.postalCode}` : ''}
                  </span>
                )}
              </div>
            </div>
          )}
          {externalOrderUrl && (
            <div className="flex items-center gap-2">
              <span className="text-xs text-ink-muted shrink-0 w-16">Link</span>
              <a
                href={externalOrderUrl}
                target="_blank"
                rel="noreferrer"
                className="text-sm text-brand-600 dark:text-brand-400 hover:underline truncate"
                aria-label="Open order on store site"
              >
                <svg className="w-4 h-4 inline-block" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                </svg>
              </a>
            </div>
          )}

          <div className="border-t border-brand-200 dark:border-gray-600 pt-2.5 mt-1 flex flex-col gap-2">
            <div className="flex gap-2 pt-1">
              <button
                type="button"
                onClick={handleApply}
                disabled={applying}
                className="inline-flex items-center justify-center px-3 py-1.5 text-sm font-medium rounded-md bg-brand-600 text-white hover:bg-brand-700 disabled:opacity-60 disabled:cursor-not-allowed"
              >
                {applying ? 'Saving…' : isExistingOrder ? 'Update order' : 'Import order'}
              </button>
            </div>
          </div>
        </div>

        {/* Right: items table */}
        <div className="flex-1 min-w-0 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-brand-100/50 dark:bg-gray-700/50 text-left border-b border-brand-200 dark:border-gray-600">
                <th className="py-1.5 px-2 font-medium text-ink-muted w-12">Qty</th>
                <th className="py-1.5 px-2 font-medium text-ink-muted">Description</th>
                <th className="py-1.5 px-2 font-medium text-ink-muted">Tracking</th>
                <th className="py-1.5 px-2 font-medium text-ink-muted w-0 whitespace-nowrap">Unit cost</th>
                <th className="py-1.5 px-2 font-medium text-ink-muted w-0 whitespace-nowrap">Line total</th>
                <th className="py-1.5 px-2 font-medium text-ink-muted w-0 whitespace-nowrap">Unit Payout</th>
                <th className="py-1.5 px-2 font-medium text-ink-muted w-0 whitespace-nowrap">Total Payout</th>
                <th className="py-1.5 px-2 font-medium text-ink-muted">Shipment status</th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 ? (
                <tr>
                  <td colSpan={8} className="py-4 px-2 text-center text-ink-muted dark:text-gray-400">
                    No items in this order.
                  </td>
                </tr>
              ) : (
                items.map((item, idx) => {
                  const qty = item.quantities?.ordered ?? 1
                  const unitCost = item.pricing?.unitPrice ?? null
                  const lineTotal =
                    item.pricing?.lineTotal ??
                    item.pricing?.linePrice ??
                    (unitCost != null ? unitCost * qty : null)
                  const unitPayoutRaw = itemPayouts[idx]?.trim()
                  const unitPayoutNum = unitPayoutRaw ? Number(unitPayoutRaw) : NaN
                  const unitPayoutValid = Number.isFinite(unitPayoutNum)
                  const lineTotalPayout = unitPayoutValid ? unitPayoutNum * qty : null
                  const shipSlices = item.shipments ?? []
                  const firstShipment = shipSlices[0]
                    ? shipmentsById.get(shipSlices[0].shipmentId ?? '')
                    : undefined
                  const trackingNumberRaw = firstShipment?.trackingNumber ?? null
                  const trackingNumber = normalizeTrackingForDisplay(
                    storeName,
                    externalOrderId,
                    trackingNumberRaw
                  )
                  const trackingUrl = firstShipment?.trackingUrl ?? null
                  const shipmentStatus = firstShipment?.status?.message ?? firstShipment?.status?.rawStatusType ?? null
                  const deliveryDate = firstShipment?.deliveryDate
                  const diffInfo = isExistingOrder ? itemDiffMap.get((item.name || '').trim()) : undefined

                  return (
                    <tr
                      key={item.logicalItemId ?? idx}
                      className={`${idx % 2 === 0 ? 'bg-white/60 dark:bg-gray-800/40' : 'bg-brand-50/30 dark:bg-gray-700/30'}${
                        diffInfo?.status === 'new' ? ' border-l-[3px] border-l-emerald-400 dark:border-l-emerald-500' :
                        diffInfo?.status === 'changed' ? ' border-l-[3px] border-l-amber-400 dark:border-l-amber-500' : ''
                      }`}
                    >
                      <td className="py-1.5 px-2 text-center font-mono text-sm">
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
                              className="w-8 h-8 rounded object-cover shrink-0 border border-brand-200 dark:border-gray-600"
                            />
                          )}
                          <div className="min-w-0">
                            <span className="block text-sm text-ink dark:text-gray-200 truncate max-w-[20rem]">
                              {item.name || '(unnamed item)'}
                              {diffInfo?.status === 'new' && (
                                <span className="ml-1.5 inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-400">
                                  New
                                </span>
                              )}
                            </span>
                            {item.variants && item.variants.length > 0 && (
                              <span className="block text-xs text-ink-muted dark:text-gray-400 truncate">
                                {item.variants.filter((v) => v.name && v.value).map((v) => `${v.name}: ${v.value}`).join(', ')}
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
                              className="text-sm text-brand-600 dark:text-brand-400 hover:underline font-mono"
                              aria-label="Track shipment"
                            >
                              {trackingNumber}
                              <svg className="w-3.5 h-3.5 inline-block ml-1 opacity-70" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                              </svg>
                            </a>
                          ) : (
                            <span className="text-sm font-mono text-ink dark:text-gray-200">{trackingNumber}</span>
                          )
                        ) : (
                          <span className="text-xs text-ink-muted dark:text-gray-500">—</span>
                        )}
                      </td>
                      <td className="py-1.5 px-2 text-right font-mono text-sm tabular-nums whitespace-nowrap">
                        {fmtMoney(unitCost)}
                        {diffInfo?.changes.includes('price') && (
                          <span className="block text-[10px] text-amber-600 dark:text-amber-400">
                            was {fmtMoney(diffInfo.currentPrice)}
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
                      <td className="py-1.5 px-2 text-right font-mono text-sm tabular-nums whitespace-nowrap">
                        {fmtMoney(lineTotal)}
                      </td>
                      <td className="py-1.5 px-2">
                        <input
                          type="text"
                          inputMode="decimal"
                          placeholder="—"
                          value={itemPayouts[idx] ?? ''}
                          onChange={(e) => {
                            setItemPayouts((prev) => {
                              const next = [...prev]
                              while (next.length <= idx) next.push('')
                              next[idx] = e.target.value
                              return next
                            })
                          }}
                          className="w-20 text-right text-sm font-mono rounded border border-brand-200 dark:border-gray-600 dark:bg-gray-800 px-1.5 py-1 text-ink dark:text-gray-200 tabular-nums"
                          aria-label={`Unit payout for ${(item.name || '').slice(0, 30)}`}
                        />
                      </td>
                      <td className="py-1.5 px-2 text-right font-mono text-sm tabular-nums whitespace-nowrap">
                        {lineTotalPayout != null ? fmtMoney(lineTotalPayout) : '—'}
                      </td>
                      <td className="py-1.5 px-2">
                        {shipmentStatus && (
                          <span className="text-xs text-ink-muted dark:text-gray-400">
                            {shipmentStatus}
                          </span>
                        )}
                        {deliveryDate && (
                          <span className="block text-xs text-ink-muted dark:text-gray-500">
                            {fmtDate(deliveryDate)}
                          </span>
                        )}
                        {!shipmentStatus && !deliveryDate && (
                          <span className="text-xs text-ink-muted dark:text-gray-500">—</span>
                        )}
                      </td>
                    </tr>
                  )
                })
              )}
            </tbody>
            {items.length > 0 && (p.totals?.subtotal != null || items.length > 0) && (
              <tfoot>
                <tr className="border-t border-brand-200 dark:border-gray-600 bg-brand-100/30 dark:bg-gray-700/40">
                  <td className="py-1.5 px-2 text-right font-medium text-ink-muted" colSpan={5}>
                    Subtotal
                  </td>
                  <td />
                  <td className="py-1.5 px-2 text-right font-mono text-sm font-semibold tabular-nums">
                    {p.totals?.subtotal != null ? fmtMoney(p.totals.subtotal) : '—'}
                  </td>
                  <td className="py-1.5 px-2 text-right font-mono text-sm font-semibold tabular-nums">
                    {fmtMoney(
                      items.reduce((sum, item, i) => {
                        const u = itemPayouts[i]?.trim()
                        const n = u ? Number(u) : NaN
                        const qty = item.quantities?.ordered ?? 1
                        return sum + (Number.isFinite(n) ? n * qty : 0)
                      }, 0)
                    )}
                  </td>
                  <td />
                </tr>
              </tfoot>
            )}
          </table>
        </div>
      </div>

      {isExistingOrder && diff?.items?.unmatched_existing && diff.items.unmatched_existing.length > 0 && (
        <div className="mb-6 rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50 px-4 py-3">
          <h3 className="text-xs font-semibold text-ink-muted dark:text-gray-400 uppercase tracking-wide mb-2">
            Existing items not in this import
          </h3>
          <div className="space-y-1">
            {diff.items.unmatched_existing.map((ei, idx) => (
              <div key={idx} className="flex items-center gap-4 text-sm text-ink-muted dark:text-gray-400">
                <span className="truncate max-w-[20rem]">{ei.description || '(unnamed)'}</span>
                <span className="font-mono text-xs">qty {ei.quantity}</span>
                <span className="font-mono text-xs">{fmtMoney(ei.price_paid)}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {shipments.length > 0 && (
        <div className="mb-6">
          <h2 className="text-sm font-semibold text-ink dark:text-gray-200 mb-2">
            Shipments ({shipments.length})
          </h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {shipments.map((s, idx) => {
              const displayTracking = normalizeTrackingForDisplay(
                storeName,
                externalOrderId,
                s.trackingNumber ?? null
              )
              const shipDiff = s.trackingNumber ? shipmentDiffMap.get(s.trackingNumber) : undefined
              return (
              <div
                key={s.shipmentId ?? idx}
                className={`bg-white dark:bg-gray-800 rounded-lg border p-3 text-sm space-y-1 ${
                  shipDiff?.status === 'new'
                    ? 'border-emerald-300 dark:border-emerald-600/50'
                    : shipDiff?.status === 'changed'
                      ? 'border-amber-300 dark:border-amber-600/50'
                      : 'border-brand-200/80 dark:border-gray-700'
                }`}
              >
                {shipDiff?.status === 'new' && (
                  <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-400">
                    New
                  </span>
                )}
                {shipDiff?.status === 'changed' && (
                  <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-400">
                    Updated
                  </span>
                )}
                {displayTracking && (
                  <div className="flex items-center gap-1.5">
                    <span className="text-xs text-ink-muted shrink-0">Tracking:</span>
                    {s.trackingUrl ? (
                      <a
                        href={s.trackingUrl}
                        target="_blank"
                        rel="noreferrer"
                        className="font-mono text-brand-600 dark:text-brand-400 hover:underline text-xs"
                        aria-label="Track shipment"
                      >
                        {displayTracking}
                        <svg className="w-3 h-3 inline-block ml-0.5 opacity-70" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                        </svg>
                      </a>
                    ) : (
                      <span className="font-mono text-xs">{displayTracking}</span>
                    )}
                  </div>
                )}
                {s.status?.message && (
                  <div className="text-xs text-ink-muted dark:text-gray-400">
                    {s.status.message}
                  </div>
                )}
                {s.deliveryDate && (
                  <div className="text-xs text-ink-muted dark:text-gray-400">
                    Delivered: {fmtDate(s.deliveryDate)}
                  </div>
                )}
                {shipDiff?.detailedChanges && shipDiff.detailedChanges.length > 0 && (
                  <div className="mt-1 space-y-0.5">
                    {shipDiff.detailedChanges.map((c, i) => {
                      if (c.field === 'delivery_date') {
                        return (
                          <div
                            key={i}
                            className="text-[11px] text-amber-700 dark:text-amber-300"
                          >
                            Delivery date changed: {c.before ? fmtDate(String(c.before)) : '—'} →{' '}
                            {c.after ? fmtDate(String(c.after)) : '—'}
                          </div>
                        )
                      }
                      if (c.field === 'status') {
                        return (
                          <div
                            key={i}
                            className="text-[11px] text-amber-700 dark:text-amber-300"
                          >
                            Status updated: {String(c.before ?? '—')} → {String(c.after ?? '—')}
                          </div>
                        )
                      }
                      return null
                    })}
                  </div>
                )}
                {s.fulfillmentType && (
                  <div className="text-xs text-ink-muted dark:text-gray-400 capitalize">
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
  )
}
