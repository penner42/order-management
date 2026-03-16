;(function () {
  'use strict'

  const STORAGE_KEY = 'walmartOrderDetail'
  const EXTENSION_SOURCE = 'order-manager-walmart-extension'

  if (typeof chrome === 'undefined' || !chrome.storage || !chrome.storage.local) {
    return
  }

  // Listen for page-context Walmart messages and persist order detail payloads
  window.addEventListener('message', (event) => {
    try {
      const data = event.data
      if (!data || data.source !== 'order-manager-walmart' || data.type !== 'orderDetail') return
      const payload = data.payload
      const url = data.url || (typeof document !== 'undefined' ? document.location.href : null)
      if (!payload || !url) return

      chrome.storage.local.set({
        [STORAGE_KEY]: { url, payload },
      })
    } catch {
      // never break the page
    }
  })

  // Bridge runtime messages from the extension to the page script for bulk operations.
  // This lets the popup/background ask the Walmart page script to collect order numbers
  // or open specific order detail pages by “clicking” the appropriate buttons.
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
})()
