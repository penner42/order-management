;(function () {
  'use strict'

  const TARGET_HOST = 'api.usabuying.group'
  const STORAGE_KEY = 'usabgBearerToken'

  function isTargetFetch(input) {
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
      // ignore and treat as non-target
    }
    if (!urlString) return false
    try {
      const u = new URL(urlString, window.location.origin)
      return u.hostname === TARGET_HOST
    } catch {
      return false
    }
  }

  function extractAuthorizationHeader(headers) {
    if (!headers) return null

    try {
      if (typeof Headers !== 'undefined' && headers instanceof Headers) {
        return headers.get('authorization') || headers.get('Authorization')
      }
    } catch {
      // fall through to object handling
    }

    if (Array.isArray(headers)) {
      for (const entry of headers) {
        if (!entry || entry.length < 2) continue
        const key = String(entry[0] || '').toLowerCase()
        if (key === 'authorization') return entry[1]
      }
      return null
    }

    if (typeof headers === 'object') {
      for (const key of Object.keys(headers)) {
        if (key.toLowerCase() === 'authorization') {
          return headers[key]
        }
      }
    }

    return null
  }

  function getTokenFromArgs(args) {
    if (!args || args.length === 0) return null

    let headers = null

    try {
      const first = args[0]
      if (first && typeof Request !== 'undefined' && first instanceof Request) {
        headers = first.headers
      }
    } catch {
      // ignore
    }

    if (!headers && args.length > 1 && args[1] && typeof args[1] === 'object') {
      headers = args[1].headers
    }

    const authHeader = extractAuthorizationHeader(headers)
    if (!authHeader || typeof authHeader !== 'string') return null

    const match = /^Bearer\s+(.+)$/i.exec(authHeader.trim())
    return match ? match[1] : null
  }

  function cacheToken(token) {
    if (!token) return
    try {
      window.postMessage(
        {
          source: 'order-manager-usabg',
          type: 'usabgToken',
          token,
        },
        '*'
      )
    } catch {
      // ignore
    }
  }

  const originalFetch = window.fetch
  if (originalFetch) {
    window.fetch = function interceptedFetch(...args) {
      try {
        if (isTargetFetch(args[0])) {
          const token = getTokenFromArgs(args)
          if (token) {
            cacheToken(token)
          }
        }
      } catch {
        // never break page fetches
      }

      return originalFetch.apply(this, args)
    }
  }

  if (typeof XMLHttpRequest !== 'undefined') {
    const OriginalXHR = XMLHttpRequest
    const originalOpen = OriginalXHR.prototype.open
    const originalSetRequestHeader = OriginalXHR.prototype.setRequestHeader

    OriginalXHR.prototype.open = function (method, url, ...rest) {
      try {
        this.__usabgIsTarget = isTargetFetch(url)
      } catch {
        this.__usabgIsTarget = false
      }
      return originalOpen.call(this, method, url, ...rest)
    }

    OriginalXHR.prototype.setRequestHeader = function (name, value) {
      try {
        if (
          this.__usabgIsTarget &&
          typeof name === 'string' &&
          name.toLowerCase() === 'authorization' &&
          typeof value === 'string'
        ) {
          const match = /^Bearer\s+(.+)$/i.exec(value.trim())
          if (match && match[1]) {
            cacheToken(match[1])
          }
        }
      } catch {
        // ignore and never break XHR
      }
      return originalSetRequestHeader.call(this, name, value)
    }
  }
})()

