;(function () {
  'use strict'

  const STORAGE_KEY = 'walmartOrderDetail'
  const INVOICE_STORAGE_KEY = 'walmartInvoiceHtml'
  const EXTENSION_SOURCE = 'order-manager-walmart-extension'

  if (typeof chrome === 'undefined' || !chrome.storage || !chrome.storage.local) {
    return
  }

  // Listen for page-context Walmart messages and persist order detail payloads
  // and captured invoice HTML.
  window.addEventListener('message', (event) => {
    try {
      const data = event.data
      if (!data || data.source !== 'order-manager-walmart') return
      const url = data.url || (typeof document !== 'undefined' ? document.location.href : null)
      if (!url) return

      if (data.type === 'orderDetail') {
        const payload = data.payload
        if (!payload) return
        chrome.storage.local.set({
          [STORAGE_KEY]: { url, payload },
        })
        return
      }

      if (data.type === 'invoiceHtml') {
        const html = data.payload && data.payload.html
        if (!html || typeof html !== 'string') return
        chrome.storage.local.set({
          [INVOICE_STORAGE_KEY]: {
            url,
            html,
            capturedAt: (data.payload && data.payload.capturedAt) || new Date().toISOString(),
          },
        })
      }
    } catch {
      // never break the page
    }
  })

  // Bridge runtime messages from the extension to the page script for bulk operations.
  // This lets the popup/background ask the Walmart page script to collect order numbers
  // or open specific order detail pages by “clicking” the appropriate buttons.
  //
  // Also forwards incremental progress updates from the page script back to the background
  // so the extension bulk progress tab can render per-page updates.
  if (chrome.runtime && chrome.runtime.onMessage) {
    chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
      try {
        if (!message || message.store !== 'walmart') {
          return
        }

        // Collect orders across up to N pages
        if (message.type === 'walmartCollectOrdersAcrossPages') {
          let finished = false

          const handleResult = (event) => {
            try {
              const data = event.data
              if (
                !data ||
                data.source !== EXTENSION_SOURCE ||
                data.type !== 'collectOrdersResult'
              ) {
                return
              }
              window.removeEventListener('message', handleResult)
              if (!finished) {
                finished = true
                sendResponse({
                  success: true,
                  orders: Array.isArray(data.orders) ? data.orders : [],
                  pagesCollected: data.pagesCollected || 0,
                  error: data.error || null,
                })
              }
            } catch {
              // ignore
            }
          }

          window.addEventListener('message', handleResult)

          window.postMessage(
            {
              source: EXTENSION_SOURCE,
              type: 'collectOrdersAcrossPages',
              maxPages: message.maxPages,
            },
            '*'
          )

          return true
        }

        // Ask the page script to open a specific order detail by order number.
        if (message.type === 'walmartOpenOrderDetail') {
          window.postMessage(
            {
              source: EXTENSION_SOURCE,
              type: 'openOrderDetail',
              orderNumber: message.orderNumber,
            },
            '*'
          )
          sendResponse({ success: true })
          return
        }
      } catch {
        // never break the page
      }
    })
  }

  // Forward progress messages (page-by-page collection) to background.
  window.addEventListener('message', (event) => {
    try {
      const data = event.data
      if (!data || data.source !== EXTENSION_SOURCE || data.type !== 'collectOrdersProgress') return
      if (!chrome.runtime || typeof chrome.runtime.sendMessage !== 'function') return

      chrome.runtime.sendMessage({
        store: 'walmart',
        type: 'walmartCollectOrdersProgress',
        page: data.page,
        extracted: data.extracted,
        total: data.total,
        pagesCollected: data.pagesCollected,
        maxPages: data.maxPages,
      })
    } catch {
      // ignore
    }
  })
})()
