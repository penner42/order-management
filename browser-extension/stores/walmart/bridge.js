;(function () {
  'use strict'

  const STORAGE_KEY = 'walmartOrderDetail'

  if (typeof chrome === 'undefined' || !chrome.storage || !chrome.storage.local) {
    return
  }

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
})()
