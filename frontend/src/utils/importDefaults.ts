type ImportItem = {
  name?: string | null
  quantities?: { ordered?: number | null }
  pricing?: {
    unitPrice?: number | null
    linePrice?: number | null
    lineTotal?: number | null
  }
}

type ImportPayload = {
  store?: string
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

function getItemUnitPrice(item: ImportItem): number | null {
  const qtyRaw = item.quantities?.ordered ?? 1
  const qty = typeof qtyRaw === 'number' && Number.isFinite(qtyRaw) ? qtyRaw : 1
  const unitPrice = coerceNumber(item.pricing?.unitPrice)
  if (unitPrice != null) return unitPrice

  const lineTotal =
    coerceNumber(item.pricing?.lineTotal) ?? coerceNumber(item.pricing?.linePrice)
  if (lineTotal != null && qty > 0) return lineTotal / qty
  return null
}

function costcoBundleAllocation(payload: ImportPayload) {
  const includedTitle = /^(?:microsoft\s+(?:office\b|365\b)|mcafee\b|norton\b|adobe\s+(?:acrobat\b|creative\b|photoshop\b)|(?:antivirus|security)\s+software\b|allstate\b)/i
  const items = payload.items ?? []
  const hasProduct = items.some(other => {
    const name = other.name?.trim() ?? ''
    return name !== '' && !includedTitle.test(name)
  })
  const isIncluded = (other: ImportItem) => hasProduct && includedTitle.test(other.name?.trim() ?? '')
  let includedCost = 0
  let subtotal = 0
  for (const other of items) {
    const qty = Math.max(0, coerceNumber(other.quantities?.ordered) || 1)
    const cost = Math.max(0, getItemUnitPrice(other) ?? 0) * qty
    if (isIncluded(other)) includedCost += cost
    else subtotal += cost
  }
  return { isIncluded, includedCost, subtotal }
}

export function getImportOrderTax(payload: ImportPayload): number | null {
  const tax = getFirstNumber(payload, [
    'totals.tax', 'totals.salesTax', 'totals.taxTotal', 'totals.totalTax',
    'externalOrder.tax', 'externalOrder.salesTax', 'externalOrder.taxTotal',
  ])
  return tax == null ? null : Math.max(0, tax)
}

/** Per-unit Costco tax share, matching the saved purchase cost. */
export function getDefaultItemSalesTax(item: ImportItem, payload: ImportPayload): number | null {
  const tax = getImportOrderTax(payload)
  if (payload.store?.trim().toLowerCase() !== 'costco' || tax == null) return null
  const { isIncluded, subtotal } = costcoBundleAllocation(payload)
  if (isIncluded(item)) return 0
  const unitPrice = getItemUnitPrice(item)
  if (unitPrice == null) return null
  const share = subtotal > 0 && unitPrice > 0 ? unitPrice * tax / subtotal : 0
  return Math.round((share + Number.EPSILON) * 100) / 100
}

/** Default per-unit payout, including Costco's order discount, bundle credit, and tax. */
export function getDefaultItemPayout(item: ImportItem, payload?: ImportPayload): number | null {
  const unitPrice = getItemUnitPrice(item)
  if (payload?.store?.trim().toLowerCase() !== 'costco') return unitPrice

  // Keep this calculation consistent with default_costco_payouts on the backend.
  const { isIncluded, includedCost, subtotal } = costcoBundleAllocation(payload)
  if (isIncluded(item)) return 0
  if (unitPrice == null) return null
  const discount = Math.min(subtotal, Math.max(0, (coerceNumber(payload.orderDiscount) ?? 0) - includedCost))
  const tax = getImportOrderTax(payload) ?? 0
  const payout = subtotal > 0 && unitPrice > 0 ? unitPrice * (1 + (tax - discount) / subtotal) : unitPrice
  return Math.round((payout + Number.EPSILON) * 100) / 100
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

    const orderTax = getImportOrderTax(payload) ?? 0

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
