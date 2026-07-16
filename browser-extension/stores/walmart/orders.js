;(function () {
  'use strict'

  if (window.__wmOrdersHookInstalled) return
  window.__wmOrdersHookInstalled = true

  const SOURCE = 'order-manager-walmart'
  const DEBUG =
    (function () {
      try {
        return !!(window && window.localStorage && localStorage.getItem('order-manager-debug') === '1')
      } catch {
        return false
      }
    })()

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
      if (DEBUG && typeof console !== 'undefined' && console.debug) {
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
    cancelPendingInvoiceCapture()
    postEvent('reset', { url: currentUrl })
  }

  // ---------------------------------------------------------------------------
  // Invoice HTML capture (order detail page → rendered HTML for PDF conversion)
  // ---------------------------------------------------------------------------

  // Fallback delay when we cannot verify render readiness via the order number.
  const INVOICE_CAPTURE_DELAY_MS = 2500
  // Poll interval / cap while waiting for the order details to finish rendering.
  const INVOICE_RENDER_POLL_MS = 500
  const INVOICE_RENDER_WAIT_MAX_MS = 15000
  // Keep the serialized document well under chrome.storage.local quotas.
  const INVOICE_MAX_CSS_CHARS = 3 * 1024 * 1024
  const INVOICE_MAX_HTML_CHARS = 6 * 1024 * 1024

  let invoiceCaptureTimer = null
  let invoiceCaptureGeneration = 0

  function cancelPendingInvoiceCapture() {
    invoiceCaptureGeneration++
    try {
      if (invoiceCaptureTimer != null) {
        clearTimeout(invoiceCaptureTimer)
        invoiceCaptureTimer = null
      }
    } catch {
      // ignore
    }
  }

  async function collectStylesheetCss() {
    const parts = []
    let total = 0
    let sheets = []
    try {
      sheets = Array.from(document.styleSheets || [])
    } catch {
      sheets = []
    }

    for (let i = 0; i < sheets.length; i++) {
      if (total >= INVOICE_MAX_CSS_CHARS) break
      const sheet = sheets[i]
      let text = null

      let rules = null
      try {
        rules = sheet.cssRules
      } catch {
        rules = null
      }

      if (rules) {
        try {
          const chunks = []
          for (let r = 0; r < rules.length; r++) {
            chunks.push(rules[r].cssText)
          }
          text = chunks.join('\n')
        } catch {
          text = null
        }
      } else if (sheet.href) {
        // Cross-origin stylesheet: fetch its text directly (page context).
        try {
          const res = await fetch(sheet.href, { credentials: 'omit' })
          if (res && res.ok) {
            text = await res.text()
          }
        } catch {
          text = null
        }
      }

      if (text) {
        parts.push(text)
        total += text.length
      }
    }

    let css = parts.join('\n')
    if (css.length > INVOICE_MAX_CSS_CHARS) {
      css = css.slice(0, INVOICE_MAX_CSS_CHARS)
    }
    return css
  }

  function toAbsoluteUrl(value) {
    try {
      if (!value) return null
      return new URL(value, window.location.href).href
    } catch {
      return null
    }
  }

  function cleanInvoiceClone(clone) {
    // Remove scripts, external resources we re-inline, and page chrome that has
    // no place on an invoice.
    const removeSelectors = [
      'script',
      'noscript',
      'iframe',
      'template',
      'link',
      'style',
      'header',
      'footer',
      'nav',
      '[role="navigation"]',
      '[role="banner"]',
      '[role="contentinfo"]',
      '[data-testid="GlobalHeader"]',
      '[data-testid="GlobalFooter"]',
      '#omni-header',
      '#omni-footer',
    ]
    for (let i = 0; i < removeSelectors.length; i++) {
      let els = []
      try {
        els = clone.querySelectorAll(removeSelectors[i])
      } catch {
        els = []
      }
      for (let j = 0; j < els.length; j++) {
        try {
          els[j].remove()
        } catch {
          // ignore
        }
      }
    }

    // Absolutize image URLs so the backend renderer can fetch them, and drop
    // srcset/lazy-loading attributes the renderer does not understand.
    let imgs = []
    try {
      imgs = clone.querySelectorAll('img')
    } catch {
      imgs = []
    }
    for (let i = 0; i < imgs.length; i++) {
      const img = imgs[i]
      try {
        const abs = toAbsoluteUrl(img.getAttribute('src'))
        if (abs) {
          img.setAttribute('src', abs)
        } else {
          img.remove()
          continue
        }
        img.removeAttribute('srcset')
        img.removeAttribute('sizes')
        img.removeAttribute('loading')
      } catch {
        // ignore
      }
    }
  }

  function findInvoiceContentRoot() {
    // Walmart's own order content lives inside the main content region; other
    // extensions inject their UI directly into <body>, so scoping the capture
    // to this element keeps the invoice free of third-party popups/overlays.
    try {
      return (
        document.querySelector('#maincontent') ||
        document.querySelector('main') ||
        document.querySelector('[role="main"]') ||
        null
      )
    } catch {
      return null
    }
  }

  // Attributes commonly used by lazy-loading libraries to hold the real URL.
  const INVOICE_LAZY_SRC_ATTRS = ['data-src', 'data-lazy-src', 'data-original', 'data-image-src']

  function resolveLiveImageUrl(img) {
    // currentSrc is what the browser actually chose (resolves srcset and
    // JS lazy loaders that already ran).
    try {
      if (img.currentSrc && img.currentSrc.indexOf('data:') !== 0) return img.currentSrc
    } catch {
      // ignore
    }
    const attrs = ['src'].concat(INVOICE_LAZY_SRC_ATTRS)
    for (let i = 0; i < attrs.length; i++) {
      try {
        const value = img.getAttribute(attrs[i])
        if (value && value.indexOf('data:') !== 0) {
          const abs = toAbsoluteUrl(value)
          if (abs) return abs
        }
      } catch {
        // ignore
      }
    }
    // Last resort: an inline data URI (could be a real image, not a placeholder).
    try {
      const src = img.getAttribute('src')
      if (src && src.indexOf('data:') === 0) return src
    } catch {
      // ignore
    }
    return null
  }

  function hydrateCloneImages(liveRoot, cloneRoot) {
    // Copy the browser-resolved image URLs onto the clone. Must run before
    // cleanInvoiceClone() removes elements, while live/clone trees still have
    // identical structure (same querySelectorAll order).
    let liveImgs = []
    let cloneImgs = []
    try {
      liveImgs = liveRoot.querySelectorAll('img')
      cloneImgs = cloneRoot.querySelectorAll('img')
    } catch {
      return
    }
    const n = Math.min(liveImgs.length, cloneImgs.length)
    for (let i = 0; i < n; i++) {
      try {
        const url = resolveLiveImageUrl(liveImgs[i])
        if (url) cloneImgs[i].setAttribute('src', url)
      } catch {
        // ignore
      }
    }
  }

  function escapeHtmlAttr(value) {
    return String(value).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;')
  }

  async function captureInvoiceHtml() {
    if (!isOrderDetailPage()) return null

    const liveRoot = findInvoiceContentRoot() || document.body
    if (!liveRoot) return null

    // Images are NOT waited on here: the capture only resolves their URLs
    // (currentSrc / lazy-loader attributes); the backend's background Chromium
    // render fetches and waits for the actual image data.
    let contentClone = null
    try {
      contentClone = liveRoot.cloneNode(true)
    } catch {
      return null
    }

    try {
      hydrateCloneImages(liveRoot, contentClone)
    } catch {
      // ignore
    }

    try {
      cleanInvoiceClone(contentClone)
    } catch {
      // best effort; continue with whatever we have
    }

    let css = ''
    try {
      css = await collectStylesheetCss()
    } catch {
      css = ''
    }

    // Rebuild a minimal document: html/body classes carried over so Walmart's
    // CSS selectors still match, base href so relative URLs in CSS resolve.
    let htmlClass = ''
    let bodyClass = ''
    try {
      htmlClass = (document.documentElement && document.documentElement.getAttribute('class')) || ''
    } catch {
      // ignore
    }
    try {
      bodyClass = (document.body && document.body.getAttribute('class')) || ''
    } catch {
      // ignore
    }

    let contentHtml = null
    try {
      contentHtml = contentClone.outerHTML
    } catch {
      return null
    }

    function assemble(includeCss) {
      return (
        '<!DOCTYPE html>\n' +
        '<html class="' + escapeHtmlAttr(htmlClass) + '">' +
        '<head>' +
        '<meta charset="utf-8">' +
        '<base href="https://www.walmart.com/">' +
        (includeCss && css ? '<style>' + css + '</style>' : '') +
        '</head>' +
        '<body class="' + escapeHtmlAttr(bodyClass) + '">' +
        contentHtml +
        '</body></html>'
      )
    }

    let html = assemble(true)
    if (html.length > INVOICE_MAX_HTML_CHARS) {
      // Retry without inlined CSS rather than dropping the capture entirely.
      html = assemble(false)
      if (html.length > INVOICE_MAX_HTML_CHARS) return null
    }

    return html
  }

  function getExpectedOrderNumber() {
    try {
      const order = orderDetailCache && orderDetailCache.order
      if (order && order.id != null) return String(order.id)
    } catch {
      // ignore
    }
    try {
      const m = (window.location.pathname || '').match(/\/orders\/([^/]+)/)
      if (m && m[1]) return decodeURIComponent(m[1])
    } catch {
      // ignore
    }
    return null
  }

  function scheduleInvoiceCapture() {
    if (!isOrderDetailPage()) return
    cancelPendingInvoiceCapture()
    const generation = invoiceCaptureGeneration
    const urlAtSchedule = currentUrl
    const orderNumber = getExpectedOrderNumber()
    const start = Date.now()

    function attempt() {
      invoiceCaptureTimer = null
      // Ignore attempts scheduled before an SPA navigation / newer capture.
      if (generation !== invoiceCaptureGeneration || urlAtSchedule !== currentUrl) return

      const elapsed = Date.now() - start
      let rendered = false
      try {
        // Compare digits only: the page displays the order number with
        // separators (e.g. "2000131-59543210") that the raw id lacks.
        const root = findInvoiceContentRoot()
        const wantedDigits = orderNumber ? String(orderNumber).replace(/\D+/g, '') : ''
        rendered = !!(
          wantedDigits &&
          root &&
          root.textContent &&
          root.textContent.replace(/\D+/g, '').indexOf(wantedDigits) >= 0
        )
      } catch {
        rendered = false
      }

      const ready =
        rendered ||
        (!orderNumber && elapsed >= INVOICE_CAPTURE_DELAY_MS) ||
        elapsed >= INVOICE_RENDER_WAIT_MAX_MS

      if (!ready) {
        invoiceCaptureTimer = setTimeout(attempt, INVOICE_RENDER_POLL_MS)
        return
      }

      captureInvoiceHtml()
        .then((html) => {
          if (!html) return
          if (generation !== invoiceCaptureGeneration || urlAtSchedule !== currentUrl) return
          postEvent('invoiceHtml', { html, capturedAt: new Date().toISOString() })
        })
        .catch(() => {
          // never break the page
        })
    }

    invoiceCaptureTimer = setTimeout(attempt, INVOICE_RENDER_POLL_MS)
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
          scheduleInvoiceCapture()
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
        } else if (data.purchaseHistory && data.purchaseHistory.pageInfo) {
          // PurchaseHistoryV3 shape
          payload.paginationInfo = { pageInfo: data.purchaseHistory.pageInfo }
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
      scheduleInvoiceCapture()
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

  function findNextPageButtons() {
    try {
      function isVisible(el) {
        try {
          if (!el) return false
          const r = el.getBoundingClientRect()
          if (!r || r.width <= 0 || r.height <= 0) return false
          // Do not require being within viewport; paginator is often below fold.
          const style = window.getComputedStyle ? window.getComputedStyle(el) : null
          if (style && (style.visibility === 'hidden' || style.display === 'none')) return false
          return true
        } catch {
          return true
        }
      }

      function isDisabled(el) {
        try {
          if (!el) return true
          if (el.disabled) return true
          const aria = el.getAttribute && el.getAttribute('aria-disabled')
          if (aria === 'true') return true
        } catch {
          // ignore
        }
        return false
      }

      // Prefer pagination-ish regions to avoid clicking unrelated "Next" buttons
      const scopes = []
      try {
        const navs = document.querySelectorAll(
          'nav[aria-label*="Pagination" i],nav[aria-label*="pagination" i],[data-testid*="pagination" i]'
        )
        for (let i = 0; i < navs.length; i++) scopes.push(navs[i])
      } catch {
        // ignore
      }
      scopes.push(document)

      const selectors = [
        'button[aria-label="Next" i]',
        'button[aria-label*="Next" i]',
        'a[aria-label="Next" i]',
        'a[aria-label*="Next" i]',
        '[data-automation-id*="next" i]',
      ]

      const found = []
      for (let s = 0; s < scopes.length; s++) {
        const scope = scopes[s]
        for (let i = 0; i < selectors.length; i++) {
          const list = scope.querySelectorAll ? scope.querySelectorAll(selectors[i]) : []
          for (let j = 0; j < list.length; j++) {
            const el = list[j]
            if (!el) continue
            if (!isVisible(el)) continue
            if (isDisabled(el)) continue
            found.push(el)
          }
        }
        if (found.length > 0) break
      }
      return found
    } catch {
      // ignore
    }
    return []
  }

  function clickNextPage(nextBtn) {
    if (!nextBtn) return false
    try {
      if (typeof nextBtn.scrollIntoView === 'function') {
        nextBtn.scrollIntoView({ block: 'center', inline: 'center' })
      }
    } catch {
      // ignore
    }

    try {
      if (typeof nextBtn.focus === 'function') nextBtn.focus()
    } catch {
      // ignore
    }

    // Some React components ignore .click() unless pointer/mouse events fire.
    try {
      const evOpts = { bubbles: true, cancelable: true, view: window }
      try {
        nextBtn.dispatchEvent(new PointerEvent('pointerdown', evOpts))
        nextBtn.dispatchEvent(new PointerEvent('pointerup', evOpts))
      } catch {
        // PointerEvent may not exist in older contexts
      }
      nextBtn.dispatchEvent(new MouseEvent('mousedown', evOpts))
      nextBtn.dispatchEvent(new MouseEvent('mouseup', evOpts))
      nextBtn.dispatchEvent(new MouseEvent('click', evOpts))
      return true
    } catch {
      // Fall back to plain click
      try {
        nextBtn.click()
        return true
      } catch {
        return false
      }
    }
  }

  function getCurrentPageNumberFromPagination() {
    try {
      const roots = document.querySelectorAll(
        'nav[aria-label*="Pagination" i],nav[aria-label*="pagination" i],[data-testid*="pagination" i]'
      )
      for (let r = 0; r < roots.length; r++) {
        const root = roots[r]
        if (!root) continue

        // Common a11y: aria-current="page" on the active page link/button
        const current = root.querySelector('[aria-current="page"]')
        if (current && current.textContent) {
          const n = parseInt(String(current.textContent).trim(), 10)
          if (Number.isFinite(n) && n > 0) return n
        }

        // Sometimes active page is marked via aria-selected
        const selected = root.querySelector('[aria-selected="true"]')
        if (selected && selected.textContent) {
          const n = parseInt(String(selected.textContent).trim(), 10)
          if (Number.isFinite(n) && n > 0) return n
        }
      }
    } catch {
      // ignore
    }
    return null
  }

  function waitForPageNumberChange(previousPageNumber, timeoutMs) {
    return new Promise((resolve) => {
      const start = Date.now()

      function check() {
        try {
          const cur = getCurrentPageNumberFromPagination()
          if (cur != null && cur !== previousPageNumber) {
            resolve(cur)
            return
          }
        } catch {
          // ignore
        }
        if (Date.now() - start >= timeoutMs) {
          resolve(null)
          return
        }
        setTimeout(check, 250)
      }

      check()
    })
  }

  function waitForPageAdvanceSignals({ previousPageNumber, previousRaw, previousFirstOrderNumber, timeoutMs }) {
    return new Promise((resolve) => {
      const start = Date.now()

      function check() {
        let pageNumChanged = false
        let payloadChanged = false
        let domChanged = false
        let currentPageNumber = null

        try {
          if (previousPageNumber != null) {
            currentPageNumber = getCurrentPageNumberFromPagination()
            if (currentPageNumber != null && currentPageNumber !== previousPageNumber) {
              pageNumChanged = true
            }
          }
        } catch {
          // ignore
        }

        try {
          if (previousRaw != null && ordersListCache && ordersListCache.raw && ordersListCache.raw !== previousRaw) {
            payloadChanged = true
          }
        } catch {
          // ignore
        }

        try {
          if (previousFirstOrderNumber) {
            const first = getFirstOrderNumberFromDom()
            if (first && first !== previousFirstOrderNumber) domChanged = true
          }
        } catch {
          // ignore
        }

        if (pageNumChanged || payloadChanged || domChanged) {
          resolve({ pageNumChanged, payloadChanged, domChanged, currentPageNumber })
          return
        }

        if (Date.now() - start >= timeoutMs) {
          resolve({ pageNumChanged: false, payloadChanged: false, domChanged: false, currentPageNumber })
          return
        }

        setTimeout(check, 250)
      }

      check()
    })
  }

  function extractOrdersFromDom() {
    const out = []
    try {
      const selector =
        '[data-automation-id^="view-order-details-link-"],' +
        '[id^="view-order-details-link-"],' +
        'a[href^="/orders/"],a[href*="/orders/"]'

      function parseOrderNumberFromHref(href) {
        try {
          if (!href || typeof href !== 'string') return null
          // Avoid URL() allocations; keep it simple.
          const raw = href.split('?')[0].split('#')[0]
          const idx = raw.indexOf('/orders/')
          if (idx < 0) return null
          const after = raw.slice(idx + '/orders/'.length)
          const seg = after.split('/')[0]
          return seg ? String(seg).trim() : null
        } catch {
          return null
        }
      }

      function parseOrderNumberFromEl(el) {
        if (!el) return null
        try {
          const getAttr = el.getAttribute ? el.getAttribute.bind(el) : null
          if (getAttr) {
            const dataId = getAttr('data-automation-id') || getAttr('id') || null
            if (dataId && typeof dataId === 'string') {
              const prefix = 'view-order-details-link-'
              const pos = dataId.indexOf(prefix)
              if (pos >= 0) {
                const candidate = dataId.slice(pos + prefix.length)
                const orderNumber = candidate ? String(candidate).trim() : null
                if (orderNumber) return orderNumber
              }
            }

            const href = getAttr('href')
            const fromHref = parseOrderNumberFromHref(href)
            if (fromHref) return fromHref
          }
        } catch {
          // ignore
        }
        return null
      }

      const els = document.querySelectorAll(selector)
      for (let i = 0; i < els.length; i++) {
        const el = els[i]
        const orderNumber = parseOrderNumberFromEl(el)
        if (!orderNumber) continue
        out.push({ orderNumber, detailButtonId: 'view-order-details-link-' + orderNumber })
      }
    } catch {
      // ignore
    }
    return out
  }

  function getFirstOrderNumberFromDom() {
    try {
      const selector =
        '[data-automation-id^="view-order-details-link-"],' +
        '[id^="view-order-details-link-"],' +
        'a[href^="/orders/"],a[href*="/orders/"]'
      const el = document.querySelector(selector)
      if (!el) return null

      const getAttr = el.getAttribute ? el.getAttribute.bind(el) : null
      if (!getAttr) return null

      const dataId = getAttr('data-automation-id') || getAttr('id') || null
      if (dataId && typeof dataId === 'string') {
        const prefix = 'view-order-details-link-'
        const pos = dataId.indexOf(prefix)
        if (pos >= 0) {
          const candidate = dataId.slice(pos + prefix.length)
          const orderNumber = candidate ? String(candidate).trim() : null
          if (orderNumber) return orderNumber
        }
      }

      const href = getAttr('href')
      if (href && typeof href === 'string') {
        const raw = href.split('?')[0].split('#')[0]
        const idx = raw.indexOf('/orders/')
        if (idx >= 0) {
          const after = raw.slice(idx + '/orders/'.length)
          const seg = after.split('/')[0]
          const orderNumber = seg ? String(seg).trim() : null
          if (orderNumber) return orderNumber
        }
      }
    } catch {
      // ignore
    }
    return null
  }

  function waitForOrdersDomChange(previousFirstOrderNumber, timeoutMs) {
    return new Promise((resolve) => {
      const start = Date.now()

      function check() {
        try {
          const first = getFirstOrderNumberFromDom()
          if (first && first !== previousFirstOrderNumber) {
            // Do the expensive full extraction once we have a change signal.
            const batch = extractOrdersFromDom()
            resolve(batch && batch.length > 0 ? batch : null)
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

  function waitForOrdersDomStabilize(timeoutMs) {
    return new Promise((resolve) => {
      const start = Date.now()
      let lastSig = null
      let lastChangedAt = Date.now()

      function signatureFromNodeList(nodeList) {
        try {
          if (!nodeList || nodeList.length === 0) return ''
          const first = getFirstOrderNumberFromDom() || ''
          let last = ''
          try {
            const lastEl = nodeList[nodeList.length - 1]
            if (lastEl && lastEl.getAttribute) {
              const dataId = lastEl.getAttribute('data-automation-id') || lastEl.getAttribute('id') || null
              if (dataId && typeof dataId === 'string') {
                const prefix = 'view-order-details-link-'
                const pos = dataId.indexOf(prefix)
                if (pos >= 0) last = String(dataId.slice(pos + prefix.length) || '')
              }
              if (!last) {
                const href = lastEl.getAttribute('href')
                if (href && typeof href === 'string') {
                  const raw = href.split('?')[0].split('#')[0]
                  const idx = raw.indexOf('/orders/')
                  if (idx >= 0) last = String((raw.slice(idx + '/orders/'.length).split('/')[0]) || '')
                }
              }
            }
          } catch {
            // ignore
          }
          return String(first) + '|' + String(last) + '|' + String(nodeList.length)
        } catch {
          return ''
        }
      }

      function check() {
        let nodeList = null
        try {
          const selector =
            '[data-automation-id^="view-order-details-link-"],' +
            '[id^="view-order-details-link-"],' +
            'a[href^="/orders/"],a[href*="/orders/"]'
          nodeList = document.querySelectorAll(selector)
        } catch {
          nodeList = null
        }
        const sig = signatureFromNodeList(nodeList)
        if (sig && sig !== lastSig) {
          lastSig = sig
          lastChangedAt = Date.now()
        }

        // Stable once unchanged for ~900ms
        if (sig && Date.now() - lastChangedAt >= 900) {
          const batch = extractOrdersFromDom()
          resolve(batch)
          return
        }

        if (Date.now() - start >= timeoutMs) {
          const batch = extractOrdersFromDom()
          resolve(batch && batch.length > 0 ? batch : null)
          return
        }

        setTimeout(check, 250)
      }

      check()
    })
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

  function await_sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  async function collectOrdersAcrossPages(maxPages) {
    const safeMaxPages = typeof maxPages === 'number' && maxPages > 0 ? Math.floor(maxPages) : 1
    const limit = Math.min(safeMaxPages, 50)
    const allOrders = []
    const seenOrderNumbers = new Set()
    let pagesCollected = 0

    function emitProgress(pageNumber, extractedCount, totalCount) {
      try {
        window.postMessage(
          {
            source: 'order-manager-walmart-extension',
            type: 'collectOrdersProgress',
            page: pageNumber,
            extracted: extractedCount,
            total: totalCount,
            pagesCollected,
            maxPages: limit,
          },
          '*'
        )
      } catch {
        // ignore
      }
    }

    function addUniqueOrders(batch) {
      let added = 0
      if (!Array.isArray(batch) || batch.length === 0) return added
      for (let i = 0; i < batch.length; i++) {
        const o = batch[i]
        if (!o || !o.orderNumber) continue
        if (seenOrderNumbers.has(o.orderNumber)) continue
        seenOrderNumbers.add(o.orderNumber)
        allOrders.push(o)
        added++
      }
      return added
    }

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
      const added = addUniqueOrders(firstBatch)
      pagesCollected++
      emitProgress(1, added, allOrders.length)
    } else {
      const firstBatch = extractOrdersFromDom()
      const added = addUniqueOrders(firstBatch)
      if (added > 0) {
        pagesCollected++
        emitProgress(1, added, allOrders.length)
      }
    }

    while (pagesCollected < limit) {
      const nextBtns = findNextPageButtons()
      if (!nextBtns || nextBtns.length === 0) break

      const previousRaw = ordersListCache ? ordersListCache.raw : null
      const prevPageNum = getCurrentPageNumberFromPagination()
      const prevDomBatch = extractOrdersFromDom()
      const prevDomFirst =
        prevDomBatch && prevDomBatch[0] && prevDomBatch[0].orderNumber
          ? String(prevDomBatch[0].orderNumber)
          : null

      // Page 3 (after collecting 2 pages) is often the slowest to hydrate.
      // Use a longer wait window there to avoid “double-next” skipping.
      const isPage3Transition = pagesCollected === 2
      const isAfterPage3 = pagesCollected >= 3
      const stepWaitMs = isPage3Transition ? 25000 : 8000
      const postClickDelayMs = isPage3Transition ? 1600 : 700
      const stabilizeWaitMs = isPage3Transition ? 18000 : 4500
      const maxAttempts = isPage3Transition ? 3 : 2

      let progressed = false
      for (let attempt = 0; attempt < maxAttempts && !progressed; attempt++) {
        for (let b = 0; b < nextBtns.length && !progressed; b++) {
          const clicked = clickNextPage(nextBtns[b])
          if (!clicked) continue

          // Give the UI time to navigate and hydrate (later pages can be slower)
          await await_sleep(postClickDelayMs)

          // Wait until *any* advance signal fires (page number, payload, or DOM),
          // so we don't burn the full timeout when page numbers aren't detectable.
          const advance = await waitForPageAdvanceSignals({
            previousPageNumber: prevPageNum,
            previousRaw,
            previousFirstOrderNumber: prevDomFirst,
            timeoutMs: stepWaitMs,
          })

          const hasAdvanceSignal = !!(
            advance && (advance.pageNumChanged || advance.payloadChanged || advance.domChanged)
          )
          // After page 3, keep these waits tighter so later pages don't feel laggy.
          // (We still rely primarily on DOM stabilization for correctness.)
          const payloadWaitMs = isPage3Transition ? 6000 : isAfterPage3 ? 2000 : 4000
          const domWaitMs = isPage3Transition ? 12000 : isAfterPage3 ? 3500 : 7000

          // Run the "next payload", "DOM changed", and "DOM stabilized" waits in parallel.
          // Previously these were sequential, which compounded into long per-page delays.
          const [nextPayload, domBatch, stabilized] = await Promise.all([
            waitForNextOrdersList(previousRaw, hasAdvanceSignal ? payloadWaitMs : 0),
            waitForOrdersDomChange(prevDomFirst, hasAdvanceSignal ? domWaitMs : 0),
            waitForOrdersDomStabilize(stabilizeWaitMs),
          ])

          // Then extract in order of reliability.
          let batch = null
          if (stabilized && stabilized.length > 0) {
            batch = stabilized
          } else if (domBatch && domBatch.length > 0) {
            batch = domBatch
          } else if (nextPayload) {
            const extracted = extractOrderNumbersFromListPayload(nextPayload)
            if (extracted && extracted.length > 0) batch = extracted
          } else {
            batch = []
          }

          // If we detected a transition but grabbed an intermediate/empty state,
          // wait for the DOM list to settle.
          const batchFirst =
            batch && batch[0] && batch[0].orderNumber ? String(batch[0].orderNumber) : null
          const batchLooksNew = !!(batchFirst && prevDomFirst && batchFirst !== prevDomFirst)

          const added = addUniqueOrders(batch)
          const payloadChanged = !!(nextPayload && nextPayload.raw && nextPayload.raw !== previousRaw)
          const domChanged =
            domBatch &&
            domBatch[0] &&
            domBatch[0].orderNumber &&
            prevDomFirst &&
            String(domBatch[0].orderNumber) !== String(prevDomFirst)
          // Critical: for the page-3 transition, do NOT advance unless we have
          // strong evidence we are actually on the next page AND have orders.
          // Otherwise we can incorrectly “progress” while still looking at page 2.
          const hasAnyOrders = Array.isArray(batch) && batch.length > 0
          const page3Ok = isPage3Transition
            ? hasAnyOrders && (batchLooksNew || domChanged || payloadChanged)
            : false
          const nonPage3Ok = !isPage3Transition && (added > 0 || domChanged || payloadChanged)
          if (added > 0 || page3Ok || nonPage3Ok) {
            pagesCollected++
            emitProgress(pagesCollected, added, allOrders.length)
            progressed = true
            break
          }

          await await_sleep(900)
        }
      }

      if (!progressed) {
        pagesCollected++
        emitProgress(pagesCollected, 0, allOrders.length)
        break
      }
    }

    return { orders: allOrders, pagesCollected }
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

