import { api } from '../api/client'
import type { IgnoredZipCode } from '../api/types'

const STORAGE_KEY = 'ignored_zip_codes'

/** Normalize postal codes for comparison (trim, case, strip spaces/hyphens). */
export function normalizeZipCode(zip: string): string {
  return zip.trim().toUpperCase().replace(/[\s-]/g, '')
}

/** Read legacy localStorage list (for one-time migration to the API). */
export function getLegacyIgnoredZipCodes(): string[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed
      .filter((z): z is string => typeof z === 'string')
      .map((z) => z.trim())
      .filter(Boolean)
  } catch {
    return []
  }
}

export function clearLegacyIgnoredZipCodes(): void {
  localStorage.removeItem(STORAGE_KEY)
}

/** Push any legacy localStorage zip codes to the API once, then clear localStorage. */
export async function migrateLegacyIgnoredZipCodes(): Promise<void> {
  const legacy = getLegacyIgnoredZipCodes()
  if (legacy.length === 0) return
  try {
    const list = await api.get<IgnoredZipCode[]>('/ignored-zip-codes')
    const existing = new Set(list.map((z) => normalizeZipCode(z.zip_code)))
    for (const zip of legacy) {
      if (existing.has(normalizeZipCode(zip))) continue
      await api.post<IgnoredZipCode>('/ignored-zip-codes', { zip_code: zip })
    }
    clearLegacyIgnoredZipCodes()
  } catch {
    // Leave localStorage in place so Settings can retry later.
  }
}

/** True when the shipping postal code matches an ignored zip (exact or ZIP+4 prefix). */
export function isIgnoredPostalCode(
  postalCode: unknown,
  ignoredZipCodes: string[]
): boolean {
  if (typeof postalCode !== 'string' || !postalCode.trim() || ignoredZipCodes.length === 0) {
    return false
  }
  const postal = normalizeZipCode(postalCode)
  if (!postal) return false
  return ignoredZipCodes.some((z) => {
    const ignored = normalizeZipCode(z)
    if (!ignored) return false
    return postal === ignored || postal.startsWith(ignored)
  })
}
