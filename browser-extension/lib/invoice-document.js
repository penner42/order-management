;(function () {
  'use strict'

  async function fetchInvoiceDocument(url, orderId, acceptResponseUrl, prepareDocument) {
    try {
      const response = await fetch(url, { mode: 'same-origin', signal: AbortSignal.timeout(8000) })
      if (!response.ok || !acceptResponseUrl(new URL(response.url))) return null
      const blob = await response.blob()
      if (blob.size > 5 * 1024 * 1024) return null
      if (await blob.slice(0, 5).text() === '%PDF-') {
        const base64 = await new Promise((resolve, reject) => {
          const reader = new FileReader()
          reader.onload = () => resolve(reader.result.split(',')[1])
          reader.onerror = reject
          reader.readAsDataURL(blob)
        })
        return { invoicePdfBase64: base64 }
      }
      if (!blob.type.startsWith('text/html')) return null

      const invoice = new DOMParser().parseFromString(await blob.text(), 'text/html')
      invoice.querySelectorAll('script, iframe, object, embed, base, meta[http-equiv]').forEach(el => el.remove())
      if (!invoice.body.textContent.includes(orderId)) return null
      prepareDocument?.(invoice)
      const base = invoice.createElement('base')
      base.href = response.url
      invoice.head.prepend(base)
      return { invoiceHtml: '<!doctype html>' + invoice.documentElement.outerHTML }
    } catch {
      return null
    }
  }

  globalThis.OrderManagerInvoiceDocument = { fetchInvoiceDocument }
})()
