export interface TrackingInfo {
  carrier: string | null
  url: string | null
}

declare const importMetaEnv: {
  readonly VITE_PACKAGE_TRACKING_API_BASE?: string
}

const PACKAGE_TRACKING_API_BASE =
  (typeof import.meta !== 'undefined' && (import.meta as any).env?.VITE_PACKAGE_TRACKING_API_BASE) ||
  (typeof importMetaEnv !== 'undefined' && importMetaEnv.VITE_PACKAGE_TRACKING_API_BASE) ||
  '/tracking'

export async function getTrackingInfoBulk(
  trackingNumbers: string[]
): Promise<(TrackingInfo | null)[]> {
  const trimmed = trackingNumbers.map((t) => (t || '').trim()).filter(Boolean)
  if (trimmed.length === 0) return []

  try {
    const res = await fetch(`${PACKAGE_TRACKING_API_BASE}/link`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ tracking_numbers: trimmed }),
    })

    if (!res.ok) {
      return trimmed.map(() => null)
    }

    const data = (await res.json()) as { results: Array<{ carrier: string | null; url: string | null }> }
    const results = data.results ?? []
    return results.map((r) =>
      r && r.carrier && r.url ? { carrier: r.carrier, url: r.url } : null
    )
  } catch {
    return trimmed.map(() => null)
  }
}

export async function getTrackingInfo(trackingNumber: string): Promise<TrackingInfo | null> {
  const t = (trackingNumber || '').trim()
  if (!t) return null
  const [result] = await getTrackingInfoBulk([t])
  return result ?? null
}

interface UpsTestResult {
  ok: boolean
  detail: string
}

export async function testUpsCredentials(): Promise<UpsTestResult> {
  try {
    const res = await fetch(`${PACKAGE_TRACKING_API_BASE}/ups-token`, {
      method: 'GET',
      headers: {
        'Accept': 'application/json',
      },
    })

    if (!res.ok) {
      const err = (await res.json().catch(() => ({ detail: res.statusText }))) as {
        detail?: string
      }
      return {
        ok: false,
        detail: err.detail || 'UPS credentials test failed.',
      }
    }

    const data = (await res.json().catch(() => null)) as {
      access_token?: string
      token_type?: string
    } | null

    if (!data || !data.access_token) {
      return {
        ok: false,
        detail: 'UPS credentials test failed: response was missing an access token.',
      }
    }

    return {
      ok: true,
      detail: 'UPS credentials are valid (UPS OAuth token retrieved successfully).',
    }
  } catch {
    return {
      ok: false,
      detail: 'UPS credentials test failed. Check network logs and UPS_CLIENT/UPS_SECRET values.',
    }
  }
}
