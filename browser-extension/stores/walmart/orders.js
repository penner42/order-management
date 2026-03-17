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
            const batch = extractOrdersFromDom()
            const first =
              batch && batch[0] && batch[0].orderNumber ? String(batch[0].orderNumber) : null
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
      const els = document.querySelectorAll(selector)
      for (let i = 0; i < els.length; i++) {
        const el = els[i]
        const dataId =
          (el && el.getAttribute && el.getAttribute('data-automation-id')) ||
          (el && el.getAttribute && el.getAttribute('id')) ||
          null
        let candidate = null
        if (dataId && typeof dataId === 'string' && dataId.includes('view-order-details-link-')) {
          candidate = dataId.split('view-order-details-link-')[1] || null
        }
        if (!candidate) {
          const href = el && el.getAttribute ? el.getAttribute('href') : null
          if (href && typeof href === 'string') {
            try {
              const u = new URL(href, window.location.origin)
              const parts = (u.pathname || '').split('/').filter(Boolean)
              const idx = parts.indexOf('orders')
              if (idx >= 0 && parts[idx + 1]) candidate = parts[idx + 1]
            } catch {
              // ignore
            }
          }
        }
        if (!candidate) continue
        const orderNumber = String(candidate).trim()
        if (!orderNumber) continue
        out.push({
          orderNumber,
          detailButtonId: 'view-order-details-link-' + orderNumber,
        })
      }
    } catch {
      // ignore
    }
    return out
  }

  function waitForOrdersDomChange(previousFirstOrderNumber, timeoutMs) {
    return new Promise((resolve) => {
      const start = Date.now()

      function check() {
        try {
          const batch = extractOrdersFromDom()
          const first =
            batch && batch[0] && batch[0].orderNumber ? String(batch[0].orderNumber) : null
          if (batch && batch.length > 0 && first && first !== previousFirstOrderNumber) {
            resolve(batch)
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

      function signature(batch) {
        try {
          if (!Array.isArray(batch) || batch.length === 0) return ''
          const first = batch[0] && batch[0].orderNumber ? String(batch[0].orderNumber) : ''
          const last =
            batch[batch.length - 1] && batch[batch.length - 1].orderNumber
              ? String(batch[batch.length - 1].orderNumber)
              : ''
          return first + '|' + last + '|' + String(batch.length)
        } catch {
          return ''
        }
      }

      function check() {
        let batch = []
        try {
          batch = extractOrdersFromDom()
        } catch {
          batch = []
        }
        const sig = signature(batch)
        if (sig && sig !== lastSig) {
          lastSig = sig
          lastChangedAt = Date.now()
        }

        // Stable once unchanged for ~900ms
        if (sig && Date.now() - lastChangedAt >= 900) {
          resolve(batch)
          return
        }

        if (Date.now() - start >= timeoutMs) {
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

          const nextPayload = await waitForNextOrdersList(
            previousRaw,
            advance && (advance.pageNumChanged || advance.payloadChanged || advance.domChanged) ? 6000 : 0
          )
          const domBatch = await waitForOrdersDomChange(
            prevDomFirst,
            advance && (advance.pageNumChanged || advance.payloadChanged || advance.payloadChanged) ? 12000 : 0
          )

          // Then wait for the DOM list to settle and extract.
          let batch = null
          const stabilized = await waitForOrdersDomStabilize(stabilizeWaitMs)
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

