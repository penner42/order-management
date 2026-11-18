;(function () {
  'use strict'

  async function captureCostcoInvoice(orderNumber) {
    if (!orderNumber || !/^#\/app\/[^/]+\/orderdetails(?:[/?]|$)/i.test(location.hash)) return null
    const orderUrl = location.href
    const findInvoiceUrl = () => {
      for (const link of document.querySelectorAll('a[href*="OrderDetailPrintView" i]')) {
        const target = new URL(link.getAttribute('href'), document.baseURI)
        if (target.origin === location.origin &&
            /\/OrderDetailPrintView$/i.test(target.pathname) &&
            target.searchParams.get('orderId') === orderNumber) {
          return target
        }
      }
      return null
    }
    let url = findInvoiceUrl()
    if (!url) {
      // The GraphQL response can arrive before the link is rendered.
      url = await new Promise(resolve => {
        const observer = new MutationObserver(() => {
          const match = location.href === orderUrl ? findInvoiceUrl() : null
          if (match || location.href !== orderUrl) finish(match)
        })
        const timer = setTimeout(() => finish(null), 2000)
        function finish(value) {
          clearTimeout(timer)
          observer.disconnect()
          resolve(value)
        }
        observer.observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ['href'] })
      })
    }
    if (!url || location.href !== orderUrl) return null
    const invoice = await globalThis.OrderManagerInvoiceDocument.fetchInvoiceDocument(
      url,
      orderNumber,
      finalUrl => /\/OrderDetailPrintView$/i.test(finalUrl.pathname) && finalUrl.searchParams.get('orderId') === orderNumber,
      doc => {
        // Costco hides the body until its startup scripts reveal it. The PDF uses a static document.
        doc.body.style.setProperty('opacity', '1', 'important')
        doc.body.style.setProperty('filter', 'none', 'important')
        // Costco's startup scripts apply the preloaded CSS. Apply it directly in the static copy.
        for (const link of doc.querySelectorAll('link[rel~="preload"][as="style"][href]')) {
          link.rel = 'stylesheet'
          link.removeAttribute('as')
        }
      },
    )
    return location.href === orderUrl ? invoice : null
  }

  globalThis.OrderManagerCostcoInvoice = { captureCostcoInvoice }
})()
