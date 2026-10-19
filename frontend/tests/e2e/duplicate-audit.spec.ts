import { test, expect } from './fixtures'
import type { Order } from '../../src/api/types'

for (const width of [1280, 390]) {
  test(`Duplicate audit shows full orders and evidence without editing at width ${width}`, async ({ app, data }, testInfo) => {
    await app.setViewportSize({ width, height: 900 })
    const store = await data.create('/stores', { name: data.name('store') })
    const order = await data.create<Order>('/orders', {
      store_id: store.id, store_order_number: data.name('duplicate'),
      items: [
        { description: 'HP 14 inch Windows Laptop Intel N150', status: 'scanned', quantity: 2, price_paid: 199, price_sold: 199, receipt_id: 'AUDIT-RECEIPT' },
        { description: 'HP 14-inch Windows 11 Laptop Intel N150 4GB RAM', status: 'submitted', quantity: 2, price_paid: 199, price_sold: 0 },
        { description: 'Separate accessory', status: 'purchased', quantity: 1, price_paid: 10, price_sold: 15 },
      ],
    })
    await data.create('/shipments', {
      tracking_number: data.name('tracking'), item_ids: order.items.slice(0, 2).map(item => item.id),
    })
    const clean = await data.create<Order>('/orders', {
      store_id: store.id, store_order_number: data.name('clean'),
      items: [{ description: 'Single item', quantity: 1, price_paid: 10, price_sold: 20 }],
    })
    const before = await data.get<Order>(`/orders/${order.id}`)
    await app.goto(`/duplicate-audit?q=${encodeURIComponent(data.prefix)}`)
    await expect(app.getByRole('heading', { name: 'Duplicate audit', exact: true })).toBeVisible()
    await expect(app.getByRole('status')).toHaveText('1 flagged order')
    await expect(app.getByText('Likely duplicate', { exact: true })).toBeVisible()
    await expect(app.getByText(/Scanned or receipt-linked line alongside a zero-payout line/)).toBeVisible()
    const region = app.getByRole('region', { name: `Order ${order.id}`, exact: true })
    await expect(region).toBeVisible()
    await expect(region.getByLabel('Order number')).toHaveValue(order.store_order_number!)
    await expect(region.getByLabel('Order number')).toBeDisabled()
    await expect(region.getByRole('button', { name: 'Delete order', exact: true })).toBeDisabled()
    await expect(app.getByRole('region', { name: `Order ${clean.id}`, exact: true })).toHaveCount(0)
    await expect(app.getByRole('button', { name: 'New order', exact: true })).toHaveCount(0)
    // Searching one line still shows its sibling and the unrelated accessory.
    await app.goto(`/duplicate-audit?order_id=${order.id}&q=4GB`)
    await expect(region).toBeVisible()
    await expect(region.locator('input[value="Separate accessory"]:visible')).toBeVisible()
    await app.getByText('Line IDs, receipts, and timestamps', { exact: true }).click()
    await expect(app.getByRole('cell', { name: `#${order.items[0].id}`, exact: true })).toBeVisible()
    await expect(app.getByRole('cell', { name: 'AUDIT-RECEIPT', exact: true })).toBeVisible()
    await app.screenshot({ path: testInfo.outputPath(`duplicate-audit-${width}.png`), fullPage: true })
    await app.getByRole('link', { name: 'Review in Orders', exact: true }).click()
    await expect(app).toHaveURL(new RegExp(`order_id=${order.id}`))
    await expect(app.getByRole('heading', { name: 'Orders', exact: true })).toBeVisible()
    await expect(region.getByLabel('Order number')).toBeEnabled()
    expect(await data.get<Order>(`/orders/${order.id}`)).toEqual(before)
    await app.goto(`/duplicate-audit?q=${encodeURIComponent(data.name('absent'))}`)
    await expect(app.getByText('No potentially duplicate orders match these filters.')).toBeVisible()
  })
}

test('Duplicate audit displays a failed request and retries', async ({ app }) => {
  await app.route('**/api/orders/paged?**', route => {
    if (new URL(route.request().url()).searchParams.has('duplicate_audit')) {
      return route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'Audit temporarily unavailable' }) })
    }
    return route.continue()
  })
  await app.getByRole('link', { name: 'Duplicate audit', exact: true }).click()
  await expect(app.getByRole('alert')).toContainText('Audit temporarily unavailable')
  await app.unroute('**/api/orders/paged?**')
  await app.getByRole('button', { name: 'Retry', exact: true }).click()
  await expect(app.getByRole('alert')).toHaveCount(0)
  await expect(app.getByRole('status')).toBeVisible()
})
