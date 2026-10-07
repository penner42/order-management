/** Walmart delivery-from-store / Spark / internalized last-mile fulfillment types. */
const WALMART_STORE_DELIVERY_FULFILLMENT_TYPES = new Set([
  'sc_delivery',
  'dfs',
  'internalized_parcel',
  'store_delivery',
  'delivery_from_store',
])

function normalizeFulfillmentToken(value: string | null | undefined): string {
  return (value || '').trim().toLowerCase().replace(/-/g, '_').replace(/\s+/g, '_')
}

export function isWalmart555Tracking(trackingNumber: string | null | undefined): boolean {
  if (!trackingNumber) return false
  const compact = trackingNumber.replace(/[\s-]+/g, '')
  return compact.length === 20 && compact.startsWith('555') && /^\d+$/.test(compact)
}

export function isWalmartStoreDeliveryFulfillment(
  fulfillmentType: string | null | undefined
): boolean {
  const normalized = normalizeFulfillmentToken(fulfillmentType)
  if (!normalized) return false
  if (WALMART_STORE_DELIVERY_FULFILLMENT_TYPES.has(normalized)) return true
  return normalized.includes('store') && normalized.includes('deliver')
}

/**
 * Display tracking for Walmart imports: store-delivery / 555… trackers show as the order id.
 */
export function normalizeTrackingForDisplay(
  storeName: string,
  externalOrderId: string | undefined | null,
  trackingNumber: string | undefined | null,
  fulfillmentType?: string | null
): string | null {
  const storeLower = (storeName || '').trim().toLowerCase()
  const orderId = externalOrderId?.trim() || ''
  const fulfillment = fulfillmentType || null
  const isStoreDelivery = isWalmartStoreDeliveryFulfillment(fulfillment)
  const is555 = isWalmart555Tracking(trackingNumber)

  if (storeLower === 'walmart' && orderId && (is555 || isStoreDelivery)) {
    return orderId
  }
  if (!trackingNumber) return null
  return trackingNumber
}
