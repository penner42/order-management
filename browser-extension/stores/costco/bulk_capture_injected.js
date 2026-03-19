;(function () {
  'use strict'

  if (window.__omCostcoGraphqlHookInstalled) return
  window.__omCostcoGraphqlHookInstalled = true

  const SOURCE = 'order-manager-costco'
  const GRAPHQL_URL_FRAGMENT = 'ecom-api.costco.com/ebusiness/order/v1/orders/graphql'

  function safeJsonParse(text) {
    try {
      return JSON.parse(text)
    } catch {
      return null
    }
  }

  function looksLikeOrdersGraphqlPayload(obj) {
    try {
      const data = obj && obj.data
      const go = data && data.getOnlineOrders
      return Array.isArray(go) && go.length > 0 && Array.isArray(go[0] && go[0].bcOrders)
    } catch {
      return false
    }
  }

  function looksLikeOrderDetailsGraphqlPayload(obj) {
    try {
      const data = obj && obj.data
      const od = data && data.getOrderDetails
      return !!(od && (od.orderNumber || (Array.isArray(od.shipToAddress) && od.shipToAddress.length > 0)))
    } catch {
      return false
    }
  }

  function postCapture(type, payload, url, request) {
    try {
      if (!payload || typeof payload !== 'object') return
      if (type === 'graphqlOrders') {
        if (!looksLikeOrdersGraphqlPayload(payload)) return
      } else if (type === 'graphqlOrderDetails') {
        if (!looksLikeOrderDetailsGraphqlPayload(payload)) return
      } else {
        return
      }
      window.postMessage(
        {
          source: SOURCE,
          type,
          url: url || (document && document.location ? document.location.href : null),
          payload,
          request: request || null,
          capturedAt: new Date().toISOString(),
        },
        '*'
      )
    } catch {
      // never break the page
    }
  }

  function extractGraphqlRequestMetaFromFetchArgs(args) {
    try {
      const init = args && args[1] && typeof args[1] === 'object' ? args[1] : null
      if (!init) return null
      const body = init.body
      if (typeof body !== 'string') return null
      const parsed = safeJsonParse(body)
      if (!parsed || typeof parsed !== 'object') return null
      return {
        operationName: typeof parsed.operationName === 'string' ? parsed.operationName : null,
        query: typeof parsed.query === 'string' ? parsed.query : null,
        variables: parsed.variables && typeof parsed.variables === 'object' ? parsed.variables : null,
      }
    } catch {
      return null
    }
  }

  function extractGraphqlRequestMetaFromXhrSendArgs(args) {
    try {
      const body = args && args[0]
      if (typeof body !== 'string') return null
      const parsed = safeJsonParse(body)
      if (!parsed || typeof parsed !== 'object') return null
      return {
        operationName: typeof parsed.operationName === 'string' ? parsed.operationName : null,
        query: typeof parsed.query === 'string' ? parsed.query : null,
        variables: parsed.variables && typeof parsed.variables === 'object' ? parsed.variables : null,
      }
    } catch {
      return null
    }
  }

  function classifyGraphqlResponse(parsed) {
    if (!parsed || typeof parsed !== 'object') return null
    if (looksLikeOrdersGraphqlPayload(parsed)) return 'graphqlOrders'
    if (looksLikeOrderDetailsGraphqlPayload(parsed)) return 'graphqlOrderDetails'
    return null
  }

  // --- fetch() hook ---
  try {
    const originalFetch = window.fetch
    if (typeof originalFetch === 'function') {
      window.fetch = function (...args) {
        const p = originalFetch.apply(this, args)
        try {
          const url = args && args[0] ? String(args[0]) : ''
          if (url && url.includes(GRAPHQL_URL_FRAGMENT)) {
            const requestMeta = extractGraphqlRequestMetaFromFetchArgs(args)
            p.then((resp) => {
              try {
                if (!resp || typeof resp.clone !== 'function') return
                const cloned = resp.clone()
                cloned
                  .text()
                  .then((t) => {
                    const parsed = safeJsonParse(t)
                    const type = classifyGraphqlResponse(parsed)
                    if (type) postCapture(type, parsed, url, requestMeta)
                  })
                  .catch(function () {})
              } catch {
                // ignore
              }
            }).catch(function () {})
          }
        } catch {
          // ignore
        }
        return p
      }
    }
  } catch {
    // ignore
  }

  // --- XHR hook ---
  try {
    const XHR = window.XMLHttpRequest
    if (XHR && XHR.prototype) {
      const origOpen = XHR.prototype.open
      const origSend = XHR.prototype.send

      XHR.prototype.open = function (method, url) {
        try {
          this.__omCostcoUrl = url ? String(url) : ''
        } catch {
          this.__omCostcoUrl = ''
        }
        return origOpen.apply(this, arguments)
      }

      XHR.prototype.send = function () {
        try {
          const url = this.__omCostcoUrl ? String(this.__omCostcoUrl) : ''
          if (url && url.includes(GRAPHQL_URL_FRAGMENT)) {
            const requestMeta = extractGraphqlRequestMetaFromXhrSendArgs(arguments)
            this.addEventListener('load', () => {
              try {
                const text = typeof this.responseText === 'string' ? this.responseText : ''
                const parsed = safeJsonParse(text)
                const type = classifyGraphqlResponse(parsed)
                if (type) postCapture(type, parsed, url, requestMeta)
              } catch {
                // ignore
              }
            })
          }
        } catch {
          // ignore
        }
        return origSend.apply(this, arguments)
      }
    }
  } catch {
    // ignore
  }
})()

