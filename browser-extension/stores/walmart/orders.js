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

        if (pageProps.paginationInfo) {
          payload.paginationInfo = pageProps.paginationInfo
        } else if (
          pageProps.orderHistory &&
          pageProps.orderHistory.paginationInfo
        ) {
          payload.paginationInfo = pageProps.orderHistory.paginationInfo
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

  if (document.readyState === 'complete' || document.readyState === 'interactive') {
    init()
  } else {
    window.addEventListener('DOMContentLoaded', init)
  }
})()

