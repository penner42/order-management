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

/** Walmart-style: shipping full name contains a buying group name or alias. */
export function matchBuyingGroupByAddressName(
  addressName: string | null | undefined,
  groups: BuyingGroup[]
): number | null {
  if (!groups.length) return null
  const addressNameLower = normalizeNameForMatching(addressName)
  if (!addressNameLower) return null

  let best: BuyingGroup | null = null
  let bestLength = 0
  for (const group of groups) {
    const matchLength = longestMatchNameLength(group, addressNameLower)
    if (matchLength > bestLength) {
      best = group
      bestLength = matchLength
    }
  }
  return best?.id ?? null
}

/** Costco-style: shipping first/last/full name exactly matches a buying group name or alias. */
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

export function autoMatchBuyingGroupIdForImport(
  payload: { store?: string | null; shippingAddress?: Record<string, unknown> | null },
  groups: BuyingGroup[]
): number | null {
  if (!payload || !groups.length) return null

  const storeName = String(payload.store ?? '')
    .trim()
    .toLowerCase()
  const shippingAddress = payload.shippingAddress ?? null

  if (storeName === 'costco') {
    return matchBuyingGroupByExactNames(
      [
        shippingAddress?.firstName as string | null | undefined,
        shippingAddress?.lastName as string | null | undefined,
        shippingAddress?.fullName as string | null | undefined,
      ],
      groups
    )
  }

  if (storeName === 'walmart') {
    return matchBuyingGroupByAddressName(
      shippingAddress?.fullName as string | null | undefined,
      groups
    )
  }

  if (storeName === 'amazon') {
    return matchBuyingGroupByAddressName(
      shippingAddress?.fullName as string | null | undefined,
      groups
    )
  }

  return null
}
