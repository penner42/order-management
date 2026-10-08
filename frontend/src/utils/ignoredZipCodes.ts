const STORAGE_KEY = 'ignored_zip_codes'

/** Normalize postal codes for comparison (trim, case, strip spaces/hyphens). */
export function normalizeZipCode(zip: string): string {
  return zip.trim().toUpperCase().replace(/[\s-]/g, '')
}

export function getIgnoredZipCodes(): string[] {
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

export function setIgnoredZipCodes(zipCodes: string[]): void {
  const cleaned = zipCodes.map((z) => z.trim()).filter(Boolean)
  localStorage.setItem(STORAGE_KEY, JSON.stringify(cleaned))
}

/** True when the shipping postal code matches an ignored zip (exact or ZIP+4 prefix). */
export function isIgnoredPostalCode(
  postalCode: unknown,
  ignoredZipCodes: string[] = getIgnoredZipCodes()
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
