/* global chrome */

;(function () {
  'use strict'

  const WALMART_ORDER_DETAIL_STORAGE_KEY = 'walmartOrderDetail'
  const ORDER_MANAGER_API_BASE_URL_STORAGE_KEY_LEGACY = 'orderManagerApiBaseUrl'
  const ORDER_MANAGER_PROD_API_BASE_URL_STORAGE_KEY = 'orderManagerProdApiBaseUrl'
  const ORDER_MANAGER_DEV_API_BASE_URL_STORAGE_KEY = 'orderManagerDevApiBaseUrl'
  const ORDER_MANAGER_DEV_ENABLED_STORAGE_KEY = 'orderManagerDevEnabled'
  const ORDER_MANAGER_ACTIVE_SERVER_STORAGE_KEY = 'orderManagerActiveServer' // "prod" | "dev"

  function normalizeActiveServer(v) {
    return v === 'dev' ? 'dev' : 'prod'
  }

  function getOrderManagerActiveServer(callback) {
    if (!chrome || !chrome.storage || !chrome.storage.local) {
      callback('prod')
      return
    }
    try {
      chrome.storage.local.get(ORDER_MANAGER_ACTIVE_SERVER_STORAGE_KEY, (data) => {
        if (chrome.runtime && chrome.runtime.lastError) {
          callback('prod')
          return
        }
        const value =
          data && data[ORDER_MANAGER_ACTIVE_SERVER_STORAGE_KEY]
            ? String(data[ORDER_MANAGER_ACTIVE_SERVER_STORAGE_KEY]).trim()
            : 'prod'
        callback(normalizeActiveServer(value))
      })
    } catch {
      callback('prod')
    }
  }

  function getOrderManagerApiBaseUrlForServer(server, callback) {
    if (!chrome || !chrome.storage || !chrome.storage.local) {
      callback(null)
      return
    }
    try {
      const normalizedServer = normalizeActiveServer(server)
      const keys =
        normalizedServer === 'dev'
          ? [ORDER_MANAGER_DEV_ENABLED_STORAGE_KEY, ORDER_MANAGER_DEV_API_BASE_URL_STORAGE_KEY]
          : [ORDER_MANAGER_PROD_API_BASE_URL_STORAGE_KEY, ORDER_MANAGER_API_BASE_URL_STORAGE_KEY_LEGACY]

      chrome.storage.local.get(keys, (data) => {
        if (chrome.runtime && chrome.runtime.lastError) {
          callback(null)
          return
        }

        if (normalizedServer === 'dev') {
          const enabled = !!(data && data[ORDER_MANAGER_DEV_ENABLED_STORAGE_KEY])
          const value =
            data && data[ORDER_MANAGER_DEV_API_BASE_URL_STORAGE_KEY]
              ? String(data[ORDER_MANAGER_DEV_API_BASE_URL_STORAGE_KEY]).trim()
              : ''
          callback(enabled && value ? value : null)
          return
        }

        const value =
          data && data[ORDER_MANAGER_PROD_API_BASE_URL_STORAGE_KEY]
            ? String(data[ORDER_MANAGER_PROD_API_BASE_URL_STORAGE_KEY]).trim()
            : data && data[ORDER_MANAGER_API_BASE_URL_STORAGE_KEY_LEGACY]
              ? String(data[ORDER_MANAGER_API_BASE_URL_STORAGE_KEY_LEGACY]).trim()
              : ''
        callback(value || null)
      })
    } catch {
      callback(null)
    }
  }

  function getOrderManagerApiBaseUrl(callback) {
    getOrderManagerActiveServer((server) => {
      getOrderManagerApiBaseUrlForServer(server, callback)
    })
  }

  function normalizeWalmartOrderDetailPayload(payload, sourceUrl) {
    if (!payload || typeof payload !== 'object') {
      throw new Error('Missing Walmart order payload.')
    }

    const raw = payload.raw || null
    let order = payload.order || null

    if (!order && raw && raw.props && raw.props.pageProps) {
      try {
        const pageProps = raw.props.pageProps
        if (pageProps.initialData && pageProps.initialData.data && pageProps.initialData.data.order) {
          order = pageProps.initialData.data.order
        }
      } catch {
        // ignore, will validate below
      }
    }

    if (!order || !order.id) {
      throw new Error('Walmart order structure not recognized.')
    }

    const customer = order.customer || {}
    const groups = Array.isArray(order.groups_2101) ? order.groups_2101 : []
    let overallStatusType = null
    try {
      for (let i = 0; i < groups.length; i++) {
        const g = groups[i] || {}
        const status = g.status || {}
        const statusType = typeof status.statusType === 'string' ? status.statusType.trim() : ''
        if (!statusType) continue
        const lower = statusType.toLowerCase()
        if (overallStatusType == null) overallStatusType = statusType
        if (lower.includes('canceled') || lower.includes('cancelled')) {
          overallStatusType = statusType
          break
        }
      }
    } catch {
      // ignore
    }
    const paymentMethodsRaw = Array.isArray(order.paymentMethods) ? order.paymentMethods : []

    const shipments = []
    for (let i = 0; i < groups.length; i++) {
      const g = groups[i] || {}
      const status = g.status || {}
      const shipment = g.shipment || {}
      const subtotal = g.subtotal || {}

      shipments.push({
        shipmentId: shipment.id || null,
        groupId: g.id || null,
        trackingNumber: shipment.trackingNumber || null,
        trackingUrl: shipment.trackingUrl || null,
        purchaseOrderId: shipment.purchaseOrderId || null,
        deliveryDate: g.deliveryDate || null,
        fulfillmentType: g.fulfillmentType || null,
        detailedGroupType: g.detailedGroupType || null,
        status: {
          rawStatusType: status.statusType || null,
          normalizedStatus: status.statusType || null,
          message:
            status.message &&
            Array.isArray(status.message.parts) &&
            status.message.parts.length > 0 &&
            status.message.parts[0] &&
            typeof status.message.parts[0].text === 'string'
              ? status.message.parts[0].text
              : null,
        },
        financials: {
          subtotal: typeof subtotal.value === 'number' ? subtotal.value : null,
        },
      })
    }

    const shippingGroup = groups[0] || {}
    const deliveryAddress = shippingGroup.deliveryAddress || {}
    const deliveryAddressAddress = deliveryAddress.address || {}

    const shippingAddress = {
      fullName: deliveryAddress.fullName || null,
      addressLine1: deliveryAddressAddress.addressLineOne || null,
      addressLine2: deliveryAddressAddress.addressLineTwo || null,
      city: deliveryAddressAddress.city || null,
      state: deliveryAddressAddress.state || null,
      postalCode: deliveryAddressAddress.postalCode || null,
      country: deliveryAddressAddress.country || null,
      phoneNumber: deliveryAddressAddress.phoneNumber || null,
    }

    const items = []
    for (let gi = 0; gi < groups.length; gi++) {
      const g = groups[gi] || {}
      const shipment = g.shipment || {}
      const shipmentId = shipment.id || null

      let groupItems = []
      if (Array.isArray(g.categories) && g.categories.length > 0) {
        for (let ci = 0; ci < g.categories.length; ci++) {
          const catItems = g.categories[ci] && Array.isArray(g.categories[ci].items) ? g.categories[ci].items : []
          groupItems = groupItems.concat(catItems)
        }
      }
      if (groupItems.length === 0 && Array.isArray(g.items)) {
        groupItems = g.items
      }

      for (let ii = 0; ii < groupItems.length; ii++) {
        const item = groupItems[ii] || {}
        const productInfo = item.productInfo || {}
        const priceInfo = item.priceInfo || {}
        const itemPrice = priceInfo.itemPrice || {}
        const unitPriceObj = priceInfo.unitPrice || {}
        const linePrice = priceInfo.linePrice || {}
        const strikethroughPrice = priceInfo.strikethroughPrice || {}
        const qty = typeof item.quantity === 'number' ? item.quantity : 1
        const linePriceVal = typeof linePrice.value === 'number' ? linePrice.value : null

        let unitPriceVal =
          typeof itemPrice.value === 'number'
            ? itemPrice.value
            : typeof unitPriceObj.value === 'number'
              ? unitPriceObj.value
              : null
        if (unitPriceVal == null && linePriceVal != null && qty > 0) {
          unitPriceVal = linePriceVal / qty
        }
        const lineTotal = linePriceVal != null ? linePriceVal : unitPriceVal != null ? unitPriceVal * qty : null

        const variants = Array.isArray(item.selectedVariants)
          ? item.selectedVariants.map(function (v) {
              return {
                name: v && v.name ? String(v.name) : null,
                value: v && v.value ? String(v.value) : null,
              }
            })
          : []

        items.push({
          logicalItemId: item.id != null ? String(item.id) : item.usItemId || null,
          externalSku: productInfo.usItemId || null,
          externalOfferId: productInfo.offerId || null,
          name: productInfo.name || null,
          productUrl: productInfo.canonicalUrl || null,
          imageUrl: productInfo.imageInfo && productInfo.imageInfo.thumbnailUrl ? productInfo.imageInfo.thumbnailUrl : null,
          variants: variants,
          quantities: {
            ordered: typeof item.quantity === 'number' ? item.quantity : null,
          },
          pricing: {
            unitPrice: unitPriceVal,
            linePrice: linePriceVal,
            lineTotal: typeof lineTotal === 'number' ? lineTotal : null,
            strikethroughPrice: typeof strikethroughPrice.value === 'number' ? strikethroughPrice.value : null,
            discounts: [],
          },
          status: {
            rawStatusCode: item.statusCode || null,
            normalizedStatus: null,
          },
          shipments:
            shipmentId && item.quantity
              ? [
                  {
                    shipmentId: shipmentId,
                    quantity: item.quantity,
                    normalizedStatus: null,
                  },
                ]
              : [],
          returnability: {
            isReturnable: !!item.isReturnable,
            returnEligibilityMessage: item.returnEligibilityMessage || null,
          },
        })
      }
    }

    const itemCancelReasons = Array.isArray(order.itemCancelReasons)
      ? order.itemCancelReasons.map(function (r) {
          return {
            code: r && r.subReasonCode != null ? String(r.subReasonCode) : null,
            description: r && typeof r.subDescription === 'string' ? r.subDescription : null,
          }
        })
      : []

    const totals = {
      itemCount: typeof order.itemCount === 'number' ? order.itemCount : null,
      subtotal: shippingGroup.subtotal && typeof shippingGroup.subtotal.value === 'number' ? shippingGroup.subtotal.value : null,
      grandTotal:
        order.priceDetails && order.priceDetails.grandTotal && typeof order.priceDetails.grandTotal.value === 'number'
          ? order.priceDetails.grandTotal.value
          : null,
    }

    const externalUrl =
      sourceUrl ||
      payload.url ||
      (typeof document !== 'undefined' ? (document.location && document.location.href) : null)

    return {
      store: 'walmart',
      source: 'browser-extension',
      capturedAt: new Date().toISOString(),
      externalOrder: {
        id: String(order.id),
        orderDate: order.orderDate || null,
        timezone: order.timezone || null,
        url: externalUrl || null,
        statusType: overallStatusType || null,
      },
      customer: {
        email: customer.email || null,
        firstName: customer.firstName || null,
        lastName: customer.lastName || null,
      },
      shippingAddress: shippingAddress,
      shipments: shipments,
      paymentMethods: paymentMethodsRaw.map(function (pm) {
        const description = typeof pm.description === 'string' ? pm.description : null
        let last4 = null
        if (description) {
          const m = /(\d{4})\b/.exec(description)
          if (m) last4 = m[1]
        }
        return {
          description: description,
          cardType: typeof pm.cardType === 'string' ? pm.cardType : null,
          paymentType: typeof pm.paymentType === 'string' ? pm.paymentType : null,
          last4: last4,
        }
      }),
      items: items,
      cancellations: {
        orderLevel: Array.isArray(order.cancelReasons) ? order.cancelReasons : [],
        itemLevelReasons: itemCancelReasons,
      },
      totals: totals,
    }
  }

  const api = {
    WALMART_ORDER_DETAIL_STORAGE_KEY,
    getOrderManagerActiveServer,
    getOrderManagerApiBaseUrlForServer,
    getOrderManagerApiBaseUrl,
    normalizeWalmartOrderDetailPayload,
  }

  try {
    // Works for window + MV3 service worker + Firefox background script
    globalThis.OrderManagerWalmart = Object.assign({}, globalThis.OrderManagerWalmart || {}, api)
  } catch {
    // ignore
  }
})()

