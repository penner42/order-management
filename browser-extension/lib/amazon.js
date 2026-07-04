/* global chrome */

;(function () {
  'use strict'

  const ORDER_ID_IN_URL_RE = /orderI[Dd]=\d{3}-\d{7}-\d{7}/i

  function coerceString(v) {
    if (v == null) return null
    if (typeof v === 'string') return v.trim() || null
    if (typeof v === 'number' || typeof v === 'boolean') return String(v)
    return null
  }

  function normalizeAmazonOrderPayload(raw, sourceUrl, accountEmail) {
    if (!raw || typeof raw !== 'object') {
      throw new Error('Missing Amazon order payload.')
    }

    const orderId = coerceString(raw.orderId)
    if (!orderId) {
      throw new Error('Amazon order structure not recognized (missing order id).')
    }

    const items = []
    const rawItems = Array.isArray(raw.items) ? raw.items : []
    for (let i = 0; i < rawItems.length; i++) {
      const it = rawItems[i] || {}
      const qty = typeof it.quantity === 'number' && it.quantity > 0 ? it.quantity : 1
      const unitPrice = typeof it.unitPrice === 'number' ? it.unitPrice : null
      const lineTotal =
        typeof it.lineTotal === 'number'
          ? it.lineTotal
          : unitPrice != null
            ? unitPrice * qty
            : null

      items.push({
        logicalItemId: coerceString(it.asin) || null,
        externalSku: coerceString(it.asin) || null,
        name: coerceString(it.name) || null,
        productUrl: coerceString(it.productUrl) || null,
        imageUrl: coerceString(it.imageUrl) || null,
        variants: [],
        quantities: { ordered: qty },
        pricing: {
          unitPrice,
          linePrice: lineTotal,
          lineTotal,
          strikethroughPrice: null,
          discounts: [],
        },
        status: {
          rawStatusCode: null,
          normalizedStatus: coerceString(raw.status) || null,
        },
        shipments: [],
        returnability: {
          isReturnable: false,
          returnEligibilityMessage: null,
        },
      })
    }

    const shipments = []
    const rawShipments = Array.isArray(raw.shipments) ? raw.shipments : []
    for (let si = 0; si < rawShipments.length; si++) {
      const s = rawShipments[si] || {}
      const st = s.status || {}
      shipments.push({
        shipmentId: null,
        trackingNumber: coerceString(s.trackingNumber) || null,
        trackingUrl: coerceString(s.trackingUrl) || null,
        deliveryDate: coerceString(s.deliveryDate) || null,
        status: {
          rawStatusType: coerceString(st.rawStatusType) || coerceString(raw.status) || null,
          normalizedStatus: coerceString(st.message) || coerceString(st.rawStatusType) || null,
          message: coerceString(st.message) || null,
        },
      })
    }

    const addr = raw.shippingAddress && typeof raw.shippingAddress === 'object' ? raw.shippingAddress : null
    const shippingAddress = addr
      ? {
          fullName: coerceString(addr.fullName) || null,
          addressLine1: coerceString(addr.addressLine1) || null,
          addressLine2: coerceString(addr.addressLine2) || null,
          city: coerceString(addr.city) || null,
          state: coerceString(addr.state) || null,
          postalCode: coerceString(addr.postalCode) || null,
          country: coerceString(addr.country) || null,
          phoneNumber: null,
        }
      : null

    const totalsRaw = raw.totals && typeof raw.totals === 'object' ? raw.totals : {}
    const totals = {
      subtotal: typeof totalsRaw.subtotal === 'number' ? totalsRaw.subtotal : null,
      grandTotal:
        typeof totalsRaw.grandTotal === 'number'
          ? totalsRaw.grandTotal
          : typeof raw.totalAmount === 'number'
            ? raw.totalAmount
            : null,
    }

    const paymentMethods = []
    const rawPm = Array.isArray(raw.paymentMethods) ? raw.paymentMethods : []
    for (let pi = 0; pi < rawPm.length; pi++) {
      const pm = rawPm[pi] || {}
      paymentMethods.push({
        description: coerceString(pm.description) || null,
        cardType: coerceString(pm.cardType) || null,
        paymentType: null,
        last4: coerceString(pm.last4) || null,
      })
    }

    const externalUrl = sourceUrl || coerceString(raw.detailUrl) || null
    const email = coerceString(accountEmail) || null

    const payload = {
      store: 'amazon',
      source: 'browser-extension',
      capturedAt: new Date().toISOString(),
      externalOrder: {
        id: orderId,
        orderDate: coerceString(raw.orderDate) || null,
        url: externalUrl,
        statusType: coerceString(raw.status) || null,
      },
      customer: {
        email,
      },
      shippingAddress,
      shipments,
      items,
      paymentMethods,
      totals,
    }

    if (typeof totalsRaw.orderDiscount === 'number' && totalsRaw.orderDiscount > 0) {
      payload.orderDiscount = totalsRaw.orderDiscount
    }

    return payload
  }

  function isAmazonHostname(hostname) {
    if (!hostname) return false
    const h = String(hostname).toLowerCase()
    if (h === 'amazon.com') return true
    if (!h.endsWith('.amazon.com')) return false
    const blocked = ['aws.', 'developer.', 'advertising.', 'music.', 'video.', 'photos.']
    for (let i = 0; i < blocked.length; i++) {
      if (h.startsWith(blocked[i])) return false
    }
    return true
  }

  function parseAmazonUrl(url) {
    try {
      return new URL(String(url))
    } catch {
      return null
    }
  }

  function isAmazonOrderDetailUrl(url) {
    if (!url) return false
    const u = parseAmazonUrl(url)
    if (!u || !isAmazonHostname(u.hostname)) return false
    const path = (u.pathname || '').toLowerCase()
    const href = u.href || ''
    if (ORDER_ID_IN_URL_RE.test(href)) return true
    if (path.includes('order-details') || path.includes('orderdetails')) return true
    if (path.includes('/gp/css/summary/print') && /orderid=/i.test(href)) return true
    return false
  }

  function isAmazonOrdersListUrl(url) {
    if (!url) return false
    if (isAmazonOrderDetailUrl(url)) return false
    const u = parseAmazonUrl(url)
    if (!u || !isAmazonHostname(u.hostname)) return false
    const path = (u.pathname || '').toLowerCase()
    const hash = u.hash || ''
    if (path.includes('order-history')) return true
    if (path.includes('/your-orders')) return true
    if (path.includes('/gp/your-account/order')) return true
    if (/^#time\//i.test(hash) || hash.includes('/pagination/')) return true
    return false
  }

  function isAmazonPageUrl(url) {
    if (!url) return false
    const u = parseAmazonUrl(url)
    return !!(u && isAmazonHostname(u.hostname))
  }

  if (typeof globalThis !== 'undefined') {
    globalThis.OrderManagerAmazon = {
      normalizeAmazonOrderPayload,
      isAmazonHostname,
      isAmazonOrderDetailUrl,
      isAmazonOrdersListUrl,
      isAmazonPageUrl,
    }
  }
})()
