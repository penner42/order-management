import type { BuyingGroup } from '../api/types'

export function normalizeNameForMatching(value: unknown): string {
  if (typeof value !== 'string') return ''
  return value.trim().toLowerCase().replace(/\s+/g, ' ')
}

function getBuyingGroupMatchNames(group: BuyingGroup): string[] {
  const names: string[] = []
  const primary = normalizeNameForMatching(group.name)
  if (primary) names.push(primary)
  for (const alias of group.aliases ?? []) {
    const normalized = normalizeNameForMatching(alias)
    if (normalized) names.push(normalized)
  }
  return names
}

function longestMatchNameLength(group: BuyingGroup, addressNameLower: string): number {
  return getBuyingGroupMatchNames(group).reduce((max, name) => {
    if (!addressNameLower.includes(name)) return max
    return Math.max(max, name.length)
  }, 0)
}

/** Walmart/Amazon-style: address fields contain a buying group name or alias. */
export function matchBuyingGroupByAddressName(
  addressFields: Array<string | null | undefined> | string | null | undefined,
  groups: BuyingGroup[]
): number | null {
  if (!groups.length) return null
  const fields = Array.isArray(addressFields) ? addressFields : [addressFields]
  const normalizedFields = fields
    .map((field) => normalizeNameForMatching(field))
    .filter(Boolean)
  if (normalizedFields.length === 0) return null

  let best: BuyingGroup | null = null
  let bestLength = 0
  for (const group of groups) {
    const matchLength = normalizedFields.reduce(
      (max, field) => Math.max(max, longestMatchNameLength(group, field)),
      0
    )
    if (matchLength > bestLength) {
      best = group
      bestLength = matchLength
    }
  }
  return best?.id ?? null
}

/** Exact equality against buying group name or alias (legacy Costco helper). */
export function matchBuyingGroupByExactNames(
  names: Array<string | null | undefined>,
  groups: BuyingGroup[]
): number | null {
  if (!groups.length) return null
  const normalizedNames = new Set(
    names.map((name) => normalizeNameForMatching(name)).filter(Boolean)
  )
  if (normalizedNames.size === 0) return null

  for (const group of groups) {
    const matchNames = getBuyingGroupMatchNames(group)
    if (matchNames.some((name) => normalizedNames.has(name))) {
      return group.id
    }
  }
  return null
}

function shippingAddressMatchFields(
  shippingAddress: Record<string, unknown> | null
): Array<string | null | undefined> {
  if (!shippingAddress) return []
  return [
    shippingAddress.fullName as string | null | undefined,
    shippingAddress.firstName as string | null | undefined,
    shippingAddress.lastName as string | null | undefined,
    shippingAddress.addressLine1 as string | null | undefined,
    shippingAddress.addressLine2 as string | null | undefined,
  ]
}

export function autoMatchBuyingGroupIdForImport(
  payload: { store?: string | null; shippingAddress?: Record<string, unknown> | null },
  groups: BuyingGroup[]
): number | null {
  if (!payload || !groups.length) return null

  const storeName = String(payload.store ?? '')
    .trim()
    .toLowerCase()
  const shippingAddress = payload.shippingAddress ?? null
  const addressFields = shippingAddressMatchFields(shippingAddress)

  // Costco/Walmart/Amazon: name or address line contains buying group name or alias.
  if (storeName === 'costco' || storeName === 'walmart' || storeName === 'amazon') {
    return matchBuyingGroupByAddressName(addressFields, groups)
  }

  return null
}
