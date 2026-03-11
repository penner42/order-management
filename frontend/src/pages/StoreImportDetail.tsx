import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { StoreOrderImport } from '../api/types'

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
  totals?: { itemCount?: number | null; subtotal?: number | null }
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

export default function StoreImportDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [record, setRecord] = useState<StoreOrderImport | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [applying, setApplying] = useState(false)
  const [discarding, setDiscarding] = useState(false)

  useEffect(() => {
    if (!id) return
    setLoading(true)
    setError(null)
    api
      .get<StoreOrderImport>(`/integrations/stores/imports/${id}`)
      .then(setRecord)
      .catch((err: unknown) => {
        console.error(err)
        setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => setLoading(false))
  }, [id])

  async function handleApply() {
    if (!record || applying || discarding) return
    setApplying(true)
    setError(null)
    try {
      await api.post(`/integrations/stores/imports/${record.id}/apply`, {})
      navigate('/store-imports')
    } catch (err) {
      console.error(err)
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setApplying(false)
    }
  }

  async function handleDiscard() {
    if (!record || applying || discarding) return
    setDiscarding(true)
    setError(null)
    try {
      await api.post(`/integrations/stores/imports/${record.id}/discard`, {})
      navigate('/store-imports')
    } catch (err) {
      console.error(err)
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setDiscarding(false)
    }
  }

  if (loading) {
    return (
      <div className="max-w-7xl">
        <p className="text-ink-muted dark:text-gray-400">Loading…</p>
      </div>
    )
  }

  if (!record) {
    return (
      <div className="max-w-7xl">
        <h1 className="text-xl font-semibold text-ink dark:text-gray-100 mb-2">
          Store import
        </h1>
        <p className="text-ink-muted dark:text-gray-400 mb-4">
          Import not found.
        </p>
        <Link
          to="/store-imports"
          className="text-sm text-brand-600 dark:text-brand-400 hover:underline"
        >
          Back to Store Imports
        </Link>
      </div>
    )
  }

  const p = record.normalized_payload_json as NormalizedPayload | null
  const items = p?.items ?? []
  const shipments = p?.shipments ?? []
  const shipmentsById = new Map(shipments.map((s) => [s.shipmentId, s]))
  const customer = p?.customer
  const address = p?.shippingAddress
  const isPending = record.status === 'pending'

  return (
    <div className="max-w-7xl">
      <div className="flex items-center justify-between gap-4 mb-6">
        <div>
          <h1 className="text-xl font-semibold text-ink dark:text-gray-100">
            Store import — {record.store} #{record.external_order_id}
          </h1>
          <p className="text-sm text-ink-muted dark:text-gray-400 mt-1">
            Review this store order before importing.
          </p>
        </div>
        <Link
          to="/store-imports"
          className="text-sm text-brand-600 dark:text-brand-400 hover:underline whitespace-nowrap"
        >
          Back to Store Imports
        </Link>
      </div>

      {error && (
        <p className="mb-3 text-sm text-red-600 dark:text-red-400">{error}</p>
      )}

      {/* Order strip — same layout as Orders page */}
      <div className="flex gap-0 border-2 border-brand-400 dark:border-gray-400 rounded-xl overflow-hidden bg-brand-50/50 dark:bg-gray-600/80 mb-6">
        {/* Left: order info box */}
        <div className="w-[340px] shrink-0 flex flex-col gap-2.5 p-4 border-r-2 border-brand-400 dark:border-gray-400 bg-white/80 dark:bg-gray-700/80">
          <div className="flex items-center gap-2">
            <span className="text-xs text-ink-muted shrink-0 w-16">Order #</span>
            <span className="text-sm font-medium text-brand-700 dark:text-brand-400">
              {record.external_order_id}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-ink-muted shrink-0 w-16">Store</span>
            <span className="text-sm text-ink dark:text-gray-200 capitalize">
              {record.store}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-ink-muted shrink-0 w-16">Date</span>
            <span className="text-sm font-mono text-ink dark:text-gray-200">
              {fmtDate(p?.externalOrder?.orderDate)}
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
          {record.external_order_url && (
            <div className="flex items-center gap-2">
              <span className="text-xs text-ink-muted shrink-0 w-16">Link</span>
              <a
                href={record.external_order_url}
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
            <div className="flex items-center gap-2">
              <span className="text-xs text-ink-muted shrink-0 w-16">Status</span>
              <span className={`text-xs font-semibold uppercase tracking-wide ${
                record.status === 'pending'
                  ? 'text-yellow-600 dark:text-yellow-400'
                  : record.status === 'applied'
                    ? 'text-emerald-600 dark:text-emerald-400'
                    : 'text-ink-muted dark:text-gray-400'
              }`}>
                {record.status}
              </span>
            </div>
            {isPending && (
              <div className="flex gap-2 pt-1">
                <button
                  type="button"
                  onClick={handleApply}
                  disabled={applying || discarding}
                  className="inline-flex items-center justify-center px-3 py-1.5 text-sm font-medium rounded-md bg-brand-600 text-white hover:bg-brand-700 disabled:opacity-60 disabled:cursor-not-allowed"
                >
                  {applying ? 'Applying…' : 'Import order'}
                </button>
                <button
                  type="button"
                  onClick={handleDiscard}
                  disabled={applying || discarding}
                  className="inline-flex items-center justify-center px-3 py-1.5 text-sm font-medium rounded-md border border-brand-200/80 dark:border-gray-600 text-ink-muted hover:text-ink hover:bg-brand-100/60 dark:text-gray-300 dark:hover:text-gray-100 dark:hover:bg-gray-700 disabled:opacity-60 disabled:cursor-not-allowed"
                >
                  {discarding ? 'Discarding…' : 'Discard'}
                </button>
              </div>
            )}
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
                <th className="py-1.5 px-2 font-medium text-ink-muted w-0 whitespace-nowrap">Unit Price</th>
                <th className="py-1.5 px-2 font-medium text-ink-muted w-0 whitespace-nowrap">Line Price</th>
                <th className="py-1.5 px-2 font-medium text-ink-muted">Shipment status</th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 ? (
                <tr>
                  <td colSpan={6} className="py-4 px-2 text-center text-ink-muted dark:text-gray-400">
                    No items in this order.
                  </td>
                </tr>
              ) : (
                items.map((item, idx) => {
                  const qty = item.quantities?.ordered ?? 1
                  const shipSlices = item.shipments ?? []
                  const firstShipment = shipSlices[0]
                    ? shipmentsById.get(shipSlices[0].shipmentId ?? '')
                    : undefined
                  const trackingNumber = firstShipment?.trackingNumber ?? null
                  const trackingUrl = firstShipment?.trackingUrl ?? null
                  const shipmentStatus = firstShipment?.status?.message ?? firstShipment?.status?.rawStatusType ?? null
                  const deliveryDate = firstShipment?.deliveryDate

                  return (
                    <tr
                      key={item.logicalItemId ?? idx}
                      className={idx % 2 === 0 ? 'bg-white/60 dark:bg-gray-800/40' : 'bg-brand-50/30 dark:bg-gray-700/30'}
                    >
                      <td className="py-1.5 px-2 text-center font-mono text-sm">{qty}</td>
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
                        {fmtMoney(item.pricing?.unitPrice)}
                      </td>
                      <td className="py-1.5 px-2 text-right font-mono text-sm tabular-nums whitespace-nowrap">
                        {fmtMoney(item.pricing?.linePrice)}
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
            {items.length > 0 && p?.totals?.subtotal != null && (
              <tfoot>
                <tr className="border-t border-brand-200 dark:border-gray-600 bg-brand-100/30 dark:bg-gray-700/40">
                  <td className="py-1.5 px-2 text-right font-medium text-ink-muted" colSpan={4}>
                    Subtotal
                  </td>
                  <td className="py-1.5 px-2 text-right font-mono text-sm font-semibold tabular-nums">
                    {fmtMoney(p.totals.subtotal)}
                  </td>
                  <td />
                </tr>
              </tfoot>
            )}
          </table>
        </div>
      </div>

      {/* Shipments summary */}
      {shipments.length > 0 && (
        <div className="mb-6">
          <h2 className="text-sm font-semibold text-ink dark:text-gray-200 mb-2">
            Shipments ({shipments.length})
          </h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {shipments.map((s, idx) => (
              <div
                key={s.shipmentId ?? idx}
                className="bg-white dark:bg-gray-800 rounded-lg border border-brand-200/80 dark:border-gray-700 p-3 text-sm space-y-1"
              >
                {s.trackingNumber && (
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
                        {s.trackingNumber}
                        <svg className="w-3 h-3 inline-block ml-0.5 opacity-70" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                        </svg>
                      </a>
                    ) : (
                      <span className="font-mono text-xs">{s.trackingNumber}</span>
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
                {s.fulfillmentType && (
                  <div className="text-xs text-ink-muted dark:text-gray-400 capitalize">
                    {s.fulfillmentType}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
