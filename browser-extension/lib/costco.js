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

    const shipTo = Array.isArray(details.shipToAddress) ? details.shipToAddress : []
    const primaryShipTo = shipTo.length > 0 ? shipTo[0] || {} : {}

    const shippingAddress = shipTo.length
      ? {
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
      : null

    const items = []
    const allLineItems = extractDetailLineItems(details)
    for (let i = 0; i < allLineItems.length; i++) {
      const it = allLineItems[i] || {}
      const logicalItemId = coerceString(it.lineItemId) || coerceString(it.orderLineItemId) || null
      const qty = safeNumber(it.quantity)
      const unitPrice = safeNumber(it.price)
      const lineTotal = safeNumber(it.merchandiseTotalAmount)

      items.push({
        logicalItemId,
        externalSku: coerceString(it.itemNumber) || null,
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
        shipments: [],
        returnability: {
          isReturnable: !!it.orderLineItemCancelAllowed ? !!it.orderReturnAllowed : !!it.orderReturnAllowed,
          returnEligibilityMessage: null,
        },
      })
    }

    const orderTotal = safeNumber(details.orderTotal) != null ? safeNumber(details.orderTotal) : safeNumber(details.merchandiseTotal)

    const paymentMethodsRaw = Array.isArray(details.orderPayment) ? details.orderPayment : []
    const paymentMethods = paymentMethodsRaw.map((pm) => {
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
      customer: {
        email: coerceString(details.emailAddress) || coerceString(primaryShipTo.emailAddress) || null,
        firstName: coerceString(details.firstName) || null,
        lastName: coerceString(details.lastName) || null,
      },
      shippingAddress,
      shipments: [],
      paymentMethods,
      items,
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

  function buildDetailIndexByLineItemId(orderDetailsPayload) {
    const details = extractOrderDetailsFromCostcoGraphql(orderDetailsPayload)
    if (!details) return null
    const orderNumber = coerceString(details.orderNumber)
    if (!orderNumber) return null

    const byId = {}
    const orderDiscount = extractOrderDiscountFromCostcoOrderDetails(details)
    const items = extractDetailLineItems(details)
    for (let i = 0; i < items.length; i++) {
      const it = items[i] || {}
      const lineItemId = coerceString(it.lineItemId) || coerceString(it.orderLineItemId) || null
      if (!lineItemId) continue
      byId[lineItemId] = {
        quantity: safeNumber(it.quantity),
        unitPrice: safeNumber(it.price),
        lineTotal: safeNumber(it.merchandiseTotalAmount),
        itemNumber: coerceString(it.itemNumber) || null,
        itemDescription: coerceString(it.itemDescription) || null,
      }
    }

    return { orderNumber: String(orderNumber).trim(), byId, orderDiscount }
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

      const idx = buildDetailIndexByLineItemId(detailsPayload)
      if (!idx || !idx.byId) continue

      // Enrich order-level discount (if present in the order details payload).
      if (idx.orderDiscount != null) {
        order.orderDiscount = idx.orderDiscount
      }

      const items = Array.isArray(order.items) ? order.items : []
      for (let ii = 0; ii < items.length; ii++) {
        const item = items[ii] || {}
        const logicalItemId = coerceString(item.logicalItemId)
        if (!logicalItemId) continue

        const d = idx.byId[logicalItemId]
        if (!d) continue

        if (!item.quantities || typeof item.quantities !== 'object') item.quantities = {}
        if (d.quantity != null) item.quantities.ordered = d.quantity

        if (!item.pricing || typeof item.pricing !== 'object') item.pricing = {}
        if (d.unitPrice != null) item.pricing.unitPrice = d.unitPrice
        if (d.lineTotal != null) {
          item.pricing.lineTotal = d.lineTotal
          item.pricing.linePrice = d.lineTotal
        }
      }
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
        const logicalItemId =
          coerceString(item.orderLineItemId) ||
          (item.lineNumber != null ? String(item.lineNumber) : null) ||
          null

        const shipmentSlices = []
        const shipments = Array.isArray(item.shipment) ? item.shipment : []

        for (let si = 0; si < shipments.length; si++) {
          const s = shipments[si] || {}
          const shipmentId = coerceString(s.shipmentId) || coerceString(s.packageNumber) || coerceString(s.trackingNumber)
          if (!shipmentId) continue

          const trackingNumber = coerceString(s.trackingNumber)
          const trackingUrl = coerceString(s.trackingSiteUrl)

          const deliveredDate = normalizeIsoOrNull(s.deliveredDate)
          const estimatedArrivalDate = normalizeIsoOrNull(s.estimatedArrivalDate)
          const deliveryDate = deliveredDate || estimatedArrivalDate || normalizeIsoOrNull(item.deliveryDate)

          const shipmentStatus = coerceString(s.status) || coerceString(item.status) || null

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

          shipmentSlices.push({
            shipmentId,
            quantity: 1,
          })
        }

        itemsList.push({
          logicalItemId,
          externalSku: coerceString(item.itemNumber) || coerceString(item.itemId) || null,
          name: itemName || null,
          productUrl: null,
          imageUrl: null,
          variants: [],
          quantities: { ordered: 1 },
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

      for (const sid in shipmentsById) {
        shipmentsList.push(shipmentsById[sid])
      }

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
        items: itemsList,
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

  const api = {
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

