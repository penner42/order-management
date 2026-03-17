;(function () {
  'use strict'

  // Prefer the production-scoped key; keep legacy as fallback.
  const STORAGE_KEY_PROD = 'orderManagerExtensionTokenProd'
  const STORAGE_KEY_LEGACY = 'orderManagerExtensionToken'

  if (typeof chrome === 'undefined' || !chrome.storage || !chrome.storage.local) {
    return
  }

  window.addEventListener('message', (event) => {
    try {
      const data = event.data
      if (!data || data.source !== 'order-manager-extension' || data.type !== 'extensionAuthToken') return
      const token = data.token
      if (!token || typeof token !== 'string') return
      const trimmed = token.trim()
      if (!trimmed) return

      chrome.storage.local.set({ [STORAGE_KEY_PROD]: trimmed, [STORAGE_KEY_LEGACY]: trimmed }, () => {
        // Best-effort; ignore errors
      })
    } catch {
      // never break the page
    }
  })
})()
