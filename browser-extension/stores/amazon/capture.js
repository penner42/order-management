;(function () {
  'use strict'

  const AMAZON_ORDER_DETAIL_STORAGE_KEY = 'amazonOrderDetail'
  const AMAZON_ACCOUNT_EMAIL_STORAGE_KEY = 'amazonAccountEmail'

  if (typeof chrome === 'undefined' || !chrome.storage || !chrome.storage.local) {
    return
  }

  function dom() {
    return typeof globalThis !== 'undefined' && globalThis.OrderManagerAmazonDom
      ? globalThis.OrderManagerAmazonDom
      : null
  }

  function notifyBackground(type, extra) {
    try {
      if (!chrome.runtime || typeof chrome.runtime.sendMessage !== 'function') return
      chrome.runtime.sendMessage({ store: 'amazon', type, ...(extra || {}) })
    } catch {
      // ignore
    }
  }

  async function ensureAccountEmailCached(options) {
    const opts = options && typeof options === 'object' ? options : {}
    const forceRefresh = !!opts.forceRefresh
    const origin = window.location.origin
    const cookieStoreId = opts.cookieStoreId ? String(opts.cookieStoreId) : 'default'
    let cachedRow = null

    if (!forceRefresh) {
      cachedRow = await new Promise((resolve) => {
        try {
          chrome.storage.local.get(AMAZON_ACCOUNT_EMAIL_STORAGE_KEY, (data) => {
            resolve(data && data[AMAZON_ACCOUNT_EMAIL_STORAGE_KEY] ? data[AMAZON_ACCOUNT_EMAIL_STORAGE_KEY] : null)
          })
        } catch {
          resolve(null)
        }
      })
      const cachedContainer = cachedRow && cachedRow.cookieStoreId ? String(cachedRow.cookieStoreId) : 'default'
      if (cachedRow && cachedRow.email && cachedRow.origin === origin && cachedContainer === cookieStoreId) {
        return cachedRow.email
      }
    }

    const d = dom()
    if (!d || typeof d.fetchAccountEmail !== 'function') return null
    const email = await d.fetchAccountEmail(origin, {
      allowSlowLookup: !!opts.allowSlowLookup,
    })
    if (email) {
      await new Promise((resolve) => {
        chrome.storage.local.set(
          {
            [AMAZON_ACCOUNT_EMAIL_STORAGE_KEY]: {
              email,
              capturedAt: new Date().toISOString(),
              origin,
              cookieStoreId,
            },
          },
          () => resolve(null)
        )
      })
    }
    return email
  }

  function persistOrderDetail(raw, url) {
    return new Promise((resolve) => {
      chrome.storage.local.set(
        {
          [AMAZON_ORDER_DETAIL_STORAGE_KEY]: {
            url: url || window.location.href,
            capturedAt: new Date().toISOString(),
            payload: raw,
          },
        },
        () => {
          notifyBackground('amazonDetailCaptured', {
            orderId: raw && raw.orderId != null ? String(raw.orderId) : null,
            url: url || window.location.href,
            payload: raw,
          })
          resolve(null)
        }
      )
    })
  }

  async function captureCurrentDetailPage(options) {
    const opts = options && typeof options === 'object' ? options : {}
    const skipTracking = !!opts.skipTrackingEnrichment
    const pageAlreadyReady = !!opts.pageAlreadyReady
    const d = dom()
    if (!d) throw new Error('Amazon DOM helpers not loaded.')

    if (!pageAlreadyReady) {
      await d.waitForOrderDetailReady(35000)
    }
    const parsed = d.parseOrderDetailPage()

    if (!parsed || !parsed.orderId) {
      throw new Error('Could not parse Amazon order detail page.')
    }
    if (!skipTracking && typeof d.enrichShipmentsWithTracking === 'function') {
      await d.enrichShipmentsWithTracking(parsed.shipments, window.location.origin)
    }
    await persistOrderDetail(parsed, window.location.href)
    return parsed
  }

  function scheduleAutoCaptureDetail() {
    const d = dom()
    if (!d || !d.isAmazonOrderDetailPage()) return

    function run() {
      setTimeout(() => {
        captureCurrentDetailPage().catch(() => {
          // best-effort; popup/background also request capture explicitly
        })
      }, 1500)
    }

    if (document.readyState === 'complete' || document.readyState === 'interactive') {
      run()
    } else {
      window.addEventListener('DOMContentLoaded', run, { once: true })
      window.addEventListener('load', run, { once: true })
    }
  }

  async function captureCurrentListPage(options) {
    const opts = options && typeof options === 'object' ? options : {}
    const pageAlreadyReady = !!opts.pageAlreadyReady
    const d = dom()
    if (!d) throw new Error('Amazon DOM helpers not loaded.')
    if (!pageAlreadyReady) {
      await d.waitForOrderListReady()
    }
    // The "ready" signal can fire on an empty shell before the SPA injects the
    // order list, so always wait until parseable order content exists (or a
    // timeout proves the page is genuinely empty) before parsing.
    if (typeof d.waitForParseableOrderList === 'function') {
      try {
        await d.waitForParseableOrderList(25000)
      } catch {
        // Timed out: page may genuinely have no orders; parse anyway.
      }
    }
    return d.parseOrderListPage()
  }

  function withDisableCsdUrl(url) {
    const am = typeof globalThis !== 'undefined' ? globalThis.OrderManagerAmazon : null
    if (am && typeof am.withDisableCsdParam === 'function') return am.withDisableCsdParam(url)
    try {
      const u = new URL(String(url), window.location.origin)
      if (!u.searchParams.has('disableCsd')) {
        u.searchParams.set('disableCsd', 'missing-library')
      }
      return u.toString()
    } catch {
      return url
    }
  }

  async function captureOrderDetailFromUrl(detailUrl, options) {
    const opts = options && typeof options === 'object' ? options : {}
    const skipTracking = !!opts.skipTrackingEnrichment
    const d = dom()
    if (!d) throw new Error('Amazon DOM helpers not loaded.')
    if (!detailUrl) throw new Error('Missing Amazon order detail URL.')

    const url = withDisableCsdUrl(detailUrl)

    return await new Promise((resolve, reject) => {
      const iframe = document.createElement('iframe')
      iframe.style.cssText =
        'position:fixed;width:1px;height:1px;opacity:0;pointer-events:none;left:-9999px;top:0;border:0;'
      iframe.setAttribute('aria-hidden', 'true')

      let settled = false
      const cleanup = () => {
        try {
          iframe.remove()
        } catch {
          // ignore
        }
      }
      const finish = (fn) => {
        if (settled) return
        settled = true
        cleanup()
        fn()
      }

      const timeoutTimer = setTimeout(() => {
        finish(() => reject(new Error('Timed out loading Amazon order detail page.')))
      }, 45000)

      iframe.addEventListener('load', () => {
        ;(async () => {
          try {
            const frameDoc = iframe.contentDocument
            const frameWin = iframe.contentWindow
            if (!frameDoc) throw new Error('Could not access order detail frame.')
            const frameUrl =
              frameWin && frameWin.location && frameWin.location.href
                ? frameWin.location.href
                : url

            if (typeof d.waitForOrderDetailReadyInDocument === 'function') {
              await d.waitForOrderDetailReadyInDocument(frameDoc, frameUrl, 35000)
            }

            const parsed = d.parseOrderDetailPage(frameDoc, frameUrl)
            if (!parsed || !parsed.orderId) {
              throw new Error('Could not parse Amazon order detail page.')
            }

            if (!skipTracking && typeof d.enrichShipmentsWithTracking === 'function') {
              await d.enrichShipmentsWithTracking(parsed.shipments, window.location.origin)
            }

            await persistOrderDetail(parsed, frameUrl)
            clearTimeout(timeoutTimer)
            finish(() => resolve(parsed))
          } catch (e) {
            clearTimeout(timeoutTimer)
            finish(() => reject(e))
          }
        })()
      })

      iframe.addEventListener('error', () => {
        clearTimeout(timeoutTimer)
        finish(() => reject(new Error('Failed to load Amazon order detail page.')))
      })

      document.documentElement.appendChild(iframe)
      iframe.src = url
    })
  }

  function schedulePageReadyNotification() {
    const d = dom()
    if (!d) return

    ;(async () => {
      try {
        if (typeof d.isAmazonOrderListPage === 'function' && d.isAmazonOrderListPage()) {
          await d.waitForParseableOrderList(25000)
          notifyBackground('amazonPageReady', { pageKind: 'list', url: window.location.href })
        } else if (typeof d.isAmazonOrderDetailPage === 'function' && d.isAmazonOrderDetailPage()) {
          await d.waitForOrderDetailReady(35000)
          notifyBackground('amazonPageReady', { pageKind: 'detail', url: window.location.href })
        }
      } catch {
        // ignore
      }
    })()
  }

  function registerMessageListener() {
    if (globalThis.__OrderManagerAmazonCaptureListener) return
    globalThis.__OrderManagerAmazonCaptureListener = true

    if (!chrome.runtime || !chrome.runtime.onMessage) return

    chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
      if (!message || message.store !== 'amazon') return undefined

      if (message.type === 'amazonParseCurrentListPage') {
        ;(async () => {
          try {
            ensureAccountEmailCached().catch(() => {})
            const orders = await captureCurrentListPage({
              pageAlreadyReady: !!message.pageAlreadyReady,
            })
            sendResponse({ success: true, orders })
          } catch (e) {
            sendResponse({
              success: false,
              error: String(e && e.message ? e.message : e),
            })
          }
        })()
        return true
      }

      if (message.type === 'amazonParseCurrentDetailPage') {
        ;(async () => {
          try {
            const parsed = await captureCurrentDetailPage({
              skipTrackingEnrichment: !!message.skipTrackingEnrichment,
              pageAlreadyReady: !!message.pageAlreadyReady,
            })
            sendResponse({ success: true, order: parsed })
          } catch (e) {
            sendResponse({
              success: false,
              error: String(e && e.message ? e.message : e),
            })
          }
        })()
        return true
      }

      if (message.type === 'amazonCaptureOrderDetailFromUrl') {
        ;(async () => {
          try {
            ensureAccountEmailCached({ allowSlowLookup: false }).catch(() => {})
            const parsed = await captureOrderDetailFromUrl(message.detailUrl, {
              skipTrackingEnrichment: !!message.skipTrackingEnrichment,
            })
            sendResponse({ success: true, order: parsed })
          } catch (e) {
            sendResponse({
              success: false,
              error: String(e && e.message ? e.message : e),
            })
          }
        })()
        return true
      }

      if (message.type === 'amazonGetNextPageUrl') {
        try {
          const d = dom()
          if (!d) {
            sendResponse({ success: false, error: 'Amazon DOM helpers not loaded.' })
            return true
          }
          const nextUrl = d.getNextPageUrl()
          sendResponse({ success: true, url: nextUrl || null, hasNext: !!nextUrl })
        } catch (e) {
          sendResponse({
            success: false,
            error: String(e && e.message ? e.message : e),
          })
        }
        return true
      }

      if (message.type === 'amazonGetSpaListPageInfo') {
        try {
          const d = dom()
          if (!d || typeof d.getSpaListPageInfo !== 'function') {
            sendResponse({ success: false, error: 'Amazon DOM helpers not loaded.' })
            return true
          }
          sendResponse({ success: true, info: d.getSpaListPageInfo() })
        } catch (e) {
          sendResponse({
            success: false,
            error: String(e && e.message ? e.message : e),
          })
        }
        return true
      }

      if (message.type === 'amazonNavigateSpaListPage') {
        try {
          const target = message.url ? String(message.url) : ''
          if (!target) {
            sendResponse({ success: false, error: 'Missing SPA list page URL.' })
            return true
          }
          const next = new URL(target, window.location.href)
          const cur = new URL(window.location.href)
          if (
            next.origin === cur.origin &&
            next.pathname === cur.pathname &&
            next.search === cur.search &&
            next.hash &&
            next.hash !== cur.hash
          ) {
            window.location.hash = next.hash
          } else {
            window.location.assign(next.toString())
          }
          sendResponse({ success: true })
        } catch (e) {
          sendResponse({
            success: false,
            error: String(e && e.message ? e.message : e),
          })
        }
        return true
      }

      if (message.type === 'amazonFetchAccountEmail') {
        ;(async () => {
          try {
            const email = await ensureAccountEmailCached({
              forceRefresh: !!message.forceRefresh,
              cookieStoreId: message.cookieStoreId,
              allowSlowLookup: !!message.allowSlowLookup,
            })
            sendResponse({ success: true, email: email || null })
          } catch (e) {
            sendResponse({
              success: false,
              error: String(e && e.message ? e.message : e),
            })
          }
        })()
        return true
      }

      if (message.type === 'amazonPing') {
        sendResponse({ success: true, ready: true })
        return true
      }

      return undefined
    })
  }

  registerMessageListener()

  const pageKey = window.location.href
  if (globalThis.__OrderManagerAmazonCapturePageKey !== pageKey) {
    globalThis.__OrderManagerAmazonCapturePageKey = pageKey
    notifyBackground('amazonContentScriptReady', { url: pageKey })
    schedulePageReadyNotification()
    scheduleAutoCaptureDetail()
  }
})()
