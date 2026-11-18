;(function () {
  'use strict'

  const COSTCO_BULK_GRAPHQL_STORAGE_KEY = 'costcoOrdersGraphql'
  const COSTCO_ORDER_DETAILS_GRAPHQL_TEMPLATE_STORAGE_KEY = 'costcoOrderDetailsGraphqlTemplate'
  const COSTCO_ORDER_DETAILS_CAPTURE_STORAGE_KEY = 'costcoOrderDetailsGraphqlCapture'
  const SOURCE = 'order-manager-costco'
  let automationOrderContext = null

  if (typeof chrome === 'undefined' || !chrome.storage || !chrome.storage.local) {
    return
  }

  function isCostcoOrdersAndPurchasesRoute() {
    try {
      return /#\/app\/[^/]+\/ordersandpurchases/.test(window.location.hash || '')
    } catch {
      return false
    }
  }

  function isCostcoOrderDetailsRoute() {
    try {
      return /#\/app\/[^/]+\/orderdetails/.test(window.location.hash || '')
    } catch {
      return false
    }
  }

  function isCostcoCaptureRoute() {
    return isCostcoOrdersAndPurchasesRoute() || isCostcoOrderDetailsRoute()
  }

  function injectPageScript() {
    try {
      const src = chrome.runtime.getURL('stores/costco/bulk_capture_injected.js')
      const s = document.createElement('script')
      s.src = src
      s.async = false
      ;(document.head || document.documentElement).appendChild(s)
      s.parentNode && s.parentNode.removeChild(s)
    } catch {
      // ignore
    }
  }

  // Inject early to catch the initial GraphQL call.
  if (isCostcoCaptureRoute()) {
    injectPageScript()
  }

  // Re-inject on SPA hash navigation (best-effort; injected hook is idempotent).
  window.addEventListener('hashchange', () => {
    automationOrderContext = null
    if (isCostcoCaptureRoute()) injectPageScript()
  })

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.store === 'costco' && message.type === 'getAutomationOrderContext') {
      sendResponse(automationOrderContext?.url === window.location.href
        ? automationOrderContext
        : null)
    }
  })

  // Persist captured payloads from the injected page script.
  window.addEventListener('message', (event) => {
    try {
      const data = event && event.data
      if (!data || data.source !== SOURCE) return

      if (data.type === 'graphqlOrders') {
        if (!data.payload) return

        const url =
          typeof data.url === 'string'
            ? data.url
            : typeof document !== 'undefined' && document.location
              ? document.location.href
              : null
        const capturedAt = typeof data.capturedAt === 'string' ? data.capturedAt : new Date().toISOString()

        chrome.storage.local.set({
          [COSTCO_BULK_GRAPHQL_STORAGE_KEY]: {
            url,
            capturedAt,
            payload: data.payload,
          },
        })

        return
      }

      // Capture order-details query template (query + operationName), so the background job can fetch
      // details for each orderHeaderId during bulk import.
      if (data.type === 'graphqlOrderDetails') {
        const order = data.payload?.data?.getOrderDetails
        const number = order?.orderNumber || order?.sourceOrderNumber
        if (number && isCostcoOrderDetailsRoute()) {
          automationOrderContext = {
            url: window.location.href,
            orderNumber: String(number).trim(),
            email: order.emailAddress || null,
          }
        }
        const request = data.request && typeof data.request === 'object' ? data.request : null
        const query = request && typeof request.query === 'string' ? request.query : null
        if (!query || !query.trim()) return
        const operationName = request && typeof request.operationName === 'string' ? request.operationName : null

        chrome.storage.local.set({
          [COSTCO_ORDER_DETAILS_GRAPHQL_TEMPLATE_STORAGE_KEY]: {
            query: query.trim(),
            operationName: operationName || null,
            capturedAt: typeof data.capturedAt === 'string' ? data.capturedAt : new Date().toISOString(),
          },
        })

        // Also store the latest captured order-details payload keyed by orderHeaderId (from request.variables.orderNumbers[0]).
        let orderHeaderId = null
        try {
          const vars = request && request.variables && typeof request.variables === 'object' ? request.variables : null
          const orderNumbers = vars && Array.isArray(vars.orderNumbers) ? vars.orderNumbers : []
          if (orderNumbers.length > 0 && orderNumbers[0] != null) orderHeaderId = String(orderNumbers[0]).trim()
        } catch {
          orderHeaderId = null
        }

        if (orderHeaderId && data.payload) {
          chrome.storage.local.set({
            [COSTCO_ORDER_DETAILS_CAPTURE_STORAGE_KEY]: {
              orderHeaderId,
              url:
                typeof data.url === 'string'
                  ? data.url
                  : typeof document !== 'undefined' && document.location
                    ? document.location.href
                    : null,
              capturedAt: typeof data.capturedAt === 'string' ? data.capturedAt : new Date().toISOString(),
              payload: data.payload,
            },
          })
        }
      }
    } catch {
      // never break the page
    }
  })

  function loadCachedGraphql() {
    return new Promise((resolve) => {
      try {
        chrome.storage.local.get(COSTCO_BULK_GRAPHQL_STORAGE_KEY, (s) => {
          const v = s && s[COSTCO_BULK_GRAPHQL_STORAGE_KEY] ? s[COSTCO_BULK_GRAPHQL_STORAGE_KEY] : null
          resolve(v || null)
        })
      } catch {
        resolve(null)
      }
    })
  }

  function waitForNextGraphqlCapture(timeoutMs) {
    const t = typeof timeoutMs === 'number' && timeoutMs > 0 ? timeoutMs : 6000
    return new Promise((resolve) => {
      let done = false
      let timer = null

      function finish(value) {
        if (done) return
        done = true
        try {
          window.removeEventListener('message', onMessage)
        } catch {}
        if (timer) clearTimeout(timer)
        resolve(value || null)
      }

      function onMessage(event) {
        try {
          const data = event && event.data
          if (!data || data.source !== SOURCE || data.type !== 'graphqlOrders') return
          finish({
            url: data.url || (document && document.location ? document.location.href : null),
            capturedAt: data.capturedAt || new Date().toISOString(),
            payload: data.payload || null,
          })
        } catch {
          // ignore
        }
      }

      window.addEventListener('message', onMessage)
      timer = setTimeout(() => finish(null), t)
    })
  }

  function getOrderDetailsUrl(orderNumber) {
    const suffix = '/orderdetails/' + encodeURIComponent(orderNumber)
    for (const link of document.querySelectorAll('a[href*="/orderdetails/" i]')) {
      const url = new URL(link.getAttribute('href'), document.baseURI)
      if (url.origin === location.origin && url.hash.split('?')[0].endsWith(suffix)) return url.href
    }

    const appId = /^#\/app\/([^/]+)\//.exec(location.hash)?.[1]
    return appId ? new URL('/myaccount/#/app/' + appId + suffix, location.origin).href : null
  }

  // Allow the background/popup to request the cached capture.
  if (chrome.runtime && chrome.runtime.onMessage) {
    chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
      try {
        if (!message || message.store !== 'costco') return
        if (message.type === 'costcoCaptureInvoice') {
          globalThis.OrderManagerCostcoInvoice.captureCostcoInvoice(message.orderNumber)
            .then(sendResponse, () => sendResponse(null))
          return true
        }

        if (message.type === 'costcoGetOrdersGraphql') {
          ;(async () => {
            const cached = await loadCachedGraphql()
            if (cached && cached.payload) {
              sendResponse({ success: true, cached: true, ...cached })
              return
            }
            const next = await waitForNextGraphqlCapture(7000)
            if (next && next.payload) {
              sendResponse({ success: true, cached: false, ...next })
              return
            }
            sendResponse({
              success: false,
              error: 'No Costco orders GraphQL payload captured yet. Refresh the Costco orders page and try again.',
            })
          })()

          return true
        }

        if (message.type === 'costcoGetOrderDetailsGraphqlTemplate') {
          chrome.storage.local.get(COSTCO_ORDER_DETAILS_GRAPHQL_TEMPLATE_STORAGE_KEY, (s) => {
            const v =
              s && s[COSTCO_ORDER_DETAILS_GRAPHQL_TEMPLATE_STORAGE_KEY] ? s[COSTCO_ORDER_DETAILS_GRAPHQL_TEMPLATE_STORAGE_KEY] : null
            if (!v || !v.query) {
              sendResponse({
                success: false,
                error:
                  'No Costco order-details GraphQL query template captured yet. Click any “View Order Details” button on the Costco Orders & Purchases page once, then try again.',
              })
              return
            }
            sendResponse({ success: true, template: v })
          })
          return true
        }

        if (message.type === 'costcoGetOrderDetailsUrl') {
          const orderNumber = String(message.orderNumber || '').trim()
          if (!orderNumber) {
            sendResponse({ success: false, error: 'Missing order number.' })
            return true
          }
          const url = getOrderDetailsUrl(orderNumber)
          if (!url) {
            sendResponse({ success: false, error: 'Could not find order details link for order ' + orderNumber })
            return true
          }
          sendResponse({ success: true, url })
          return true
        }

        if (message.type === 'costcoClearOrderDetailsCapture') {
          try {
            chrome.storage.local.remove(COSTCO_ORDER_DETAILS_CAPTURE_STORAGE_KEY, () => {
              sendResponse({ success: true })
            })
          } catch {
            sendResponse({ success: true })
          }
          return true
        }
      } catch (e) {
        try {
          sendResponse({ success: false, error: String(e && e.message ? e.message : e) })
        } catch {}
      }
    })
  }
})()

