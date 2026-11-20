import { useState } from 'react'
import { api } from '../api/client'

interface BestBuyDetails {
  customer?: { firstName?: string; lastName?: string; email?: string }
  shippingAddresses?: Array<{
    fullName?: string
    addressLine1?: string
    addressLine2?: string
    city?: string
    state?: string
    postalCode?: string
    phoneNumber?: string
  }>
  price?: {
    productTotal?: number
    shippingTotal?: number
    totalSalesTax?: number
    totalTaxableFees?: number
    totalDiscounts?: number
    orderTotal?: number
  }
  payments?: Array<{
    description?: string
    type?: string
    amount?: number
    authorizedAmount?: number
    chargedAmount?: number
    refundedAmount?: number
    authorizationStatus?: string
  }>
  items?: Array<{
    id: string
    sku: string
    modelNumber?: string
    manufacturerName?: string
    thirdPartyOrderId?: string
    originalQuantity?: number
  }>
  fulfillmentGroups?: Array<{ groupId: string; displayName?: string; carrier?: string; trackingNumber?: string; status?: string; lineIds: string[] }>
  shipments?: Array<{ shipmentId?: string; trackingNumber?: string; message?: string; deliveryDate?: string }>
}

const money = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' })

export function BestBuyOrderDetails({ orderId }: { orderId: number }) {
  const [data, setData] = useState<BestBuyDetails | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function loadDetails() {
    if (data || loading) return
    setLoading(true)
    setError(null)
    try {
      setData(await api.get<BestBuyDetails>(`/orders/${orderId}/best-buy-details`))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load Best Buy details.')
    } finally {
      setLoading(false)
    }
  }

  const totals = [
    ['Product total', data?.price?.productTotal],
    ['Shipping', data?.price?.shippingTotal],
    ['Sales tax', data?.price?.totalSalesTax],
    ['Taxable fees', data?.price?.totalTaxableFees],
    ['Discounts', data?.price?.totalDiscounts],
    ['Order total', data?.price?.orderTotal],
  ] as const
  const name = [data?.customer?.firstName, data?.customer?.lastName].filter(Boolean).join(' ')

  return (
    <details className="text-xs text-ink dark:text-gray-200" onToggle={event => {
      if (event.currentTarget.open) void loadDetails()
    }}>
      <summary className="cursor-pointer text-brand-600 hover:underline dark:text-brand-400">Best Buy order details</summary>
      <div className="mt-2 space-y-3 rounded-md border border-brand-200 p-2 dark:border-gray-600">
        {loading && <p role="status">Loading Best Buy details…</p>}
        {error && <p role="alert">{error} <button type="button" onClick={loadDetails} className="underline">Retry</button></p>}
        {data && (
          <>
            {(name || data.customer?.email) && (
              <div>
                <p className="font-medium">Customer</p>
                {name && <p>{name}</p>}
                {data.customer?.email && <p className="break-all">{data.customer.email}</p>}
              </div>
            )}
            {data.shippingAddresses?.map((address, index) => (
              <div key={index}>
                <p className="font-medium">Ship to</p>
                <p>{address.fullName}</p>
                <p>{address.addressLine1}</p>
                {address.addressLine2 && <p>{address.addressLine2}</p>}
                <p>{[address.city, address.state].filter(Boolean).join(', ')} {address.postalCode}</p>
                {address.phoneNumber && <p>{address.phoneNumber}</p>}
              </div>
            ))}
            {totals.some(([, value]) => value != null) && (
              <dl className="space-y-0.5">
                {totals.map(([label, value]) => value == null ? null : (
                  <div key={label} className="flex justify-between gap-2"><dt>{label}</dt><dd>{money.format(value)}</dd></div>
                ))}
              </dl>
            )}
            {data.payments?.map((payment, index) => (
              <div key={index}>
                <p className="font-medium">{payment.description ?? payment.type ?? 'Payment'}</p>
                {payment.amount != null && <p>Amount {money.format(payment.amount)}</p>}
                {payment.authorizedAmount != null && <p>Authorized {money.format(payment.authorizedAmount)}</p>}
                {payment.chargedAmount != null && <p>Charged {money.format(payment.chargedAmount)}</p>}
                {payment.refundedAmount != null && payment.refundedAmount > 0 && <p>Refunded {money.format(payment.refundedAmount)}</p>}
              </div>
            ))}
            {!!data.items?.length && (
              <div>
                <p className="font-medium">Retailer line items</p>
                <ul className="mt-1 space-y-1">
                  {data.items.map(item => (
                    <li key={item.id} className="break-words">
                      <p>{item.id}</p>
                      <p>{[item.sku && `SKU ${item.sku}`, item.modelNumber, item.manufacturerName].filter(Boolean).join(' · ')}</p>
                      {item.originalQuantity != null && <p>Original quantity {item.originalQuantity}</p>}
                      {item.thirdPartyOrderId && <p>Seller order {item.thirdPartyOrderId}</p>}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {data.fulfillmentGroups?.map(group => (
              <div key={group.groupId}>
                <p className="font-medium">{group.displayName ?? 'Fulfillment group'}</p>
                <p>{group.lineIds.length} {group.lineIds.length === 1 ? 'line' : 'lines'}</p>
                {(group.carrier || group.trackingNumber || group.status) && (
                  <p>{[group.carrier, group.trackingNumber, group.status].filter(Boolean).join(' · ')}</p>
                )}
              </div>
            ))}
            {data.shipments?.map((shipment, index) => (
              <div key={shipment.shipmentId ?? index}>
                <p className="font-medium">Shipment</p>
                <p>{[shipment.trackingNumber, shipment.message, shipment.deliveryDate].filter(Boolean).join(' · ') || 'Tracking pending'}</p>
              </div>
            ))}
          </>
        )}
      </div>
    </details>
  )
}
