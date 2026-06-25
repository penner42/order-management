import type { StoreAccount } from '../api/types'

export function matchStoreAccountIdByEmail(
  email: string | null | undefined,
  accounts: StoreAccount[]
): number | null {
  if (!accounts.length) return null
  const normalized = email?.trim().toLowerCase()
  if (!normalized) return null
  const match = accounts.find((a) => a.name.trim().toLowerCase() === normalized)
  return match?.id ?? null
}

export function matchStoreAccountIdForImport(
  payload: { customer?: { email?: string | null } | null },
  accounts: StoreAccount[]
): number | null {
  return matchStoreAccountIdByEmail(payload?.customer?.email, accounts)
}
