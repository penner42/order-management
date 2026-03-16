;(function () {
  'use strict'

  if (window.__wmOrdersHookInstalled) return
  window.__wmOrdersHookInstalled = true

  const SOURCE = 'order-manager-walmart'

  let currentUrl = window.location.href
  let ordersListCache = null
  let orderDetailCache = null

  function isOrdersListPage() {
    try {
      return window.location.pathname === '/orders'
    } catch {
      return false
    }
  }

  function isOrderDetailPage() {
    try {
      const path = window.location.pathname || ''
      return path.startsWith('/orders/') || path.includes('/order-details')
    } catch {
      return false
    }
  }

  function postEvent(type, payload) {
    try {
      const message = {
        source: SOURCE,
        type,
        url: currentUrl,
        payload,
      }

      // Post to the page / other extension contexts.
      window.postMessage(message, '*')

      // Helpful during development.
      if (typeof console !== 'undefined' && console.debug) {
        console.debug('[order-manager][walmart]', type, message)
      }
    } catch {
      // never break the page
    }
  }

  function postReset(newUrl) {
    currentUrl = newUrl || window.location.href
    ordersListCache = null
    orderDetailCache = null
    postEvent('reset', { url: currentUrl })
  }


  function getNextData() {
    try {
      if (window.__NEXT_DATA__) return window.__NEXT_DATA__
    } catch {
      // ignore
    }

    try {
      const script = document.querySelector('script#__NEXT_DATA__')
      if (script && script.textContent) {
        return JSON.parse(script.textContent)
      }
    } catch {
      // ignore
    }

    return null
  }

  function extractOrdersFromNextData(nextData) {
    if (!nextData || typeof nextData !== 'object') return null
    const payload = {
      kind: 'ordersList',
      raw: nextData,
      orders: null,
      paginationInfo: null,
    }

    try {
      const pageProps = nextData.props && nextData.props.pageProps
      if (pageProps && typeof pageProps === 'object') {
        // Best-effort guesses; structure may differ but we always keep raw.
        if (Array.isArray(pageProps.orders)) {
          payload.orders = pageProps.orders
        } else if (pageProps.orderHistory && Array.isArray(pageProps.orderHistory.orders)) {
          payload.orders = pageProps.orderHistory.orders
        }

        // Walmart redesigned purchase history: orders live inside
        // pageProps.phRedesignInitialData.data.purchaseHistory
        if (!payload.orders) {
          try {
            const ph =
              pageProps.phRedesignInitialData &&
              pageProps.phRedesignInitialData.data &&
              pageProps.phRedesignInitialData.data.purchaseHistory
            if (ph) {
              if (Array.isArray(ph.orders)) {
                payload.orders = ph.orders
              }
              if (ph.paginationInfo) {
                payload.paginationInfo = ph.paginationInfo
              }
            }
          } catch {
            // ignore
          }
        }

        if (!payload.paginationInfo) {
          if (pageProps.paginationInfo) {
            payload.paginationInfo = pageProps.paginationInfo
          } else if (
            pageProps.orderHistory &&
            pageProps.orderHistory.paginationInfo
          ) {
            payload.paginationInfo = pageProps.orderHistory.paginationInfo
          }
        }
      }
    } catch {
      // ignore and fall back to raw
    }

    return payload
  }

  function extractOrderDetailFromNextData(nextData) {
    if (!nextData || typeof nextData !== 'object') return null

    const payload = {
      kind: 'orderDetail',
      raw: nextData,
      order: null,
    }

    try {
      const pageProps = nextData.props && nextData.props.pageProps
      if (pageProps && typeof pageProps === 'object') {
        if (pageProps.order) {
          payload.order = pageProps.order
        } else if (pageProps.orderDetail) {
          payload.order = pageProps.orderDetail
        }
      }
    } catch {
      // ignore and fall back to raw
    }

    return payload
  }

  function processInitialNextData() {
    const nextData = getNextData()
    if (!nextData) return

    try {
      if (isOrdersListPage()) {
        const extracted = extractOrdersFromNextData(nextData)
        if (extracted) {
          ordersListCache = extracted
          postEvent('ordersListPage', extracted)
        }
      } else if (isOrderDetailPage()) {
        const extracted = extractOrderDetailFromNextData(nextData)
        if (extracted) {
          orderDetailCache = extracted
          postEvent('orderDetail', extracted)
        }
      }
    } catch {
      // ignore
    }
  }

  function handleUrlChange() {
    try {
      const newUrl = window.location.href
      if (newUrl === currentUrl) return
      postReset(newUrl)
      // #region agent log
      try {
        fetch('http://localhost:7823/ingest/728b760a-8edb-455e-a019-596e2988cd87', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-Debug-Session-Id': 'dc3cbd',
          },
          body: JSON.stringify({
            sessionId: 'dc3cbd',
            runId: 'pre-fix',
            hypothesisId: 'H2',
            location: 'orders.js:handleUrlChange',
            message: 'URL changed on Walmart orders site',
            data: {
              previousUrl: currentUrl,
              newUrl,
              isOrdersList: isOrdersListPage(),
              isOrderDetail: isOrderDetailPage(),
            },
            timestamp: Date.now(),
          }),
        }).catch(() => {})
      } catch {}
      // #endregion agent log
      processInitialNextData()
    } catch {
      // ignore
    }
  }

  function installUrlWatcher() {
    try {
      const originalPushState = history.pushState
      const originalReplaceState = history.replaceState

      function wrap(fn) {
        return function (...args) {
          let result
          try {
            result = fn.apply(this, args)
          } catch (e) {
            throw e
          } finally {
            try {
              // Defer to ensure location has been updated.
              setTimeout(handleUrlChange, 0)
            } catch {
              // ignore
            }
          }
          return result
        }
      }

      if (typeof originalPushState === 'function') {
        history.pushState = wrap(originalPushState)
      }
      if (typeof originalReplaceState === 'function') {
        history.replaceState = wrap(originalReplaceState)
      }

      window.addEventListener('popstate', function () {
        handleUrlChange()
      })
    } catch {
      // ignore
    }
  }

  function getUrlFromInput(input) {
    let urlString = null
    try {
      if (typeof input === 'string') {
        urlString = input
      } else if (input && typeof URL !== 'undefined' && input instanceof URL) {
        urlString = input.href
      } else if (input && typeof Request !== 'undefined' && input instanceof Request) {
        urlString = input.url
      } else if (input && typeof input.url === 'string') {
        urlString = input.url
      }
    } catch {
      // ignore
    }
    return urlString
  }

  function classifyOrdersGraphql(input) {
    const urlString = getUrlFromInput(input)
    if (!urlString) return null

    try {
      const u = new URL(urlString, window.location.origin)
      if (u.hostname !== 'www.walmart.com') return null
      const path = u.pathname || ''

      if (path.startsWith('/orchestra/orders/graphql/getOrder/')) {
        return { kind: 'detail', url: u.href }
      }
      if (path.startsWith('/orchestra/cph/graphql/PurchaseHistoryV3/')) {
        return { kind: 'list', url: u.href }
      }
    } catch {
      // ignore
    }

    return null
  }

  function handleOrdersListResponse(json, url) {
    if (!json) return
    try {
      const payload = {
        kind: 'ordersList',
        url: url || null,
        raw: json,
        orders: null,
        paginationInfo: null,
      }

      try {
        const data = json.data || json
        if (Array.isArray(data.orders)) {
          payload.orders = data.orders
        } else if (data.purchaseHistory && Array.isArray(data.purchaseHistory.orders)) {
          payload.orders = data.purchaseHistory.orders
        }

        if (data.paginationInfo) {
          payload.paginationInfo = data.paginationInfo
        } else if (
          data.purchaseHistory &&
          data.purchaseHistory.paginationInfo
        ) {
          payload.paginationInfo = data.purchaseHistory.paginationInfo
        }
      } catch {
        // fall back to raw only
      }

      ordersListCache = payload
      postEvent('ordersListPage', payload)
    } catch {
      // ignore
    }
  }

  function handleOrderDetailResponse(json, url) {
    if (!json) return
    try {
      const payload = {
        kind: 'orderDetail',
        url: url || null,
        raw: json,
        order: null,
      }

      try {
        const data = json.data || json
        if (data.order) {
          payload.order = data.order
        } else if (data.orderDetail) {
          payload.order = data.orderDetail
        }
      } catch {
        // fall back to raw only
      }

      orderDetailCache = payload
      postEvent('orderDetail', payload)
    } catch {
      // ignore
    }
  }

  function installFetchInterceptor() {
    try {
      const originalFetch = window.fetch
      if (!originalFetch) return

      window.fetch = function interceptedFetch(...args) {
        let classification = null
        try {
          classification = classifyOrdersGraphql(args[0])
        } catch {
          classification = null
        }

        const responsePromise = originalFetch.apply(this, args)

        if (classification) {
          responsePromise
            .then(function (response) {
              try {
                const clone = response.clone()
                clone
                  .json()
                  .then(function (json) {
                    try {
                      if (classification.kind === 'detail') {
                        handleOrderDetailResponse(json, clone.url || classification.url)
                      } else if (classification.kind === 'list') {
                        handleOrdersListResponse(json, clone.url || classification.url)
                      }
                    } catch {
                      // ignore
                    }
                  })
                  .catch(function () {
                    // ignore JSON parse errors
                  })
              } catch {
                // ignore
              }
            })
            .catch(function () {
              // ignore fetch errors
            })
        }

        return responsePromise
      }
    } catch {
      // ignore
    }
  }

  function installXhrInterceptor() {
    try {
      if (typeof XMLHttpRequest === 'undefined') return

      const OriginalXHR = XMLHttpRequest
      const originalOpen = OriginalXHR.prototype.open
      const originalSend = OriginalXHR.prototype.send

      OriginalXHR.prototype.open = function (method, url, ...rest) {
        try {
          this.__wmOrdersClassification = classifyOrdersGraphql(url)
        } catch {
          this.__wmOrdersClassification = null
        }
        return originalOpen.call(this, method, url, ...rest)
      }

      OriginalXHR.prototype.send = function (...args) {
        try {
          if (this.__wmOrdersClassification) {
            const xhr = this
            const onLoad = function () {
              try {
                if (xhr.status >= 200 && xhr.status < 300 && xhr.responseText) {
                  let json = null
                  try {
                    json = JSON.parse(xhr.responseText)
                  } catch {
                    json = null
                  }
                  if (!json) return

                  if (xhr.__wmOrdersClassification.kind === 'detail') {
                    handleOrderDetailResponse(json, xhr.responseURL || xhr.__wmOrdersClassification.url)
                  } else if (xhr.__wmOrdersClassification.kind === 'list') {
                    handleOrdersListResponse(json, xhr.responseURL || xhr.__wmOrdersClassification.url)
                  }
                }
              } catch {
                // ignore
              }
            }

            this.addEventListener('load', onLoad)
          }
        } catch {
          // ignore
        }

        return originalSend.apply(this, args)
      }
    } catch {
      // ignore
    }
  }

  function init() {
    try {
      postReset(currentUrl)
      processInitialNextData()
      installUrlWatcher()
      installFetchInterceptor()
      installXhrInterceptor()
    } catch {
      // ignore
    }
  }

  // ---------------------------------------------------------------------------
  // Bulk helpers (used via extension <-> page RPC)
  // ---------------------------------------------------------------------------

  function extractOrderNumbersFromListPayload(payload) {
    const orders = []
    if (!payload || !Array.isArray(payload.orders)) return orders
    for (let i = 0; i < payload.orders.length; i++) {
      const o = payload.orders[i] || {}
      const rawId =
        o.legacyOrderId != null
          ? o.legacyOrderId
          : o.orderNumber != null
            ? o.orderNumber
            : o.orderId != null
              ? o.orderId
              : o.id != null
                ? o.id
                : null
      if (rawId == null) continue
      const orderNumber = String(rawId)
      orders.push({
        orderNumber,
        detailButtonId: 'view-order-details-link-' + orderNumber,
      })
    }
    return orders
  }

  function findNextPageButton() {
    try {
      const candidates = [
        'button[aria-label="Next"]',
        'button[aria-label*="Next"]',
        '[data-automation-id*="next"]',
      ]
      for (let i = 0; i < candidates.length; i++) {
        const el = document.querySelector(candidates[i])
        if (el) return el
      }
    } catch {
      // ignore
    }
    return null
  }

  function waitForNextOrdersList(previousRaw, timeoutMs) {
    return new Promise((resolve) => {
      const start = Date.now()

      function check() {
        try {
          if (ordersListCache && ordersListCache.raw && ordersListCache.raw !== previousRaw) {
            resolve(ordersListCache)
            return
          }
        } catch {
          // ignore
        }
        if (Date.now() - start >= timeoutMs) {
          resolve(null)
          return
        }
        setTimeout(check, 300)
      }

      check()
    })
  }
 
  async function collectOrdersAcrossPages(maxPages) {
    const safeMaxPages = typeof maxPages === 'number' && maxPages > 0 ? Math.floor(maxPages) : 1
    const limit = Math.min(safeMaxPages, 50)
    const allOrders = []
    let pagesCollected = 0

    // Ensure we have the current page's orders at least once, even if no
    // GraphQL list request has been intercepted yet.
    if (!ordersListCache && isOrdersListPage()) {
      const nextData = getNextData()
      if (nextData) {
        const extracted = extractOrdersFromNextData(nextData)
        if (extracted) {
          ordersListCache = extracted
        }
      }
    }

    // Start from whatever we already have for this URL.
    if (ordersListCache) {
      const firstBatch = extractOrderNumbersFromListPayload(ordersListCache)
      for (let i = 0; i < firstBatch.length; i++) {
        allOrders.push(firstBatch[i])
      }
      pagesCollected++
    }

    while (pagesCollected < limit) {
      const nextBtn = findNextPageButton()
      if (!nextBtn) break

      const previousRaw = ordersListCache ? ordersListCache.raw : null
      try {
        nextBtn.click()
      } catch {
        break
      }

      const nextPayload = await waitForNextOrdersList(previousRaw, 8000)
      if (!nextPayload) {
        break
      }

      const batch = extractOrderNumbersFromListPayload(nextPayload)
      if (batch.length === 0) {
        pagesCollected++
        break
      }
      for (let i = 0; i < batch.length; i++) {
        allOrders.push(batch[i])
      }
      pagesCollected++
    }

    // De-duplicate by orderNumber while preserving order.
    const seen = new Set()
    const deduped = []
    for (let i = 0; i < allOrders.length; i++) {
      const o = allOrders[i]
      if (!o || !o.orderNumber) continue
      if (seen.has(o.orderNumber)) continue
      seen.add(o.orderNumber)
      deduped.push(o)
    }

    return { orders: deduped, pagesCollected }
  }

  function openOrderDetailByNumber(orderNumber) {
    if (!orderNumber) return
    const id = 'view-order-details-link-' + String(orderNumber)
    try {
      const el =
        document.querySelector('[data-automation-id="' + id + '"]') ||
        document.getElementById(id)
      if (el && typeof el.click === 'function') {
        el.click()
      }
    } catch {
      // ignore
    }
  }

  function installExtensionRpcListener() {
    try {
      window.addEventListener('message', (event) => {
        try {
          const data = event.data
          if (!data || data.source !== 'order-manager-walmart-extension') return

          if (data.type === 'collectOrdersAcrossPages') {
            const maxPages = data.maxPages
            collectOrdersAcrossPages(maxPages)
              .then((result) => {
                window.postMessage(
                  {
                    source: 'order-manager-walmart-extension',
                    type: 'collectOrdersResult',
                    orders: result.orders,
                    pagesCollected: result.pagesCollected,
                    error: null,
                  },
                  '*'
                )
              })
              .catch((err) => {
                window.postMessage(
                  {
                    source: 'order-manager-walmart-extension',
                    type: 'collectOrdersResult',
                    orders: [],
                    pagesCollected: 0,
                    error: err && err.message ? String(err.message) : 'Unknown error',
                  },
                  '*'
                )
              })
          } else if (data.type === 'openOrderDetail') {
            openOrderDetailByNumber(data.orderNumber)
          }
        } catch {
          // never break the page
        }
      })
    } catch {
      // ignore
    }
  }

  if (document.readyState === 'complete' || document.readyState === 'interactive') {
    init()
    installExtensionRpcListener()
  } else {
    window.addEventListener('DOMContentLoaded', () => {
      init()
      installExtensionRpcListener()
    })
  }
})()

