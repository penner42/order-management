;(function () {
  'use strict'

  const STORAGE_KEY = 'bgBearerToken'

  if (typeof chrome === 'undefined' || !chrome.storage || !chrome.storage.local) {
    return
  }

  window.addEventListener('message', (event) => {
    try {
      if (event.source !== window) return
      const data = event.data
      if (!data || data.source !== 'order-manager-bg' || data.type !== 'bgToken') return
      const token = data.token
      if (!token || typeof token !== 'string') return

      chrome.storage.local.get(STORAGE_KEY, (existingData) => {
        if (chrome.runtime && chrome.runtime.lastError) {
          // Best-effort write even if get failed
          try {
            chrome.storage.local.set({ [STORAGE_KEY]: token })
          } catch {
            // ignore
          }
          return
        }

        const existing = existingData ? existingData[STORAGE_KEY] : null
        if (existing === token) return

        try {
          chrome.storage.local.set({ [STORAGE_KEY]: token })
        } catch {
          // ignore
        }
      })
    } catch {
      // never break the page
    }
  })
})()
