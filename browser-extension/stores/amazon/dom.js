;(function () {
  'use strict'

  const ORDER_ID_RE = /\d{3}-\d{7}-\d{7}/
  const ASIN_RE = /\/(?:dp|gp\/product)\/([A-Z0-9]{10})/i
  const EMAIL_RE = /[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/
  const ORDER_DATE_RE =
    /(?:ordered on|order placed|placed on)\s*([A-Za-z]+\s+\d{1,2},\s+\d{4})/i

  const AD_PATTERNS = [
    /amazon\s*visa/i,
    /amazon\s*business.*card/i,
    /prime.*card/i,
    /credit\s*card/i,
  ]

  function getSelectors() {
    return typeof globalThis !== 'undefined' && globalThis.OrderManagerAmazonSelectors
      ? globalThis.OrderManagerAmazonSelectors
      : {}
  }

  function coerceString(v) {
    if (v == null) return null
    if (typeof v === 'string') return v.trim() || null
    if (typeof v === 'number' || typeof v === 'boolean') return String(v)
    return null
  }

  function queryFirst(root, selectorList) {
    if (!root || !selectorList) return null
    const list = Array.isArray(selectorList) ? selectorList : [selectorList]
    for (let i = 0; i < list.length; i++) {
      try {
        const el = root.querySelector(list[i])
        if (el) return el
      } catch {
        // invalid selector (e.g. :has in older browsers)
      }
    }
    return null
  }

  function queryAllFirst(root, selectorList) {
    if (!root || !selectorList) return []
    const list = Array.isArray(selectorList) ? selectorList : [selectorList]
    for (let i = 0; i < list.length; i++) {
      try {
        const nodes = root.querySelectorAll(list[i])
        if (nodes && nodes.length > 0) return Array.from(nodes)
      } catch {
        // ignore
      }
    }
    return []
  }

  function textOf(el) {
    if (!el) return ''
    return (el.textContent || '').replace(/\s+/g, ' ').trim()
  }

  function extractOrderIdFromText(text) {
    const m = coerceString(text) && ORDER_ID_RE.exec(text)
    return m ? m[0] : null
  }

  function extractOrderIdFromUrl(url) {
    if (!url) return null
    const m = /orderI[Dd]=([\d-]+)/i.exec(String(url))
    if (m && m[1] && ORDER_ID_RE.test(m[1])) return m[1]
    return extractOrderIdFromText(url)
  }

  function extractAsinFromUrl(href) {
    if (!href) return null
    const m = ASIN_RE.exec(String(href))
    return m ? m[1].toUpperCase() : null
  }

  function parsePrice(text) {
    const s = coerceString(text)
    if (!s) return null
    const cleaned = s.replace(/[^0-9.,-]/g, '').replace(/,/g, '')
    if (!cleaned) return null
    const n = parseFloat(cleaned)
    return Number.isFinite(n) ? n : null
  }

  function extractPriceFromText(text) {
    const s = coerceString(text) || ''
    const patterns = [
      /\$\s*([0-9]+(?:[.,][0-9]{2})?)/,
      /([0-9]+(?:[.,][0-9]{2})?)\s*USD/i,
      /USD\s*([0-9]+(?:[.,][0-9]{2})?)/i,
    ]
    for (let i = 0; i < patterns.length; i++) {
      const m = patterns[i].exec(s)
      if (m && m[1]) {
        const p = parsePrice(m[1])
        if (p != null) return p
      }
    }
    return null
  }

  function parseOrderDate(text) {
    const s = coerceString(text) || ''
    const m = ORDER_DATE_RE.exec(s)
    if (m && m[1]) return m[1].trim()
    const elMatch = s.match(/[A-Za-z]+\s+\d{1,2},\s+\d{4}/)
    return elMatch ? elMatch[0] : null
  }

  function absoluteUrl(href) {
    if (!href) return null
    try {
      return new URL(href, window.location.origin).toString()
    } catch {
      return null
    }
  }

  function isAmazonOrderListPage() {
    const am = typeof globalThis !== 'undefined' ? globalThis.OrderManagerAmazon : null
    if (am && typeof am.isAmazonOrdersListUrl === 'function') {
      return am.isAmazonOrdersListUrl(window.location.href)
    }
    try {
      const path = window.location.pathname || ''
      const href = window.location.href || ''
      return path.includes('/your-orders') || path.includes('order-history')
    } catch {
      return false
    }
  }

  function isAmazonOrderDetailPage() {
    const am = typeof globalThis !== 'undefined' ? globalThis.OrderManagerAmazon : null
    if (am && typeof am.isAmazonOrderDetailUrl === 'function') {
      return am.isAmazonOrderDetailUrl(window.location.href)
    }
    try {
      const href = window.location.href || ''
      return /order-details|orderI[Dd]=/.test(href)
    } catch {
      return false
    }
  }

  function findOrderCards() {
    const sel = getSelectors()
    const cards = queryAllFirst(document, sel.ORDER_CARD)
    if (cards.length > 0) return cards

    const potential = new Set()
    document.querySelectorAll('*').forEach((el) => {
      if (!el.textContent || !ORDER_ID_RE.test(el.textContent)) return
      let parent = el
      for (let i = 0; i < 10 && parent && parent.parentElement; i++) {
        parent = parent.parentElement
        if (
          parent.classList.contains('a-box') ||
          parent.classList.contains('a-box-group') ||
          parent.classList.contains('order-card') ||
          (parent.tagName === 'DIV' && parent.children.length > 2)
        ) {
          potential.add(parent)
          break
        }
      }
    })
    return Array.from(potential)
  }

  function isAdvertisementCard(summary) {
    if (!summary) return true
    if (!summary.orderId) return true
    if (!summary.detailUrl) {
      if (!summary.orderDate) {
        const titles = (summary.items || []).map((it) => it.name || '').join(' ')
        if (AD_PATTERNS.some((p) => p.test(titles))) return true
      }
    }
    return false
  }

  function shouldSkipOrderCard(card) {
    if (queryFirst(card, '.brand-info-box .brand-logo img')) return true
    const wf = card.querySelector('a.yohtmlc-order-details-link[href^="/wholefoodsmarket"]')
    if (wf) return true
    const statusEl = queryFirst(card, getSelectors().ORDER_STATUS)
    const statusText = textOf(statusEl)
    if (statusText === 'Purchased at Amazon') return true
    return false
  }

  function parseListOrderCard(card) {
    if (shouldSkipOrderCard(card)) return null

    const sel = getSelectors()
    const cardText = textOf(card)

    let orderId = extractOrderIdFromText(textOf(queryFirst(card, sel.ORDER_ID)))
    if (!orderId) orderId = extractOrderIdFromText(cardText)

    const detailLink = queryFirst(card, sel.ORDER_DETAIL_LINK)
    let detailUrl = detailLink ? absoluteUrl(detailLink.getAttribute('href')) : null
    if (!orderId && detailUrl) orderId = extractOrderIdFromUrl(detailUrl)

    const orderDate =
      parseOrderDate(textOf(queryFirst(card, sel.ORDER_DATE))) || parseOrderDate(cardText)
    const totalAmount = extractPriceFromText(textOf(queryFirst(card, sel.ORDER_TOTAL))) ||
      extractPriceFromText(cardText)
    const status = textOf(queryFirst(card, sel.ORDER_STATUS))

    const items = []
    const seenAsins = new Set()
    card.querySelectorAll('a[href*="/dp/"], a[href*="/gp/product/"]').forEach((link) => {
      const href = link.getAttribute('href') || ''
      const asin = extractAsinFromUrl(href)
      if (!asin || seenAsins.has(asin)) return
      seenAsins.add(asin)
      let title = textOf(link)
      if (!title || title.length < 3) {
        const img = link.querySelector('img')
        if (img && img.alt) title = img.alt.trim()
      }
      items.push({
        asin,
        name: title || null,
        productUrl: absoluteUrl(href),
      })
    })

    const summary = {
      orderId: orderId || null,
      orderDate: orderDate || null,
      totalAmount: totalAmount,
      status: status || null,
      detailUrl: detailUrl || null,
      items,
    }

    if (isAdvertisementCard(summary)) return null
    if (!summary.orderId || !summary.detailUrl) return null
    return summary
  }

  function parseOrderListPage() {
    const cards = findOrderCards()
    const orders = []
    const seen = new Set()
    for (let i = 0; i < cards.length; i++) {
      const parsed = parseListOrderCard(cards[i])
      if (!parsed || !parsed.orderId || seen.has(parsed.orderId)) continue
      seen.add(parsed.orderId)
      orders.push(parsed)
    }
    return orders
  }

  function splitHtmlLines(el) {
    if (!el) return []
    const html = el.innerHTML || ''
    if (!html || !/<br/i.test(html)) {
      const t = textOf(el)
      return t ? [t] : []
    }
    return html
      .split(/<br\s*\/?>/gi)
      .map((part) => {
        const tmp = document.createElement('div')
        tmp.innerHTML = part
        return textOf(tmp)
      })
      .filter(Boolean)
  }

  function parseAddressFromHorizonteComponent(root) {
    const addrRoot = root.querySelector('[data-component="shippingAddress"]')
    if (!addrRoot) return null

    const listItems = addrRoot.querySelectorAll('ul li')
    if (listItems.length < 2) return null

    const fullName = textOf(listItems[0].querySelector('.a-list-item') || listItems[0])
    const addrLines = splitHtmlLines(
      listItems[1].querySelector('.a-list-item') || listItems[1]
    )
    const country =
      listItems.length > 2
        ? textOf(listItems[2].querySelector('.a-list-item') || listItems[2])
        : null

    if (!fullName && addrLines.length === 0) return null

    let city = null
    let state = null
    let postalCode = null
    let line1 = null
    let line2 = null

    const cityIdx = addrLines.findIndex((line) => /,\s*[A-Z]{2}\s+\d{5}/.test(line))
    if (cityIdx >= 0) {
      const m = /^(.+?),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)/.exec(addrLines[cityIdx])
      if (m) {
        city = m[1].trim()
        state = m[2]
        postalCode = m[3]
      }
      line1 = addrLines[0] || null
      if (cityIdx > 1) {
        line2 = addrLines.slice(1, cityIdx).join(', ') || null
      }
    } else if (addrLines.length > 0) {
      line1 = addrLines[0]
      line2 = addrLines.length > 1 ? addrLines.slice(1).join(', ') : null
    }

    return {
      fullName: fullName || null,
      addressLine1: line1,
      addressLine2: line2,
      city,
      state,
      postalCode,
      country: country || null,
    }
  }

  function parseAddressRoot(root) {
    if (!root) return null
    const sel = getSelectors()
    const addrRoot = queryFirst(root, sel.SHIPPING_ADDRESS) || root
    const fullName = textOf(queryFirst(addrRoot, sel.ADDRESS_NAME))
    const line1 = textOf(queryFirst(addrRoot, sel.ADDRESS_LINE1))
    const line2 = textOf(queryFirst(addrRoot, sel.ADDRESS_LINE2))
    const cityState = textOf(queryFirst(addrRoot, sel.ADDRESS_CITY))
    const country = textOf(queryFirst(addrRoot, sel.ADDRESS_COUNTRY))

    let city = null
    let state = null
    let postalCode = null
    if (cityState) {
      const m = /^(.+?),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)/.exec(cityState)
      if (m) {
        city = m[1].trim()
        state = m[2]
        postalCode = m[3]
      } else {
        city = cityState
      }
    }

    if (!fullName && !line1) return null
    return {
      fullName: fullName || null,
      addressLine1: line1 || null,
      addressLine2: line2 || null,
      city,
      state,
      postalCode,
      country: country || null,
    }
  }

  function parseAddressFromPopover(root) {
    const sel = getSelectors()
    const popoverEls = queryAllFirst(root, sel.ADDRESS_POPOVER)
    for (let i = 0; i < popoverEls.length; i++) {
      const raw = popoverEls[i].getAttribute('data-a-popover')
      if (!raw) continue
      try {
        const data = JSON.parse(raw)
        const html = data.inlineContent || data.content || ''
        if (!html) continue
        const doc = new DOMParser().parseFromString(html, 'text/html')
        const parsed = parseAddressRoot(doc.body)
        if (parsed && (parsed.fullName || parsed.addressLine1)) return parsed
      } catch {
        // ignore malformed popover JSON
      }
    }
    return null
  }

  function parseAddressFromShipToScript(root) {
    const scripts = root.querySelectorAll('script[id^="shipToData"]')
    for (let i = 0; i < scripts.length; i++) {
      const html = scripts[i].textContent || scripts[i].innerHTML || ''
      if (!html.trim()) continue
      try {
        const doc = new DOMParser().parseFromString(html, 'text/html')
        const parsed = parseAddressRoot(doc.body)
        if (parsed && (parsed.fullName || parsed.addressLine1)) return parsed
      } catch {
        // ignore
      }
    }
    return null
  }

  function parseAddressFromRecipientText(root) {
    const horizonte = parseAddressFromHorizonteComponent(root)
    if (horizonte) return horizonte

    const recipient = root.querySelector('div.recipient, [data-component="shippingAddress"]')
    if (!recipient) return null
    const lines = (recipient.innerText || recipient.textContent || '')
      .split(/\n/)
      .map((line) => line.replace(/\s+/g, ' ').trim())
      .filter(Boolean)
    while (lines.length > 0 && /^ship\s*to$/i.test(lines[0])) {
      lines.shift()
    }
    if (lines.length < 2) return null

    const cityLine = lines.find((line) => /,\s*[A-Z]{2}\s+\d{5}/.test(line))
    let city = null
    let state = null
    let postalCode = null
    if (cityLine) {
      const m = /^(.+?),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)/.exec(cityLine)
      if (m) {
        city = m[1].trim()
        state = m[2]
        postalCode = m[3]
      }
    }

    const cityIdx = cityLine ? lines.indexOf(cityLine) : -1
    const name = lines[0]
    const line1 = lines.length > 1 ? lines[1] : null
    const line2 = cityIdx > 2 ? lines.slice(2, cityIdx).join(', ') || null : null
    const country = cityIdx >= 0 && cityIdx < lines.length - 1 ? lines[lines.length - 1] : null

    if (!name && !line1) return null
    return {
      fullName: name || null,
      addressLine1: line1 || null,
      addressLine2: line2,
      city,
      state,
      postalCode,
      country,
    }
  }

  function resolveShippingAddress(root) {
    let addr = parseAddressFromHorizonteComponent(root)
    if (addr && (addr.fullName || addr.addressLine1)) return addr
    addr = parseAddressRoot(root)
    if (addr && (addr.fullName || addr.addressLine1)) return addr
    addr = parseAddressFromPopover(root)
    if (addr) return addr
    addr = parseAddressFromShipToScript(root)
    if (addr) return addr
    return parseAddressFromRecipientText(root)
  }

  function findDetailItemContainers(root) {
    const sel = getSelectors()
    let containers = queryAllFirst(root, sel.ITEM_ROOT)

    if (containers.length === 1) {
      const only = containers[0]
      const inner = only.querySelectorAll('.yohtmlc-item, .item-box, .a-fixed-left-grid')
      if (inner.length > 1) containers = Array.from(inner)
    }

    containers = containers.filter(
      (el, _idx, arr) => !arr.some((other) => other !== el && other.contains(el))
    )

    if (containers.length === 0) {
      const seen = new Set()
      root.querySelectorAll('a[href*="/dp/"], a[href*="/gp/product/"]').forEach((link) => {
        let parent = link.closest('.yohtmlc-item, .item-box, .a-fixed-left-grid, .a-row')
        if (!parent) parent = link.parentElement
        if (parent && !seen.has(parent)) {
          seen.add(parent)
          containers.push(parent)
        }
      })
    }

    return containers
  }

  function parseDetailItems(root, seen, itemCounter) {
    const sel = getSelectors()
    const itemContainers = findDetailItemContainers(root)
    const items = []
    const seenKeys = seen || new Set()
    const counter = itemCounter || { n: 0 }

    function pushItem(container) {
      const link =
        container.querySelector('a[href*="/dp/"], a[href*="/gp/product/"]') ||
        queryFirst(container, sel.ITEM_TITLE)
      const href = link ? link.getAttribute('href') || '' : ''
      const asin = extractAsinFromUrl(href)

      let name = textOf(queryFirst(container, sel.ITEM_TITLE)) || textOf(link)
      if (!name || name.length < 3) {
        const img = container.querySelector('img')
        if (img && img.alt) name = img.alt.trim()
      }

      const index = counter.n
      const key = asin ? `${asin}:${index}` : `${name || 'item'}:${index}`
      if ((!asin && (!name || name.length < 3)) || seenKeys.has(key)) return
      seenKeys.add(key)
      counter.n += 1

      let qty = 1
      const qtyEl = queryFirst(container, sel.ITEM_QTY)
      if (qtyEl) {
        const q = parseInt(textOf(qtyEl), 10)
        if (Number.isFinite(q) && q > 0) qty = q
      } else {
        const qm = textOf(container).match(/(?:Qty|Quantity)[:\s]*(\d+)/i)
        if (qm) qty = parseInt(qm[1], 10) || 1
      }

      const unitPrice =
        extractPriceFromText(textOf(queryFirst(container, sel.ITEM_PRICE))) ||
        extractPriceFromText(textOf(container))

      items.push({
        asin: asin || null,
        name: name || null,
        productUrl: href ? absoluteUrl(href) : null,
        imageUrl: (container.querySelector('img') || {}).src || null,
        quantity: qty,
        unitPrice,
        lineTotal: unitPrice != null ? unitPrice * qty : null,
      })
    }

    if (itemContainers.length > 0) {
      itemContainers.forEach((container) => pushItem(container))
    }

    return items
  }

  function parseDetailItemsFromProductLinks(root, seenKeys) {
    const items = []
    const seen = seenKeys || new Set()
    const scope = root && root.querySelectorAll ? root : document
    scope.querySelectorAll('a[href*="/dp/"], a[href*="/gp/product/"]').forEach((link) => {
      const href = link.getAttribute('href') || ''
      const asin = extractAsinFromUrl(href)
      if (!asin || seen.has(asin)) return
      seen.add(asin)
      let name = textOf(link)
      if (!name || name.length < 3) {
        const img = link.querySelector('img')
        if (img && img.alt) name = img.alt.trim()
      }
      if (!name || name.length < 3) {
        const row = link.closest('.yohtmlc-item, .item-box, .a-fixed-left-grid, .a-row, [data-component="itemTitle"]')
        if (row) name = textOf(row.querySelector('[data-component="itemTitle"]')) || textOf(row)
      }
      items.push({
        asin,
        name: name || null,
        productUrl: absoluteUrl(href),
        imageUrl: (link.querySelector('img') || {}).src || null,
        quantity: 1,
        unitPrice: null,
        lineTotal: null,
      })
    })
    return items
  }

  function withDisableCsdUrl(url) {
    const am = globalThis.OrderManagerAmazon
    if (am && typeof am.withDisableCsdParam === 'function') {
      return am.withDisableCsdParam(url)
    }
    try {
      const u = new URL(String(url))
      if (!u.searchParams.has('disableCsd')) {
        u.searchParams.set('disableCsd', 'missing-library')
      }
      return u.toString()
    } catch {
      return url
    }
  }

  function isLikelyCarrierTrackingNumber(id) {
    const s = coerceString(id)
    if (!s) return false
    if (/^TBA\d/i.test(s)) return true
    if (/^1Z[A-Z0-9]{10,}$/i.test(s)) return true
    if (/^\d{10,22}$/.test(s)) return true
    if (/^[A-Z]{2}\d{9}[A-Z]{2}$/i.test(s)) return true
    if (s.length >= 12 && /\d/.test(s) && /[A-Za-z]/.test(s)) return true
    if (s.length <= 12 && /^[A-Za-z0-9]+$/.test(s) && !/^\d+$/.test(s)) return false
    return s.length >= 10
  }

  function isAmazonTrackingPageUrl(url) {
    if (!url) return false
    return /ship-track|progress\/tracker|track\.amazon/i.test(String(url))
  }

  function needsTrackingPageLookup(shipment) {
    if (!shipment || !shipment.trackingUrl) return false
    if (!isAmazonTrackingPageUrl(shipment.trackingUrl)) return false
    if (!shipment.trackingNumber) return true
    return !isLikelyCarrierTrackingNumber(shipment.trackingNumber)
  }

  function extractTrackingFromUrl(url) {
    if (!url) return null
    const trackingIdMatch = /[?&]trackingId=([^&]+)/i.exec(String(url))
    if (trackingIdMatch && trackingIdMatch[1]) {
      const id = decodeURIComponent(trackingIdMatch[1])
      if (isLikelyCarrierTrackingNumber(id)) return id
    }
    const trackerMatch = /tracker\/([^/?]+)/i.exec(String(url))
    if (trackerMatch && trackerMatch[1]) {
      const id = decodeURIComponent(trackerMatch[1])
      if (isLikelyCarrierTrackingNumber(id)) return id
    }
    return null
  }

  function extractCarrierFromTrackingRoot(root) {
    const sel = getSelectors()
    const carrierEl = queryFirst(root, sel.TRACKING_PAGE_CARRIER)
    if (carrierEl) {
      const m = /Shipped with\s+(.+)/i.exec(textOf(carrierEl))
      if (m && m[1]) return m[1].trim()
    }
    return null
  }

  function parseTrackingPageHtml(html) {
    if (!html) return null

    const jsonMatch = /"trackingId"\s*:\s*"([^"]+)"/.exec(html)
    if (jsonMatch && jsonMatch[1] && isLikelyCarrierTrackingNumber(jsonMatch[1])) {
      return {
        trackingNumber: jsonMatch[1],
        carrier: null,
      }
    }

    try {
      const doc = new DOMParser().parseFromString(html, 'text/html')
      const sel = getSelectors()
      const idEl = queryFirst(doc, sel.TRACKING_PAGE_ID)
      if (idEl) {
        const m = /Tracking\s*ID\s*:\s*(\S+)/i.exec(textOf(idEl))
        if (m && m[1] && isLikelyCarrierTrackingNumber(m[1])) {
          return {
            trackingNumber: m[1],
            carrier: extractCarrierFromTrackingRoot(doc),
          }
        }
      }

      const bodyText = doc.body ? doc.body.textContent || '' : html
      const textMatch = /Tracking\s*ID\s*:\s*(\S+)/i.exec(bodyText)
      if (textMatch && textMatch[1] && isLikelyCarrierTrackingNumber(textMatch[1])) {
        return {
          trackingNumber: textMatch[1].trim(),
          carrier: extractCarrierFromTrackingRoot(doc),
        }
      }
    } catch {
      // ignore
    }

    return null
  }

  async function fetchTrackingPageDetails(trackingUrl, baseOrigin) {
    const origin = baseOrigin || window.location.origin
    let url = coerceString(trackingUrl)
    if (!url) return null
    if (url.startsWith('/')) url = origin + url
    url = withDisableCsdUrl(url)

    try {
      const resp = await fetch(url, { credentials: 'include' })
      if (!resp.ok) return null
      const html = await resp.text()
      return parseTrackingPageHtml(html)
    } catch {
      return null
    }
  }

  async function enrichShipmentsWithTracking(shipments, baseOrigin) {
    if (!Array.isArray(shipments) || shipments.length === 0) return shipments
    const origin = baseOrigin || window.location.origin
    const cache = new Map()

    for (let i = 0; i < shipments.length; i++) {
      const shipment = shipments[i]
      if (!needsTrackingPageLookup(shipment)) continue

      const cacheKey = String(shipment.trackingUrl)
      let details = cache.get(cacheKey)
      if (details === undefined) {
        details = await fetchTrackingPageDetails(shipment.trackingUrl, origin)
        cache.set(cacheKey, details)
      }

      if (details && details.trackingNumber) {
        shipment.trackingNumber = details.trackingNumber
      }
    }

    return shipments
  }

  function parseTrackingFromBlock(block) {
    const sel = getSelectors()
    const links = []
    queryAllFirst(block, sel.TRACKING_LINK).forEach((a) => links.push(a))
    block
      .querySelectorAll(
        'a[href*="ship-track"], a[href*="progress/tracker"], a[href*="trackingId="], a[href*="track.amazon"]'
      )
      .forEach((a) => {
        if (!links.includes(a)) links.push(a)
      })

    const results = []
    const seen = new Set()
    links.forEach((a) => {
      const url = absoluteUrl(a.getAttribute('href'))
      if (!url || seen.has(url)) return
      seen.add(url)

      let trackingNumber = extractTrackingFromUrl(url)
      if (!trackingNumber) {
        const tn = /\b(\d{9,22})\b/.exec(textOf(a))
        if (tn) trackingNumber = tn[1]
      }

      results.push({ trackingNumber, trackingUrl: url })
    })
    return results
  }

  function extractShipmentIdFromUrl(url) {
    if (!url) return null
    const m = /[?&]shipmentId=([^&]+)/i.exec(String(url))
    return m && m[1] ? decodeURIComponent(m[1]) : null
  }

  function buildShipmentId(entry, index) {
    const fromUrl = extractShipmentIdFromUrl(entry && entry.trackingUrl)
    if (fromUrl) return fromUrl
    const tracking = coerceString(entry && entry.trackingNumber)
    if (tracking) return `track:${tracking}`
    return `shipment-${index}`
  }

  function parseShipmentBlock(block, seenItemKeys, itemCounter, blockIndex) {
    const sel = getSelectors()
    const entries = parseShipmentEntries(block)
    const blockItems = parseDetailItems(block, seenItemKeys, itemCounter)
    if (entries.length === 0 && blockItems.length === 0) return null

    const entry = entries[0] || {
      trackingNumber: null,
      trackingUrl: null,
      deliveryDate: null,
      status: {
        rawStatusType: textOf(queryFirst(block, sel.ORDER_STATUS)),
        message: textOf(queryFirst(block, sel.ORDER_STATUS)),
      },
    }
    const shipmentId = buildShipmentId(entry, blockIndex)

    return {
      shipment: {
        shipmentId,
        trackingNumber: entry.trackingNumber,
        trackingUrl: entry.trackingUrl,
        deliveryDate: entry.deliveryDate,
        status: entry.status,
      },
      items: blockItems.map((item) => ({
        ...item,
        shipmentId,
      })),
    }
  }

  function parseShipmentEntries(block) {
    const sel = getSelectors()
    const status = textOf(queryFirst(block, sel.ORDER_STATUS))
    const tracking = parseTrackingFromBlock(block)

    if (tracking.length > 0) {
      return tracking.map((t) => ({
        trackingNumber: t.trackingNumber,
        trackingUrl: t.trackingUrl,
        deliveryDate: null,
        status: { rawStatusType: status, message: status },
      }))
    }

    if (status) {
      return [
        {
          trackingNumber: null,
          trackingUrl: null,
          deliveryDate: null,
          status: { rawStatusType: status, message: status },
        },
      ]
    }
    return []
  }

  function findShipmentRoots(root) {
    const sel = getSelectors()
    const blocks = queryAllFirst(root, sel.SHIPMENT_ROOT)
    if (blocks.length > 0) return blocks
    return [root]
  }

  function dedupeShipments(shipments) {
    const out = []
    const seen = new Set()
    shipments.forEach((s, idx) => {
      const key =
        s.trackingUrl ||
        s.trackingNumber ||
        `idx:${idx}:${JSON.stringify(s.status || {})}`
      if (seen.has(key)) return
      seen.add(key)
      out.push(s)
    })
    return out
  }

  function parseShipments(root) {
    const shipmentRoots = findShipmentRoots(root)
    const all = []
    shipmentRoots.forEach((block) => {
      parseShipmentEntries(block).forEach((entry) => all.push(entry))
    })
    if (all.length === 0) {
      parseShipmentEntries(root).forEach((entry) => all.push(entry))
    }
    return dedupeShipments(all)
  }

  function parsePaymentMethods(root) {
    const sel = getSelectors()
    const methods = []
    root.querySelectorAll(sel.PAYMENT_LOGO.join(',')).forEach((img) => {
      const alt = coerceString(img.getAttribute('alt'))
      let last4 = null
      let parent = img.parentElement
      for (let i = 0; i < 3 && parent; i++) {
        const m = /\b(\d{4})\b/.exec(textOf(parent))
        if (m) {
          last4 = m[1]
          break
        }
        parent = parent.parentElement
      }
      methods.push({
        description: alt,
        cardType: alt,
        last4,
      })
    })
    return methods
  }

  function parseTotals(root) {
    const sel = getSelectors()
    let subtotal = null
    let grandTotal = null
    let orderDiscount = null

    queryAllFirst(root, sel.SUBTOTAL_ROWS).forEach((row) => {
      const t = textOf(row)
      const amount = extractPriceFromText(t)
      if (amount == null) return
      if (/grand\s*total/i.test(t)) grandTotal = amount
      else if (/subtotal|items/i.test(t) && subtotal == null) subtotal = amount
      else if (/discount|coupon|promotion|savings/i.test(t)) {
        orderDiscount = (orderDiscount || 0) + amount
      }
    })

    return { subtotal, grandTotal, orderDiscount }
  }

  function parseOrderDetailPage(scope, pageUrlOverride) {
    const sel = getSelectors()
    const doc =
      scope && scope.nodeType === 9
        ? scope
        : scope && scope.ownerDocument
          ? scope.ownerDocument
          : document
    const root =
      scope && scope.nodeType === 1
        ? scope
        : queryFirst(doc, sel.ORDER_DETAILS_ROOT) || doc.body || doc.documentElement
    const pageUrl =
      pageUrlOverride ||
      (doc.defaultView && doc.defaultView.location ? doc.defaultView.location.href : window.location.href)
    const rootText = textOf(root)

    let orderId = extractOrderIdFromText(textOf(queryFirst(root, sel.ORDER_ID)))
    if (!orderId) orderId = extractOrderIdFromUrl(pageUrl)
    if (!orderId) orderId = extractOrderIdFromText(rootText)
    if (!orderId) return null

    const orderDate =
      parseOrderDate(textOf(queryFirst(root, sel.ORDER_DATE))) || parseOrderDate(rootText)
    const status = textOf(queryFirst(root, sel.ORDER_STATUS))
    const seenItemKeys = new Set()
    const itemCounter = { n: 0 }
    const shipmentRoots = findShipmentRoots(root)
    const items = []
    const shipments = []
    let blockIndex = 0

    shipmentRoots.forEach((block) => {
      const parsedBlock = parseShipmentBlock(block, seenItemKeys, itemCounter, blockIndex)
      if (!parsedBlock) return
      blockIndex += 1
      shipments.push(parsedBlock.shipment)
      parsedBlock.items.forEach((item) => items.push(item))
    })

    if (items.length === 0) {
      parseDetailItems(root, seenItemKeys, itemCounter).forEach((item) => items.push(item))
    }
    if (items.length === 0) {
      parseDetailItemsFromProductLinks(root, seenItemKeys).forEach((item) => items.push(item))
    }
    if (shipments.length === 0) {
      parseShipments(root).forEach((entry, idx) => {
        shipments.push({
          shipmentId: buildShipmentId(entry, idx),
          ...entry,
        })
      })
    }
    const shippingAddress = resolveShippingAddress(root)
    const paymentMethods = parsePaymentMethods(root)
    const totals = parseTotals(root)

    if (totals.grandTotal == null) {
      totals.grandTotal = extractPriceFromText(textOf(queryFirst(root, sel.ORDER_TOTAL)))
    }

    return {
      orderId,
      orderDate,
      status,
      detailUrl: pageUrl,
      items,
      shippingAddress,
      shipments,
      paymentMethods,
      totals,
    }
  }

  function hasNextPage() {
    const sel = getSelectors()
    const next = queryFirst(document, sel.NEXT_PAGE)
    return !!next
  }

  function getNextPageUrl() {
    const sel = getSelectors()
    const next = queryFirst(document, sel.NEXT_PAGE)
    if (!next) return null
    return absoluteUrl(next.getAttribute('href'))
  }

  function findCsdEncryptedContainers(root) {
    const scope = root && root.querySelectorAll ? root : document
    const out = []
    const seen = new Set()
    scope.querySelectorAll('[class*="csd-encrypted"], [id*="csd-encrypted"]').forEach((el) => {
      if (!seen.has(el)) {
        seen.add(el)
        out.push(el)
      }
    })
    return out
  }

  function elementHasCsdDecryptScript(el) {
    if (!el) return false
    const scripts = el.querySelectorAll('script')
    for (let i = 0; i < scripts.length; i++) {
      const src = scripts[i].textContent || scripts[i].innerHTML || ''
      if (/SiegeClientSideDecryption|decryptInElementWithId/i.test(src)) return true
    }
    return false
  }

  function hasPendingCsdEncryption(root) {
    const containers = findCsdEncryptedContainers(root || document)
    for (let i = 0; i < containers.length; i++) {
      const el = containers[i]
      if (!elementHasCsdDecryptScript(el)) continue

      const text = textOf(el)
      const hasOrderId = ORDER_ID_RE.test(text)
      const hasProductLink = !!el.querySelector('a[href*="/dp/"], a[href*="/gp/product/"]')
      const hasPrice = extractPriceFromText(text) != null
      if (!hasOrderId && !hasProductLink && !hasPrice && text.length < 40) return true
    }
    return false
  }

  function orderIdVisibleInDom(root) {
    const sel = getSelectors()
    const scope = root && root.querySelectorAll ? root : document
    const detailsRoot = queryFirst(scope, sel.ORDER_DETAILS_ROOT) || scope.body || scope
    if (ORDER_ID_RE.test(textOf(queryFirst(detailsRoot, sel.ORDER_ID)))) return true
    if (ORDER_ID_RE.test(textOf(detailsRoot))) return true
    return false
  }

  function isOrderListContentReady() {
    if (hasPendingCsdEncryption(document)) return false

    const cards = findOrderCards()
    if (cards.length === 0) return false

    for (let i = 0; i < cards.length; i++) {
      const card = cards[i]
      const cardText = textOf(card)
      if (!ORDER_ID_RE.test(cardText)) continue
      if (card.querySelector('a[href*="order-details"], a[href*="orderID="], a[href*="orderId="]')) {
        return true
      }
      if (card.querySelector('a[href*="/dp/"], a[href*="/gp/product/"]')) return true
      if (extractPriceFromText(cardText) != null) return true
      if (textOf(queryFirst(card, getSelectors().ORDER_STATUS)).length > 2) return true
    }
    return false
  }

  function isOrderDetailContentReadyInDocument(doc, pageUrl) {
    if (!doc) return false
    if (hasPendingCsdEncryption(doc)) return false

    if (doc.querySelector('a[href*="/dp/"], a[href*="/gp/product/"]')) {
      const urlOrderId = extractOrderIdFromUrl(pageUrl || '')
      if (urlOrderId && ORDER_ID_RE.test(urlOrderId)) return true
    }

    const parsed = parseOrderDetailPage(doc, pageUrl)
    if (!parsed || !parsed.orderId) return false

    if (parsed.items && parsed.items.some((it) => (it.name && it.name.length > 3) || it.asin)) {
      return true
    }
    if (parsed.totals && (parsed.totals.grandTotal != null || parsed.totals.subtotal != null)) {
      return true
    }
    if (
      parsed.shippingAddress &&
      (parsed.shippingAddress.fullName || parsed.shippingAddress.addressLine1)
    ) {
      return true
    }
    if (parsed.status && parsed.status.length > 2) return true
    if (
      parsed.shipments &&
      parsed.shipments.some((s) => (s.status && s.status.message) || s.trackingUrl || s.trackingNumber)
    ) {
      return true
    }
    return orderIdVisibleInDom(doc)
  }

  function waitForDecryptedContentInDocument(doc, checkFn, timeoutMs) {
    const timeout = typeof timeoutMs === 'number' && timeoutMs > 0 ? timeoutMs : 20000
    return new Promise((resolve, reject) => {
      const observers = []
      let timeoutTimer = null
      let settled = false

      function finish(ok) {
        if (settled) return
        settled = true
        observers.forEach((obs) => {
          try {
            obs.disconnect()
          } catch {
            // ignore
          }
        })
        if (timeoutTimer != null) clearTimeout(timeoutTimer)
        if (ok) resolve(true)
        else reject(new Error('Timed out waiting for Amazon page content to decrypt.'))
      }

      function check() {
        try {
          if (checkFn()) {
            finish(true)
            return true
          }
        } catch {
          // ignore
        }
        return false
      }

      if (check()) return

      const observerOptions = {
        childList: true,
        subtree: true,
        characterData: true,
        attributes: true,
      }

      const attachObserver = (target) => {
        if (!target) return
        const observer = new MutationObserver(() => {
          check()
        })
        observer.observe(target, observerOptions)
        observers.push(observer)
      }

      attachObserver(doc.documentElement || doc.body)
      findCsdEncryptedContainers(doc).forEach((el) => attachObserver(el))

      timeoutTimer = setTimeout(() => {
        if (!settled) finish(false)
      }, timeout)
    })
  }

  function waitForOrderDetailReadyInDocument(doc, pageUrl, timeoutMs) {
    const timeout = typeof timeoutMs === 'number' && timeoutMs > 0 ? timeoutMs : 35000
    return waitForDecryptedContentInDocument(
      doc,
      () => isOrderDetailContentReadyInDocument(doc, pageUrl),
      timeout
    )
  }

  function isOrderDetailContentReady() {
    if (hasPendingCsdEncryption(document)) return false

    if (document.querySelector('a[href*="/dp/"], a[href*="/gp/product/"]')) {
      const urlOrderId = extractOrderIdFromUrl(window.location.href)
      if (urlOrderId && ORDER_ID_RE.test(urlOrderId)) return true
    }

    const parsed = parseOrderDetailPage()
    if (!parsed || !parsed.orderId) return false

    if (parsed.items && parsed.items.some((it) => (it.name && it.name.length > 3) || it.asin)) {
      return true
    }
    if (parsed.totals && (parsed.totals.grandTotal != null || parsed.totals.subtotal != null)) {
      return true
    }
    if (
      parsed.shippingAddress &&
      (parsed.shippingAddress.fullName || parsed.shippingAddress.addressLine1)
    ) {
      return true
    }
    if (parsed.status && parsed.status.length > 2) return true
    if (
      parsed.shipments &&
      parsed.shipments.some((s) => (s.status && s.status.message) || s.trackingUrl || s.trackingNumber)
    ) {
      return true
    }

    const sel = getSelectors()
    const root = queryFirst(document, sel.ORDER_DETAILS_ROOT) || document.body
    if (root.querySelector('a[href*="/dp/"], a[href*="/gp/product/"]')) return true
    if (parsePaymentMethods(root).length > 0) return true

    if (!orderIdVisibleInDom(document)) return false

    return orderDetailHasDecryptedContent(root)
  }

  function diagnoseOrderDetailContentReady() {
    const pendingCsd = hasPendingCsdEncryption(document)
    const parsed = parseOrderDetailPage()
    const sel = getSelectors()
    const root = queryFirst(document, sel.ORDER_DETAILS_ROOT) || document.body
    return {
      pendingCsd,
      csdContainerCount: findCsdEncryptedContainers(document).length,
      hasParsedOrderId: !!(parsed && parsed.orderId),
      itemCount: parsed && parsed.items ? parsed.items.length : 0,
      hasGrandTotal: !!(parsed && parsed.totals && parsed.totals.grandTotal != null),
      hasStatus: !!(parsed && parsed.status && parsed.status.length > 2),
      hasProductLink: !!root.querySelector('a[href*="/dp/"], a[href*="/gp/product/"]'),
      orderIdInDom: orderIdVisibleInDom(document),
      ready: isOrderDetailContentReady(),
    }
  }

  function waitForDecryptedContent(checkFn, timeoutMs) {
    const timeout = typeof timeoutMs === 'number' && timeoutMs > 0 ? timeoutMs : 20000
    return new Promise((resolve, reject) => {
      const observers = []
      let timeoutTimer = null
      let settled = false

      function finish(ok) {
        if (settled) return
        settled = true
        observers.forEach((obs) => {
          try {
            obs.disconnect()
          } catch {
            // ignore
          }
        })
        if (timeoutTimer != null) clearTimeout(timeoutTimer)
        if (ok) resolve(true)
        else reject(new Error('Timed out waiting for Amazon page content to decrypt.'))
      }

      function check() {
        try {
          if (checkFn()) {
            finish(true)
            return true
          }
        } catch {
          // ignore
        }
        return false
      }

      if (check()) return

      const observerOptions = {
        childList: true,
        subtree: true,
        characterData: true,
        attributes: true,
      }

      const attachObserver = (target) => {
        if (!target) return
        const observer = new MutationObserver(() => {
          check()
        })
        observer.observe(target, observerOptions)
        observers.push(observer)
      }

      attachObserver(document.documentElement || document.body)

      findCsdEncryptedContainers(document).forEach((el) => attachObserver(el))

      timeoutTimer = setTimeout(() => {
        if (!settled) finish(false)
      }, timeout)
    })
  }

  function waitForOrderListReady() {
    return waitForDecryptedContent(isOrderListContentReady)
  }

  function orderDetailHasDecryptedContent(root) {
    if (!root) return false
    const sel = getSelectors()

    const orderIdEl = queryFirst(root, sel.ORDER_ID)
    if (!orderIdEl || !ORDER_ID_RE.test(textOf(orderIdEl))) {
      if (!ORDER_ID_RE.test(textOf(root))) return false
    }

    const horizonteAddr = parseAddressFromHorizonteComponent(root)
    if (horizonteAddr && (horizonteAddr.fullName || horizonteAddr.addressLine1)) return true

    const addr = parseAddressRoot(root)
    if (addr && (addr.fullName || addr.addressLine1)) return true

    if (root.querySelector('script[id^="shipToData"]')) return true

    const itemContainers = findDetailItemContainers(root)
    for (let i = 0; i < itemContainers.length; i++) {
      const link = itemContainers[i].querySelector('a[href*="/dp/"], a[href*="/gp/product/"]')
      if (link && extractAsinFromUrl(link.getAttribute('href'))) return true
    }

    if (queryAllFirst(root, sel.SUBTOTAL_ROWS).length > 0) {
      const subtotalText = queryAllFirst(root, sel.SUBTOTAL_ROWS)
        .map((row) => textOf(row))
        .join(' ')
      if (extractPriceFromText(subtotalText) != null) return true
    }

    return false
  }

  function waitForOrderDetailReady(timeoutMs) {
    const timeout = typeof timeoutMs === 'number' && timeoutMs > 0 ? timeoutMs : 35000
    return waitForDecryptedContent(isOrderDetailContentReady, timeout)
  }

  function parseEmailFromAccountHtml(html) {
    if (!html) return null
    try {
      const doc = new DOMParser().parseFromString(html, 'text/html')
      const emailInputs = doc.querySelectorAll('input[type="email"], input[name*="email" i]')
      for (let i = 0; i < emailInputs.length; i++) {
        const val = coerceString(emailInputs[i].value)
        if (val && EMAIL_RE.test(val)) return val
      }
      const bodyText = doc.body ? doc.body.textContent || '' : html
      const idx = bodyText.toLowerCase().indexOf('email')
      if (idx >= 0) {
        const slice = bodyText.slice(idx, idx + 200)
        const m = EMAIL_RE.exec(slice)
        if (m) return m[0]
      }
      const m = EMAIL_RE.exec(bodyText)
      return m ? m[0] : null
    } catch {
      return null
    }
  }

  async function fetchAccountEmail(baseOrigin) {
    const origin = baseOrigin || window.location.origin
    const url = origin + '/gp/css/account/info/view.html'
    try {
      const resp = await fetch(url, { credentials: 'include' })
      if (!resp.ok) return null
      const html = await resp.text()
      return parseEmailFromAccountHtml(html)
    } catch {
      return null
    }
  }

  const api = {
    ORDER_ID_RE,
    isAmazonOrderListPage,
    isAmazonOrderDetailPage,
    parseOrderListPage,
    parseOrderDetailPage,
    hasNextPage,
    getNextPageUrl,
    waitForOrderListReady,
    waitForOrderDetailReady,
    waitForOrderDetailReadyInDocument,
    isOrderDetailContentReady,
    isOrderDetailContentReadyInDocument,
    diagnoseOrderDetailContentReady,
    fetchAccountEmail,
    parseEmailFromAccountHtml,
    extractOrderIdFromUrl,
    enrichShipmentsWithTracking,
    parseTrackingPageHtml,
    isLikelyCarrierTrackingNumber,
  }

  if (typeof globalThis !== 'undefined') {
    globalThis.OrderManagerAmazonDom = api
  }
})()
