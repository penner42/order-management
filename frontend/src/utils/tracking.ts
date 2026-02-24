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

export async function getTrackingInfo(trackingNumber: string): Promise<TrackingInfo | null> {
  const t = (trackingNumber || '').trim()
  if (!t) return null

  try {
    const res = await fetch(`${PACKAGE_TRACKING_API_BASE}/link`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ tracking_number: t }),
    })

    if (!res.ok) {
      return null
    }

    const data = (await res.json()) as { carrier: string | null; url: string | null }
    if (!data.carrier || !data.url) return null
    return { carrier: data.carrier, url: data.url }
  } catch {
    return null
  }
}
