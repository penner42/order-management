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
    try {
      const path = window.location.pathname || ''
      const href = window.location.href || ''
      return (
        path.includes('/your-orders') ||
        path.includes('/gp/css/order-history') ||
        href.includes('/your-orders/') ||
        href.includes('order-history')
      )
    } catch {
      return false
    }
  }

  function isAmazonOrderDetailPage() {
    try {
      const href = window.location.href || ''
      const path = window.location.pathname || ''
      return (
        path.includes('order-details') ||
        href.includes('order-details') ||
        /orderI[Dd]=/.test(href)
      )
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

  function parseAddressRoot(root) {
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

  function parseDetailItems(root) {
    const sel = getSelectors()
    const itemEls = queryAllFirst(root, sel.ITEM_ROOT)
    const items = []
    const seen = new Set()

    function pushItem(container) {
      const link =
        container.querySelector('a[href*="/dp/"], a[href*="/gp/product/"]') ||
        queryFirst(container, sel.ITEM_TITLE)
      const href = link ? link.getAttribute('href') || '' : ''
      const asin = extractAsinFromUrl(href)
      const key = asin || textOf(link)
      if (!key || seen.has(key)) return
      seen.add(key)

      let name = textOf(queryFirst(container, sel.ITEM_TITLE)) || textOf(link)
      if (!name || name.length < 3) {
        const img = container.querySelector('img')
        if (img && img.alt) name = img.alt.trim()
      }

      let qty = 1
      const qtyEl = queryFirst(container, sel.ITEM_QTY)
      if (qtyEl) {
        const q = parseInt(textOf(qtyEl), 10)
        if (Number.isFinite(q) && q > 0) qty = q
      } else {
        const qm = textOf(container).match(/(?:Qty|Quantity)[:\s]*(\d+)/i)
        if (qm) qty = parseInt(qm[1], 10) || 1
      }

      const unitPrice = extractPriceFromText(textOf(queryFirst(container, sel.ITEM_PRICE))) ||
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

    if (itemEls.length === 0) {
      root.querySelectorAll('a[href*="/dp/"], a[href*="/gp/product/"]').forEach((link) => {
        let parent = link.closest('.a-fixed-left-grid, .yohtmlc-item, .item-box, .a-row')
        if (!parent) parent = link.parentElement
        if (parent) pushItem(parent)
      })
    } else {
      itemEls.forEach((el) => pushItem(el))
    }

    return items
  }

  function parseShipments(root) {
    const sel = getSelectors()
    const shipments = []
    const trackingLinks = []
    queryAllFirst(root, sel.TRACKING_LINK).forEach((a) => trackingLinks.push(a))
    if (trackingLinks.length === 0) {
      root.querySelectorAll('a[href*="ship-track"], a[href*="progress/tracker"]').forEach((a) => {
        trackingLinks.push(a)
      })
    }

    const status = textOf(queryFirst(root, sel.ORDER_STATUS))

    if (trackingLinks.length === 0) {
      if (status) {
        shipments.push({
          trackingNumber: null,
          trackingUrl: null,
          deliveryDate: null,
          status: { rawStatusType: status, message: status },
        })
      }
      return shipments
    }

    const seen = new Set()
    trackingLinks.forEach((a) => {
      const url = absoluteUrl(a.getAttribute('href'))
      if (!url || seen.has(url)) return
      seen.add(url)
      let trackingNumber = null
      const tm = /trackingId=([^&]+)/i.exec(url) || /tracker\/([^/?]+)/i.exec(url)
      if (tm) trackingNumber = decodeURIComponent(tm[1])
      shipments.push({
        trackingNumber,
        trackingUrl: url,
        deliveryDate: null,
        status: { rawStatusType: status, message: status },
      })
    })
    return shipments
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

  function parseOrderDetailPage() {
    const sel = getSelectors()
    const root = queryFirst(document, sel.ORDER_DETAILS_ROOT) || document.body
    const rootText = textOf(root)

    let orderId = extractOrderIdFromText(textOf(queryFirst(root, sel.ORDER_ID)))
    if (!orderId) orderId = extractOrderIdFromUrl(window.location.href)
    if (!orderId) orderId = extractOrderIdFromText(rootText)
    if (!orderId) return null

    const orderDate =
      parseOrderDate(textOf(queryFirst(root, sel.ORDER_DATE))) || parseOrderDate(rootText)
    const status = textOf(queryFirst(root, sel.ORDER_STATUS))
    const items = parseDetailItems(root)
    const shippingAddress = parseAddressRoot(root)
    const shipments = parseShipments(root)
    const paymentMethods = parsePaymentMethods(root)
    const totals = parseTotals(root)

    if (totals.grandTotal == null) {
      totals.grandTotal = extractPriceFromText(textOf(queryFirst(root, sel.ORDER_TOTAL)))
    }

    return {
      orderId,
      orderDate,
      status,
      detailUrl: window.location.href,
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

  function waitForDecryptedContent(checkFn, timeoutMs) {
    const timeout = typeof timeoutMs === 'number' && timeoutMs > 0 ? timeoutMs : 20000
    return new Promise((resolve, reject) => {
      const start = Date.now()

      function check() {
        try {
          if (checkFn()) {
            resolve(true)
            return true
          }
        } catch {
          // ignore
        }
        return false
      }

      if (check()) return

      const observer = new MutationObserver(() => {
        if (check()) observer.disconnect()
      })
      observer.observe(document.documentElement || document.body, {
        childList: true,
        subtree: true,
        characterData: true,
      })

      const interval = setInterval(() => {
        if (check()) {
          clearInterval(interval)
          return
        }
        if (Date.now() - start > timeout) {
          clearInterval(interval)
          observer.disconnect()
          reject(new Error('Timed out waiting for Amazon page content to decrypt.'))
        }
      }, 400)
    })
  }

  function waitForOrderListReady() {
    return waitForDecryptedContent(() => {
      const cards = findOrderCards()
      if (cards.length === 0) return false
      for (let i = 0; i < cards.length; i++) {
        if (ORDER_ID_RE.test(textOf(cards[i]))) return true
      }
      return false
    })
  }

  function waitForOrderDetailReady() {
    return waitForDecryptedContent(() => {
      const parsed = parseOrderDetailPage()
      return !!(parsed && parsed.orderId)
    })
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
    fetchAccountEmail,
    parseEmailFromAccountHtml,
    extractOrderIdFromUrl,
  }

  if (typeof globalThis !== 'undefined') {
    globalThis.OrderManagerAmazonDom = api
  }
})()
