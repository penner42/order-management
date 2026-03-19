;(function () {
  'use strict'

  const COSTCO_BULK_GRAPHQL_STORAGE_KEY = 'costcoOrdersGraphql'
  const COSTCO_ORDER_DETAILS_GRAPHQL_TEMPLATE_STORAGE_KEY = 'costcoOrderDetailsGraphqlTemplate'
  const COSTCO_ORDER_DETAILS_CAPTURE_STORAGE_KEY = 'costcoOrderDetailsGraphqlCapture'
  const SOURCE = 'order-manager-costco'

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
    if (isCostcoCaptureRoute()) injectPageScript()
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

  function getOrderDetailsUrlForHeaderId(orderHeaderId) {
    const target = orderHeaderId != null ? String(orderHeaderId).trim() : ''
    if (!target) return null

    // 1) Preferred: find the actual link Costco rendered (may be relative).
    const anchors = Array.from(document.querySelectorAll('a[href]'))
    for (let i = 0; i < anchors.length; i++) {
      const el = anchors[i]
      try {
        const href = el.getAttribute('href') || ''
        const lower = href.toLowerCase()
        if (!lower.includes('orderdetails')) continue
        if (!href.includes(target)) continue
        return new URL(href, document.location.href).toString()
      } catch {
        // ignore
      }
    }

    // 2) Fallback: construct the SPA route URL from the current app id in the hash.
    // The orders list URL looks like: /myaccount/#/app/<graphQlClientId>/ordersandpurchases
    let appId = null
    try {
      const h = window.location.hash || ''
      const m = /#\/app\/([^/]+)\//.exec(h)
      if (m && m[1]) appId = String(m[1]).trim()
    } catch {
      appId = null
    }
    if (!appId) return null

    const base = 'https://www.costco.com/myaccount/#/app/' + encodeURIComponent(appId) + '/orderdetails'
    const candidates = [
      // Most likely: matches GraphQL variables.orderNumbers=["<orderHeaderId>"]
      base + '?orderNumbers=' + encodeURIComponent(target),
      // Alternate singular param
      base + '?orderNumber=' + encodeURIComponent(target),
      // Alternate path segment form
      base + '/' + encodeURIComponent(target),
    ]
    return candidates[0]
  }

  // Allow the background/popup to request the cached capture.
  if (chrome.runtime && chrome.runtime.onMessage) {
    chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
      try {
        if (!message || message.store !== 'costco') return
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
          const headerId = message.orderHeaderId != null ? String(message.orderHeaderId).trim() : ''
          if (!headerId) {
            sendResponse({ success: false, error: 'Missing orderHeaderId.' })
            return true
          }
          const url = getOrderDetailsUrlForHeaderId(headerId)
          if (!url) {
            sendResponse({ success: false, error: 'Could not find order details link for orderHeaderId ' + headerId })
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

