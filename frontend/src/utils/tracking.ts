/**
 * Detect carrier from tracking number and return tracking URL.
 * Order of checks matters: more specific patterns first.
 */

export interface TrackingInfo {
  carrier: string
  url: string
}

function normalize(tracking: string): string {
  return (tracking || '').trim().replace(/\s+/g, '')
}

/** UPS: 1Z + 16 alphanumeric; or K/J + 10 digits (ground/express) */
function isUPS(t: string): boolean {
  if (t.length < 9) return false
  if (/^1Z[0-9A-Z]{16}$/i.test(t)) return true
  if (/^[KJ][0-9]{10}$/i.test(t)) return true
  if (/^T\d{3}\d{4}\d{3}$/.test(t)) return true
  return false
}

/** USPS: 20-22 digits (often 94...); 91...; E? + 9 digits + US; 82 + 8; etc. */
function isUSPS(t: string): boolean {
  if (/^9[0-4]\d{18,21}$/.test(t)) return true
  if (/^91\d+$/.test(t) && t.length >= 20 && t.length <= 22) return true
  if (/^[0-9]{20,22}$/.test(t) && !/^96/.test(t)) return true
  if (/^E[A-Z]\d{9}[A-Z]{2}$/i.test(t)) return true
  if (/^[A-Z]{2}\d{9}[A-Z]{2}$/i.test(t)) return true
  if (/^82\d{8}$/.test(t)) return true
  if (/^(03|23|14|70)\d{16,18}$/.test(t)) return true
  return false
}

/** FedEx: 12, 15, 20, 22 digits; 96 + 20; 100 + 31; etc. */
function isFedEx(t: string): boolean {
  if (/^[0-9]{12}$/.test(t)) return true
  if (/^[0-9]{15}$/.test(t)) return true
  if (/^96\d{20}$/.test(t)) return true
  if (/^96\d{22}$/.test(t)) return true
  if (/^100\d{31}$/.test(t)) return true
  if (/^[0-9]{18}$/.test(t)) return true
  return false
}

/** Amazon Logistics: TBA + digits */
function isAmazon(t: string): boolean {
  return /^TBA\d{10,}$/i.test(t) || /^AMZN\d+[A-Z]?$/i.test(t)
}

/** OnTrac: C + 14 digits; D + digits; 13-14 digit numeric (12-digit left to FedEx) */
function isOnTrac(t: string): boolean {
  if (/^C\d{14}$/.test(t)) return true
  if (/^D\d{11,14}$/.test(t)) return true
  if (/^\d{13,14}$/.test(t)) return true
  return false
}

/** LaserShip: 1LS, LS, LX, BN + alphanumeric (often 2 letters + 8 digits) */
function isLaserShip(t: string): boolean {
  if (/^1LS\d{8,}$/i.test(t)) return true
  if (/^LS\d{8,}$/i.test(t)) return true
  if (/^LX\d{8,}$/i.test(t)) return true
  if (/^BN\d{8,}$/i.test(t)) return true
  if (/^[A-Z]{2}\d{8,12}$/i.test(t) && t.length >= 10 && t.length <= 15) return true
  return false
}

const TRACKING_URLS: Record<string, (tn: string) => string> = {
  UPS: (tn) => `https://www.ups.com/track?tracknum=${encodeURIComponent(tn)}`,
  USPS: (tn) => `https://tools.usps.com/go/TrackConfirmAction?tLabels=${encodeURIComponent(tn)}`,
  FedEx: (tn) => `https://www.fedex.com/fedextrack/?trknbr=${encodeURIComponent(tn)}`,
  Amazon: (tn) => `https://www.amazon.com/progress/tracker/${encodeURIComponent(tn)}`,
  OnTrac: (tn) => `https://www.ontrac.com/tracking?tracking_number=${encodeURIComponent(tn)}`,
  LaserShip: (tn) => `https://www.lasership.com/track/${encodeURIComponent(tn)}`,
}

/**
 * Returns carrier name and tracking URL if the tracking number is recognized;
 * otherwise null. Pass the raw tracking string (spaces are stripped).
 */
export function getTrackingInfo(trackingNumber: string): TrackingInfo | null {
  const t = normalize(trackingNumber)
  if (!t) return null
  // Check in order: most specific first. UPS 1Z and K/J are very specific.
  if (isUPS(t)) return { carrier: 'UPS', url: TRACKING_URLS.UPS(t) }
  if (isAmazon(t)) return { carrier: 'Amazon', url: TRACKING_URLS.Amazon(t) }
  if (isLaserShip(t)) return { carrier: 'LaserShip', url: TRACKING_URLS.LaserShip(t) }
  if (isOnTrac(t)) return { carrier: 'OnTrac', url: TRACKING_URLS.OnTrac(t) }
  if (isUSPS(t)) return { carrier: 'USPS', url: TRACKING_URLS.USPS(t) }
  if (isFedEx(t)) return { carrier: 'FedEx', url: TRACKING_URLS.FedEx(t) }
  return null
}
