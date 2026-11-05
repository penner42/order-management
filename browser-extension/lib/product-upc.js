// Shared by extension workers, popups and automated browser imports.
;(function () {
  'use strict'

  function valid(value) {
    return typeof value === 'string' && /^(?:[0-9]{8}|[0-9]{12,14})$/.test(value.trim())
      ? value.trim() : null
  }

  function explicit(...records) {
    const values = new Set()
    for (const record of records) {
      for (const key of ['upc', 'UPC', 'upcCode', 'upcNumber', 'gtin8', 'gtin12', 'gtin13', 'gtin14']) {
        const value = valid(record?.[key])
        if (value) values.add(value)
      }
    }
    return values.size === 1 ? [...values][0] : null
  }

  function text(html) {
    return html.replace(/<[^>]*>/g, ' ').replace(/&(?:nbsp|#160);/g, ' ')
      .replace(/[\u200e\u200f\u202a-\u202e]/g, '').replace(/\s+/g, ' ').trim()
  }

  function extract(html, sku, store) {
    if (typeof html !== 'string') return null
    const values = new Set()
    // Walmart's Next data and schema.org products carry explicit product identity.
    function visit(node) {
      if (!node || typeof node !== 'object') return
      const matches = store === 'walmart'
        ? String(node.usItemId || (node['@type'] === 'Product' && node.sku) || '') === sku
        : node['@type'] === 'Product' && String(node.asin || node.sku || '') === sku
      if (matches) {
        for (const key of ['upc', 'UPC', 'upcCode', 'upcNumber', 'gtin8', 'gtin12', 'gtin13', 'gtin14']) {
          const value = valid(node[key])
          if (value) values.add(value)
        }
      }
      for (const child of Object.values(node)) {
        if (child && typeof child === 'object') visit(child)
      }
    }
    for (const script of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script\s*>/gi)) {
      try { visit(JSON.parse(script[1])) } catch { /* Non-JSON scripts are irrelevant. */ }
    }
    if (store === 'amazon') {
      // Read only product information tables/bullets, never recommendations or variants.
      // Newer expandable Item details use anonymous prodDetTable tables.
      const sections = [...html.matchAll(/<(table|ul)\b(?=[^>]*(?:\bid=["'](?:productDetails_[^"']*|detailBullets_feature_div)["']|\bclass=["'][^"']*\bprodDetTable\b[^"']*["']))[^>]*>([\s\S]*?)<\/\1\s*>/gi)]
      // detailBullets_feature_div is usually a div containing a ul.
      const bullets = /<div\b[^>]*\bid=["']detailBullets_feature_div["'][^>]*>([\s\S]*?)<\/ul\s*>/i.exec(html)
      if (bullets) sections.push([null, null, bullets[1]])
      const rows = sections.flatMap(section => [...section[2].matchAll(/<(tr|li)\b[^>]*>([\s\S]*?)<\/\1\s*>/gi)].map(row => text(row[2])))
      const asins = rows.map(row => /^ASIN\s*:?\s*([A-Z0-9]{10})\b/i.exec(row)?.[1]).filter(Boolean)
      const hiddenAsin = /<input\b(?=[^>]*\bid=["']ASIN["'])(?=[^>]*\bvalue=["']([A-Z0-9]{10})["'])[^>]*>/i.exec(html)?.[1]
      if (hiddenAsin) asins.push(hiddenAsin)
      if (asins.some(asin => asin !== sku)) return null
      if (asins.length && asins.every(asin => asin === sku)) {
        for (const row of rows) {
          const match = /^UPC\s*:?\s*([0-9][0-9\s]*)$/i.exec(row)
          const value = match && valid(match[1].replace(/\s/g, ''))
          if (value) values.add(value)
        }
      }
    }
    return values.size === 1 ? [...values][0] : null
  }

  async function enrich(orders, store, loadHtml) {
    const products = new Map()
    for (const order of orders) {
      for (const item of order.items || []) {
        const sku = String(item.externalSku || '')
        if (item.upc || !(store === 'amazon' ? /^[A-Z0-9]{10}$/ : /^[0-9]{1,20}$/).test(sku)) continue
        if (!products.has(sku)) products.set(sku, [])
        products.get(sku).push(item)
      }
    }
    const pending = [...products]
    async function worker() {
      while (pending.length) {
        const [sku, items] = pending.shift()
        try {
          let html
          if (loadHtml) html = await loadHtml(sku)
          else {
            const origin = store === 'amazon' ? 'https://www.amazon.com' : 'https://www.walmart.com'
            const controller = new AbortController()
            const timer = setTimeout(() => controller.abort(), 12000)
            try {
              const response = await fetch(origin + (store === 'amazon' ? '/dp/' : '/ip/') + sku,
                { credentials: 'include', signal: controller.signal })
              if (!response.ok || new URL(response.url).origin !== origin) continue
              html = await response.text()
            } finally { clearTimeout(timer) }
          }
          const upc = extract(html, sku, store)
          if (upc) for (const item of items) item.upc = upc
        } catch { /* Missing, blocked and discontinued products must not stop imports. */ }
      }
    }
    await Promise.all([worker(), worker()])
    return orders
  }

  globalThis.OrderManagerProductUpc = { valid, explicit, extract, enrich }
})()
