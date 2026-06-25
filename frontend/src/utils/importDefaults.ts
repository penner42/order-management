type ImportItem = {
  quantities?: { ordered?: number | null }
  pricing?: {
    unitPrice?: number | null
    linePrice?: number | null
    lineTotal?: number | null
  }
}

type ImportPayload = {
  items?: ImportItem[]
  orderDiscount?: unknown
  totals?: Record<string, unknown>
  externalOrder?: Record<string, unknown>
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
    let cur: unknown = obj
    let ok = true
    for (const part of parts) {
      if (cur && typeof cur === 'object' && part in cur) {
        cur = (cur as Record<string, unknown>)[part]
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

/** Default per-unit payout from imported item pricing (unit price, or line total / qty). */
export function getDefaultItemPayout(item: ImportItem): number | null {
  const qtyRaw = item.quantities?.ordered ?? 1
  const qty = typeof qtyRaw === 'number' && Number.isFinite(qtyRaw) ? qtyRaw : 1
  const unitPrice = coerceNumber(item.pricing?.unitPrice)
  if (unitPrice != null) return unitPrice

  const lineTotal =
    coerceNumber(item.pricing?.lineTotal) ?? coerceNumber(item.pricing?.linePrice)
  if (lineTotal != null && qty > 0) return lineTotal / qty
  return null
}

/** Order total for payment method default (items + shipping + tax − discount). */
export function getDefaultOrderTotal(payload: ImportPayload): number | null {
  const items = payload.items ?? []
  if (items.length > 0) {
    const itemsSubtotal = items.reduce((sum, it) => {
      const q = it.quantities?.ordered
      const qtyN = typeof q === 'number' && Number.isFinite(q) ? q : 1
      const unit = coerceNumber(it.pricing?.unitPrice)
      if (unit != null) return sum + unit * qtyN
      const line =
        coerceNumber(it.pricing?.lineTotal) ?? coerceNumber(it.pricing?.linePrice)
      return sum + (line ?? 0)
    }, 0)

    const orderShipping =
      getFirstNumber(payload, [
        'totals.shipping',
        'totals.shippingTotal',
        'totals.totalShipping',
        'externalOrder.shipping',
        'externalOrder.shippingTotal',
      ]) ?? 0

    const orderTax =
      getFirstNumber(payload, [
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
        getFirstNumber(payload, [
          'orderDiscount',
          'totals.discount',
          'totals.orderDiscount',
          'externalOrder.discount',
          'externalOrder.orderDiscount',
        ]) ?? 0
      ) || 0

    const total = itemsSubtotal + orderShipping + orderTax - orderDiscount
    if (total > 0) return total
  }

  const fromTotals =
    coerceNumber(payload.totals?.grandTotal) ?? coerceNumber(payload.totals?.subtotal)
  return fromTotals != null && fromTotals > 0 ? fromTotals : null
}

export function formatImportDefaultAmount(n: number): string {
  return n.toFixed(2)
}
