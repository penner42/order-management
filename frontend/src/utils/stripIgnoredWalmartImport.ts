/** Walmart placeholder for digital / email delivery — ignore on import. */
const WALMART_IGNORED_TRACKING = 'sent via email'

function isIgnoredWalmartTracking(trackingNumber: unknown): boolean {
  return (
    typeof trackingNumber === 'string' &&
    trackingNumber.trim().toLowerCase() === WALMART_IGNORED_TRACKING
  )
}

type ImportShipment = {
  shipmentId?: string | null
  trackingNumber?: string | null
  [key: string]: unknown
}

type ImportItem = {
  shipments?: Array<{ shipmentId?: string | null; [key: string]: unknown }> | null
  [key: string]: unknown
}

type ImportPayload = {
  store?: string | null
  shipments?: ImportShipment[] | null
  items?: ImportItem[] | null
  [key: string]: unknown
}

/**
 * Remove Walmart shipments/items whose tracking is "Sent via email".
 * Returns the same object when nothing changes.
 */
export function stripIgnoredWalmartImportSlices<T extends ImportPayload>(payload: T): T {
  const store = (payload.store || '').trim().toLowerCase()
  if (store !== 'walmart') return payload

  const shipments = Array.isArray(payload.shipments) ? payload.shipments : []
  const ignoredShipmentIds = new Set<string>()
  const keptShipments: ImportShipment[] = []

  for (const shipment of shipments) {
    if (!shipment || typeof shipment !== 'object') continue
    if (isIgnoredWalmartTracking(shipment.trackingNumber)) {
      const sid = shipment.shipmentId
      if (sid != null && String(sid).trim()) {
        ignoredShipmentIds.add(String(sid).trim())
      }
      continue
    }
    keptShipments.push(shipment)
  }

  const items = Array.isArray(payload.items) ? payload.items : []
  const keptItems: ImportItem[] = []
  for (const item of items) {
    if (!item || typeof item !== 'object') continue
    const itemShips = Array.isArray(item.shipments) ? item.shipments : []
    const linkedToIgnored = itemShips.some((slice) => {
      const sid = slice?.shipmentId
      return sid != null && ignoredShipmentIds.has(String(sid).trim())
    })
    if (linkedToIgnored) continue
    keptItems.push(item)
  }

  if (keptShipments.length === shipments.length && keptItems.length === items.length) {
    return payload
  }

  return {
    ...payload,
    shipments: keptShipments,
    items: keptItems,
  }
}
