/* global chrome */

;(function () {
  'use strict'

  function coerceString(v) {
    if (v == null) return null
    if (typeof v === 'string') return v
    if (typeof v === 'number' || typeof v === 'boolean') return String(v)
    return null
  }

  function normalizeIsoOrNull(v) {
    const s = coerceString(v)
    if (!s || !s.trim()) return null
    return s.trim()
  }

  function costcoUpc(item) {
    // Accept only explicit barcode fields; itemNumber is a Costco SKU.
    // Keep strings intact so leading zeros survive capture and storage.
    const values = new Set()
    for (const key of ['upc', 'UPC', 'upcCode', 'upcNumber']) {
      const raw = item && item[key]
      if (typeof raw !== 'string') continue
      const value = raw.trim()
      if (/^(?:[0-9]{8}|[0-9]{12,14})$/.test(value)) values.add(value)
    }
    return values.size === 1 ? values.values().next().value : null
  }

  function safeNumber(v) {
    if (typeof v === 'number' && Number.isFinite(v)) return v
    if (v && typeof v === 'object' && typeof v.parsedValue === 'number' && Number.isFinite(v.parsedValue)) {
      return v.parsedValue
    }
    if (typeof v === 'string' && v.trim()) {
      const n = parseFloat(v)
      return Number.isFinite(n) ? n : null
    }
    return null
  }

  function uniqPush(map, key, value) {
    if (!key) return
    if (map[key]) return
    map[key] = value
  }

  // Costco GraphQL returns `shipment` as a single object on each line item, not an array.
  function coerceShipmentArray(shipment) {
    if (Array.isArray(shipment)) return shipment
    if (shipment && typeof shipment === 'object') return [shipment]
    return []
  }

  function isCostcoLogisticsCarrier(carrierName) {
    const name = coerceString(carrierName)
    return !!(name && name.trim().toLowerCase() === 'costco logistics')
  }

  function resolveCostcoTrackingNumber(shipment, orderNumber) {
    const jsonTracking = coerceString(shipment && shipment.trackingNumber)
    if (!isCostcoLogisticsCarrier(shipment && shipment.carrierName)) {
      return jsonTracking || null
    }
    const orderId =
      coerceString(orderNumber) ||
      coerceString(shipment && shipment.orderNumber) ||
      null
    if (orderId && orderId.trim()) return orderId.trim()
    return jsonTracking || null
  }

  function collectShipmentsFromCostcoLineItem(lineItem, shipmentsById, orderNumber) {
    const shipmentSlices = []
    const shipments = coerceShipmentArray(lineItem && lineItem.shipment)
    const itemStatus =
      lineItem && (coerceString(lineItem.status) || coerceString(lineItem.orderStatus))

    for (let si = 0; si < shipments.length; si++) {
      const s = shipments[si] || {}
      const shipmentId =
        coerceString(s.shipmentId) ||
        coerceString(s.packageNumber) ||
        coerceString(s.trackingNumber)
      if (!shipmentId) continue

      const trackingNumber = resolveCostcoTrackingNumber(s, orderNumber)
      const trackingUrl = coerceString(s.trackingSiteUrl)
      const deliveredDate = normalizeIsoOrNull(s.deliveredDate)
      const estimatedArrivalDate = normalizeIsoOrNull(s.estimatedArrivalDate)
      const deliveryDate =
        deliveredDate ||
        estimatedArrivalDate ||
        normalizeIsoOrNull(lineItem && lineItem.deliveryDate) ||
        normalizeIsoOrNull(lineItem && lineItem.estimatedDeliveryDate)

      const shipmentStatus = coerceString(s.status) || itemStatus || null

      uniqPush(shipmentsById, shipmentId, {
        shipmentId,
        trackingNumber: trackingNumber || null,
        trackingUrl: trackingUrl || null,
        deliveryDate: deliveryDate || null,
        status: {
          rawStatusType: shipmentStatus,
          message: shipmentStatus,
        },
      })

      const qty = safeNumber(lineItem && lineItem.quantity)
      shipmentSlices.push({
        shipmentId,
        quantity: qty != null && qty > 0 ? qty : 1,
      })
    }

    return shipmentSlices
  }

  function shipmentsListFromMap(shipmentsById) {
    const shipmentsList = []
    for (const sid in shipmentsById) {
      shipmentsList.push(shipmentsById[sid])
    }
    return shipmentsList
  }

  function deduplicateCostcoPurchaseLines(items, shipments) {
    // Returned and Delivered can be two snapshots of the same purchase line.
    // Only collapse matching identities and allocations, never just matching SKUs.
    const sources = new Map(shipments.map(s => [s.shipmentId, s]))
    const positions = new Map()
    const result = []
    for (const item of items) {
      const identity = coerceString(item.logicalItemId)
      if (!identity || !identity.trim()) {
        result.push(item)
        continue
      }
      const slices = (item.shipments || []).map(s => [
        String((sources.get(s.shipmentId) || {}).trackingNumber || s.shipmentId || ''),
        s.quantity || 1,
      ]).sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)))
      const key = JSON.stringify([identity.trim(), item.externalSku, item.name,
        (item.quantities || {}).ordered, (item.pricing || {}).unitPrice, slices])
      if (!positions.has(key)) {
        positions.set(key, result.length)
        result.push(item)
      } else if (/return|refund/i.test((result[positions.get(key)].status || {}).rawStatusCode || '')) {
        result[positions.get(key)] = item
      }
    }
    return result
  }

  function extractOrdersFromCostcoGraphql(graphqlPayload) {
    if (!graphqlPayload || typeof graphqlPayload !== 'object') return []
    const go = graphqlPayload.data && graphqlPayload.data.getOnlineOrders
    if (!Array.isArray(go) || go.length === 0) return []
    const first = go[0] || {}
    const orders = Array.isArray(first.bcOrders) ? first.bcOrders : []
    return orders
  }

  function extractOrderDetailsFromCostcoGraphql(graphqlPayload) {
    if (!graphqlPayload || typeof graphqlPayload !== 'object') return null
    const data = graphqlPayload.data && graphqlPayload.data.getOrderDetails
    if (!data || typeof data !== 'object') return null
    return data
  }

  function extractOrderDiscountFromCostcoOrderDetails(details) {
    if (!details || typeof details !== 'object') return null
    // Costco order details GraphQL uses { source: "575.0", parsedValue: 575 }
    // We prefer "source" because it's consistently present as a string.
    const discountObj = details.discountAmount
    if (discountObj && typeof discountObj === 'object' && discountObj.source != null) {
      return safeNumber(discountObj.source)
    }
    return safeNumber(discountObj)
  }

  function primaryShipToFromDetails(details) {
    const shipTo = Array.isArray(details && details.shipToAddress) ? details.shipToAddress : []
    return shipTo.length > 0 ? shipTo[0] || {} : null
  }

  function buildShippingAddressFromShipTo(primaryShipTo) {
    if (!primaryShipTo || typeof primaryShipTo !== 'object') return null
    return {
      firstName: coerceString(primaryShipTo.firstName) || null,
      lastName: coerceString(primaryShipTo.lastName) || null,
      fullName: [coerceString(primaryShipTo.firstName), coerceString(primaryShipTo.lastName)]
        .filter(Boolean)
        .join(' ')
        .trim() || null,
      addressLine1: coerceString(primaryShipTo.line1) || null,
      addressLine2: coerceString(primaryShipTo.line2) || null,
      city: coerceString(primaryShipTo.city) || null,
      state: coerceString(primaryShipTo.state) || null,
      postalCode: coerceString(primaryShipTo.postalCode) || null,
      country: coerceString(primaryShipTo.countryCode) || null,
      phoneNumber: coerceString(primaryShipTo.phoneNumber) || null,
    }
  }

  function buildCustomerFromDetails(details, primaryShipTo) {
    return {
      email:
        coerceString(details && details.emailAddress) ||
        coerceString(primaryShipTo && primaryShipTo.emailAddress) ||
        null,
      firstName: coerceString(details && details.firstName) || null,
      lastName: coerceString(details && details.lastName) || null,
    }
  }

  function buildPaymentMethodsFromDetails(details) {
    const paymentMethodsRaw = Array.isArray(details && details.orderPayment) ? details.orderPayment : []
    return paymentMethodsRaw.map((pm) => {
      const paymentType = coerceString(pm && pm.paymentType)
      const last4 = (() => {
        const v = coerceString(pm && pm.cardNumber)
        if (!v) return null
        const digits = v.replace(/\D+/g, '')
        if (digits.length >= 4) return digits.slice(-4)
        if (v.length >= 4) return v.slice(-4)
        return null
      })()
      return {
        description: paymentType || null,
        cardType: paymentType || null,
        paymentType: paymentType || null,
        last4,
      }
    })
  }

  function normalizeCostcoOrderDetailsGraphqlPayload(graphqlPayload, sourceUrl) {
    const details = extractOrderDetailsFromCostcoGraphql(graphqlPayload)
    if (!details) {
      throw new Error('Missing Costco getOrderDetails payload.')
    }

    const orderNumber = coerceString(details.orderNumber)
    if (!orderNumber || !orderNumber.trim()) {
      throw new Error('Missing Costco orderNumber in getOrderDetails payload.')
    }

    const capturedAt = new Date().toISOString()
    const externalUrl =
      sourceUrl ||
      (typeof document !== 'undefined' ? (document.location && document.location.href) : null) ||
      null

    const primaryShipTo = primaryShipToFromDetails(details)
    const shippingAddress = primaryShipTo ? buildShippingAddressFromShipTo(primaryShipTo) : null

    const items = []
    const shipmentsById = {}
    const allLineItems = extractDetailLineItems(details)
    for (let i = 0; i < allLineItems.length; i++) {
      const it = allLineItems[i] || {}
      const logicalItemId = costcoLineItemLogicalId(it)
      const qty = safeNumber(it.quantity)
      const unitPrice = safeNumber(it.price)
      const lineTotal = safeNumber(it.merchandiseTotalAmount)
      const shipmentSlices = collectShipmentsFromCostcoLineItem(it, shipmentsById, orderNumber)

      items.push({
        logicalItemId,
        externalSku: coerceString(it.itemNumber) || null,
        upc: costcoUpc(it),
        name: coerceString(it.itemDescription) || coerceString(it.itemNumber) || null,
        productUrl: null,
        imageUrl: null,
        variants: [],
        quantities: { ordered: qty != null ? qty : 1 },
        pricing: {
          unitPrice: unitPrice != null ? unitPrice : null,
          linePrice: lineTotal != null ? lineTotal : null,
          lineTotal: lineTotal != null ? lineTotal : null,
          strikethroughPrice: null,
          discounts: [],
        },
        status: {
          rawStatusCode: coerceString(it.orderStatus) || coerceString(it.status) || null,
          normalizedStatus: null,
        },
        shipments: shipmentSlices,
        returnability: {
          isReturnable: !!it.orderLineItemCancelAllowed ? !!it.orderReturnAllowed : !!it.orderReturnAllowed,
          returnEligibilityMessage: null,
        },
      })
    }

    const orderTotal = safeNumber(details.orderTotal) != null ? safeNumber(details.orderTotal) : safeNumber(details.merchandiseTotal)
    const paymentMethods = buildPaymentMethodsFromDetails(details)
    const orderDiscount = extractOrderDiscountFromCostcoOrderDetails(details)

    return {
      store: 'costco',
      source: 'browser-extension',
      capturedAt,
      orderDiscount: orderDiscount != null ? orderDiscount : null,
      externalOrder: {
        id: String(orderNumber).trim(),
        orderDate: normalizeIsoOrNull(details.orderPlacedDate),
        url: externalUrl,
        statusType: coerceString(details.status) || null,
      },
      customer: buildCustomerFromDetails(details, primaryShipTo),
      shippingAddress,
      shipments: shipmentsListFromMap(shipmentsById),
      paymentMethods,
      items: deduplicateCostcoPurchaseLines(items, shipmentsListFromMap(shipmentsById)),
      cancellations: {
        orderLevel: [],
        itemLevelReasons: [],
      },
      totals: {
        grandTotal: orderTotal != null ? orderTotal : null,
      },
    }
  }

  function extractDetailLineItems(orderDetails) {
    const shipTo = Array.isArray(orderDetails.shipToAddress) ? orderDetails.shipToAddress : []
    const all = []
    for (let si = 0; si < shipTo.length; si++) {
      const s = shipTo[si] || {}
      const items = Array.isArray(s.orderLineItems) ? s.orderLineItems : []
      for (let ii = 0; ii < items.length; ii++) all.push(items[ii] || {})
    }
    return all
  }

  function costcoLineItemLogicalIds(lineItem) {
    const it = lineItem || {}
    const ids = []
    const seen = {}
    const candidates = [
      coerceString(it.lineItemId),
      coerceString(it.orderLineItemId),
      coerceString(it.sourceLineItemId),
      it.lineNumber != null ? String(it.lineNumber) : null,
    ]
    for (let i = 0; i < candidates.length; i++) {
      const id = candidates[i]
      if (!id || !String(id).trim() || seen[id]) continue
      seen[id] = true
      ids.push(id)
    }
    return ids
  }

  function costcoLineItemLogicalId(lineItem) {
    const ids = costcoLineItemLogicalIds(lineItem)
    return ids.length > 0 ? ids[0] : null
  }

  function buildDetailIndexByLineItemId(orderDetailsPayload) {
    const details = extractOrderDetailsFromCostcoGraphql(orderDetailsPayload)
    if (!details) return null
    const orderNumber = coerceString(details.orderNumber)
    if (!orderNumber) return null

    const byId = {}
    const shipmentsById = {}
    const orderDiscount = extractOrderDiscountFromCostcoOrderDetails(details)
    const items = extractDetailLineItems(details)
    for (let i = 0; i < items.length; i++) {
      const it = items[i] || {}
      const ids = costcoLineItemLogicalIds(it)
      if (ids.length === 0) continue
      const entry = {
        quantity: safeNumber(it.quantity),
        unitPrice: safeNumber(it.price),
        lineTotal: safeNumber(it.merchandiseTotalAmount),
        itemNumber: coerceString(it.itemNumber) || null,
        upc: costcoUpc(it),
        itemDescription: coerceString(it.itemDescription) || null,
        shipmentSlices: collectShipmentsFromCostcoLineItem(it, shipmentsById, orderNumber),
      }
      for (let ki = 0; ki < ids.length; ki++) {
        byId[ids[ki]] = entry
      }
    }

    return {
      orderNumber: String(orderNumber).trim(),
      byId,
      orderDiscount,
      shipmentsById,
    }
  }

  function mergeCostcoOrderDetailsIntoNormalizedOrders(normalizedOrders, orderDetailsPayloadsByOrderNumber) {
    const list = Array.isArray(normalizedOrders) ? normalizedOrders : []
    const detailsMap = orderDetailsPayloadsByOrderNumber && typeof orderDetailsPayloadsByOrderNumber === 'object' ? orderDetailsPayloadsByOrderNumber : {}

    for (let oi = 0; oi < list.length; oi++) {
      const order = list[oi] || {}
      const orderNumber = order && order.externalOrder && order.externalOrder.id ? String(order.externalOrder.id) : null
      if (!orderNumber) continue
      const detailsPayload = detailsMap[orderNumber]
      if (!detailsPayload) continue

      const details = extractOrderDetailsFromCostcoGraphql(detailsPayload)
      const primaryShipTo = details ? primaryShipToFromDetails(details) : null

      // List capture omits address/email/payments; detail payload has them for subaccount + BG matching.
      if (details) {
        const detailCustomer = buildCustomerFromDetails(details, primaryShipTo)
        if (!order.customer || typeof order.customer !== 'object') order.customer = {}
        if (!order.customer.email && detailCustomer.email) order.customer.email = detailCustomer.email
        if (!order.customer.firstName && detailCustomer.firstName) order.customer.firstName = detailCustomer.firstName
        if (!order.customer.lastName && detailCustomer.lastName) order.customer.lastName = detailCustomer.lastName

        if (!order.shippingAddress && primaryShipTo) {
          order.shippingAddress = buildShippingAddressFromShipTo(primaryShipTo)
        }

        const existingPayments = Array.isArray(order.paymentMethods) ? order.paymentMethods : []
        if (existingPayments.length === 0) {
          order.paymentMethods = buildPaymentMethodsFromDetails(details)
        }
      }

      const idx = buildDetailIndexByLineItemId(detailsPayload)
      if (!idx || !idx.byId) continue

      // Enrich order-level discount (if present in the order details payload).
      if (idx.orderDiscount != null) {
        order.orderDiscount = idx.orderDiscount
      }

      const orderShipmentsById = {}
      const existingShipments = Array.isArray(order.shipments) ? order.shipments : []
      for (let si = 0; si < existingShipments.length; si++) {
        const shipment = existingShipments[si] || {}
        const shipmentId = coerceString(shipment.shipmentId)
        if (shipmentId) orderShipmentsById[shipmentId] = shipment
      }
      const detailShipmentsById =
        idx.shipmentsById && typeof idx.shipmentsById === 'object' ? idx.shipmentsById : {}
      for (const sid in detailShipmentsById) {
        uniqPush(orderShipmentsById, sid, detailShipmentsById[sid])
      }
      order.shipments = shipmentsListFromMap(orderShipmentsById)

      const items = Array.isArray(order.items) ? order.items : []
      for (let ii = 0; ii < items.length; ii++) {
        const item = items[ii] || {}
        const logicalItemId = coerceString(item.logicalItemId)
        if (!logicalItemId) continue

        const d = idx.byId[logicalItemId]
        if (!d) continue

        if (!item.upc && d.upc) item.upc = d.upc

        if (!item.quantities || typeof item.quantities !== 'object') item.quantities = {}
        if (d.quantity != null) item.quantities.ordered = d.quantity

        if (!item.pricing || typeof item.pricing !== 'object') item.pricing = {}
        if (d.unitPrice != null) item.pricing.unitPrice = d.unitPrice
        if (d.lineTotal != null) {
          item.pricing.lineTotal = d.lineTotal
          item.pricing.linePrice = d.lineTotal
        }

        if (Array.isArray(d.shipmentSlices) && d.shipmentSlices.length > 0) {
          item.shipments = d.shipmentSlices
        }
      }
      order.items = deduplicateCostcoPurchaseLines(items, order.shipments)
    }

    return list
  }

  function normalizeCostcoOrdersGraphqlPayload(graphqlPayload, sourceUrl) {
    const orders = extractOrdersFromCostcoGraphql(graphqlPayload)
    const capturedAt = new Date().toISOString()

    const normalized = []

    for (let oi = 0; oi < orders.length; oi++) {
      const o = orders[oi] || {}
      const orderNumber = coerceString(o.orderNumber)
      if (!orderNumber || !orderNumber.trim()) continue

      const orderPlacedDate = normalizeIsoOrNull(o.orderPlacedDate)
      const status = coerceString(o.status)
      const orderTotal = safeNumber(o.orderTotal)

      const externalUrl =
        sourceUrl ||
        (typeof document !== 'undefined' ? (document.location && document.location.href) : null) ||
        null

      const shipmentsById = {}
      const shipmentsList = []
      const itemsList = []

      const lineItems = Array.isArray(o.orderLineItems) ? o.orderLineItems : []
      for (let li = 0; li < lineItems.length; li++) {
        const item = lineItems[li] || {}
        const itemName = coerceString(item.itemDescription) || coerceString(item.itemNumber) || 'Item'
        const logicalItemId = costcoLineItemLogicalId(item)
        const qty = safeNumber(item.quantity)

        const shipmentSlices = collectShipmentsFromCostcoLineItem(item, shipmentsById, orderNumber)

        itemsList.push({
          logicalItemId,
          externalSku: coerceString(item.itemNumber) || coerceString(item.itemId) || null,
          upc: costcoUpc(item),
          name: itemName || null,
          productUrl: null,
          imageUrl: null,
          variants: [],
          quantities: { ordered: qty != null && qty > 0 ? qty : 1 },
          pricing: {
            unitPrice: null,
            linePrice: null,
            lineTotal: null,
            strikethroughPrice: null,
            discounts: [],
          },
          status: {
            rawStatusCode: coerceString(item.status) || null,
            normalizedStatus: null,
          },
          shipments: shipmentSlices,
          returnability: {
            isReturnable: !!item.orderReturnAllowed,
            returnEligibilityMessage: null,
          },
        })
      }

      shipmentsList.push(...shipmentsListFromMap(shipmentsById))

      normalized.push({
        store: 'costco',
        source: 'browser-extension',
        capturedAt,
        externalOrder: {
          id: String(orderNumber).trim(),
          orderDate: orderPlacedDate,
          url: externalUrl,
          statusType: status || null,
        },
        customer: {
          email: coerceString(o.emailAddress) || null,
          firstName: null,
          lastName: null,
        },
        shippingAddress: null,
        shipments: shipmentsList,
        paymentMethods: [],
        items: deduplicateCostcoPurchaseLines(itemsList, shipmentsList),
        cancellations: {
          orderLevel: [],
          itemLevelReasons: [],
        },
        totals: {
          grandTotal: orderTotal,
        },
      })
    }

    return normalized
  }

  function jsonObjectAt(text, start) {
    // Read JSON without executing the page's scripts (also works in a worker).
    let depth = 0
    let quoted = false
    let escaped = false
    for (let i = start; i < text.length; i++) {
      const ch = text[i]
      if (quoted) {
        if (escaped) escaped = false
        else if (ch === '\\') escaped = true
        else if (ch === '"') quoted = false
      } else if (ch === '"') quoted = true
      else if (ch === '{') depth++
      else if (ch === '}' && --depth === 0) {
        try { return JSON.parse(text.slice(start, i + 1)) } catch { return null }
      }
    }
    return null
  }

  function catalogUpc(value) {
    const upc = costcoUpc({ upc: value })
    if (!upc) return null
    // GS1 RCN-12 prefix 4 / RCN-13 prefix 04 is company-internal numbering.
    if ((upc.length === 12 && upc[0] === '4') || (upc.length === 13 && upc.startsWith('04'))) return null
    let sum = 0
    for (let i = upc.length - 2, weight = 3; i >= 0; i--, weight = 4 - weight) {
      sum += Number(upc[i]) * weight
    }
    return (10 - sum % 10) % 10 === Number(upc.at(-1)) ? upc : null
  }

  function extractCostcoProductUpc(html, sku) {
    if (typeof html !== 'string' || !/^[0-9]{1,20}$/.test(String(sku))) return null
    // Costco embeds catalog data in Next.js Flight chunks, sometimes split
    // across scripts. Product pages also contain unrelated recommended products.
    const chunks = []
    for (const script of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script\s*>/gi)) {
      const push = /^\s*self\.__next_f\.push\(([\s\S]*)\)\s*;?\s*$/.exec(script[1])
      if (!push) continue
      try {
        const part = JSON.parse(push[1])
        if (part[0] === 1 && typeof part[1] === 'string') chunks.push(part[1])
      } catch { /* Ignore scripts that are not plain Flight JSON. */ }
    }
    const text = chunks.join('')
    const values = new Set()
    for (const match of text.matchAll(/"productData"\s*:\s*\{/g)) {
      const product = jsonObjectAt(text, match.index + match[0].length - 1)
      if (!product) continue
      const children = Array.isArray(product.childCatalogData) ? product.childCatalogData : []
      const exact = children.filter(child => String(child.id) === String(sku))
      for (const child of exact) {
        let upc = catalogUpc(child.upc)
        // A single-item product may expose the manufacturer's barcode only
        // on its parent. Never borrow a parent's UPC across multiple variants.
        const internal = typeof child.upc === 'string' && /^(?:4[0-9]{11}|04[0-9]{11})$/.test(child.upc)
        if (!upc && children.length === 1 && internal) upc = catalogUpc(product.upc)
        if (upc) values.add(upc)
      }
      if (String(product.id) === String(sku)) {
        const upc = catalogUpc(product.upc)
        if (upc) values.add(upc)
      }
    }
    return values.size === 1 ? values.values().next().value : null
  }

  async function fetchCostcoProductHtml(sku) {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 15000)
    try {
      const response = await fetch('https://www.costco.com/p/-/' + sku, { signal: controller.signal })
      if (!response.ok) return null
      // Refuse off-site redirects, error pages and non-HTML responses.
      if (new URL(response.url).origin !== 'https://www.costco.com') return null
      if (!(response.headers.get('content-type') || '').includes('text/html')) return null
      return await response.text()
    } finally { clearTimeout(timer) }
  }

  async function enrichCostcoOrdersWithProductUpcs(orders, loadHtml = fetchCostcoProductHtml) {
    const bySku = new Map()
    for (const order of orders) {
      for (const item of order.items || []) {
        const sku = String(item.externalSku || '')
        if (item.upc || !/^[0-9]{1,20}$/.test(sku)) continue
        if (!bySku.has(sku)) bySku.set(sku, [])
        bySku.get(sku).push(item)
      }
    }
    const pending = [...bySku.entries()]
    async function worker() {
      while (pending.length) {
        const [sku, items] = pending.shift()
        try {
          const upc = extractCostcoProductUpc(await loadHtml(sku), sku)
          if (upc) for (const item of items) item.upc = upc
        } catch { /* Missing/discontinued/blocked products must not stop imports. */ }
      }
    }
    await Promise.all([worker(), worker()])
    return orders
  }

  const api = {
    extractCostcoProductUpc,
    enrichCostcoOrdersWithProductUpcs,
    normalizeCostcoOrdersGraphqlPayload,
    normalizeCostcoOrderDetailsGraphqlPayload,
    mergeCostcoOrderDetailsIntoNormalizedOrders,
  }

  try {
    globalThis.OrderManagerCostco = Object.assign({}, globalThis.OrderManagerCostco || {}, api)
  } catch {
    // ignore
  }
})()

