;(function () {
  'use strict'

  const AMAZON_ORDER_DETAIL_STORAGE_KEY = 'amazonOrderDetail'
  const AMAZON_ACCOUNT_EMAIL_STORAGE_KEY = 'amazonAccountEmail'

  if (typeof chrome === 'undefined' || !chrome.storage || !chrome.storage.local) {
    return
  }

  if (globalThis.__OrderManagerAmazonCaptureInitialized) return
  globalThis.__OrderManagerAmazonCaptureInitialized = true

  function dom() {
    return typeof globalThis !== 'undefined' && globalThis.OrderManagerAmazonDom
      ? globalThis.OrderManagerAmazonDom
      : null
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms))
  }

  async function ensureAccountEmailCached() {
    const existing = await new Promise((resolve) => {
      try {
        chrome.storage.local.get(AMAZON_ACCOUNT_EMAIL_STORAGE_KEY, (data) => {
          resolve(data && data[AMAZON_ACCOUNT_EMAIL_STORAGE_KEY] ? data[AMAZON_ACCOUNT_EMAIL_STORAGE_KEY] : null)
        })
      } catch {
        resolve(null)
      }
    })
    if (existing && existing.email) return existing.email

    const d = dom()
    if (!d || typeof d.fetchAccountEmail !== 'function') return null
    const email = await d.fetchAccountEmail(window.location.origin)
    if (email) {
      await new Promise((resolve) => {
        chrome.storage.local.set(
          {
            [AMAZON_ACCOUNT_EMAIL_STORAGE_KEY]: {
              email,
              capturedAt: new Date().toISOString(),
              origin: window.location.origin,
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
        () => resolve(null)
      )
    })
  }

  async function captureCurrentDetailPage(options) {
    const opts = options && typeof options === 'object' ? options : {}
    const skipTracking = !!opts.skipTrackingEnrichment
    const d = dom()
    if (!d) throw new Error('Amazon DOM helpers not loaded.')

    await d.waitForOrderDetailReady(35000)
    await sleep(500)
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

  async function captureCurrentListPage() {
    const d = dom()
    if (!d) throw new Error('Amazon DOM helpers not loaded.')
    await d.waitForOrderListReady()
    await sleep(500)
    return d.parseOrderListPage()
  }

  if (chrome.runtime && chrome.runtime.onMessage) {
    chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
      if (!message || message.store !== 'amazon') return undefined

      if (message.type === 'amazonParseCurrentListPage') {
        ;(async () => {
          try {
            await ensureAccountEmailCached()
            const orders = await captureCurrentListPage()
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
            await ensureAccountEmailCached()
            const parsed = await captureCurrentDetailPage({
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

      if (message.type === 'amazonFetchAccountEmail') {
        ;(async () => {
          try {
            const email = await ensureAccountEmailCached()
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

  scheduleAutoCaptureDetail()
})()
