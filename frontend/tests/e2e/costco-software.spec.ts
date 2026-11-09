import { test, expect } from './fixtures'
import type { Order, Store } from '../../src/api/types'

for (const bundle of ['software', 'allstate', 'allstate-variable-price', 'allstate-sales-tax']) {
  test(`Costco bundled ${bundle} imports at zero payout and filters as no payment necessary`, async ({ app, data }) => {
    const stores = await data.get<Store[]>('/stores')
    if (!stores.some(store => store.name.toLowerCase() === 'costco')) {
      await data.create('/stores', { name: 'Costco' })
    }
    const payload = {
      store: 'costco',
      externalOrder: { id: data.name('costco-software'), orderDate: new Date().toISOString() },
      orderDiscount: 100.03,
      totals: { tax: bundle === 'allstate-sales-tax' ? 82.5 : 0 },
      items: [
        { name: 'Microsoft Surface Pro PC Bundle', quantities: { ordered: 1 }, pricing: { unitPrice: 1000 } },
        { name: 'Microsoft 365 Personal Subscription (e-delivery)', quantities: { ordered: 1 }, pricing: { unitPrice: 0.01 } },
        { name: 'McAfee Total Protection, 1 Device, (E-Delivery)', quantities: { ordered: 2 }, pricing: { unitPrice: 0.01 } },
      ],
    }
    if (bundle !== 'software') {
      const planPrice = bundle === 'allstate' ? 34.99 : 49.99
      payload.items = [
        { name: 'Samsung OLED TV', quantities: { ordered: 1 }, pricing: { unitPrice: 1000 } },
        { name: 'Allstate 3 Years (For TVs $500 to $1,000)', quantities: { ordered: 2 }, pricing: { unitPrice: planPrice } },
      ]
      payload.orderDiscount = 100 + 2 * planPrice
    }
    await app.goto(`/import-review#${Buffer.from(JSON.stringify(payload)).toString('base64')}`)
    await expect(app.getByText(payload.items[1].name, { exact: true })).toBeVisible()
    const taxSummary = app.getByRole('row').filter({ has: app.getByRole('cell', { name: 'Sales tax', exact: true }) })
    await expect(taxSummary.getByRole('cell', { name: `$${payload.totals.tax.toFixed(2)}`, exact: true })).toBeVisible()
    for (const item of payload.items.slice(1)) {
      await expect(app.getByRole('textbox', { name: `Unit payout for ${item.name.slice(0, 30)}`, exact: true })).toHaveValue('0.00')
    }
    const expectedPayout = 900 + payload.totals.tax
    await expect(app.getByRole('textbox', { name: `Unit payout for ${payload.items[0].name.slice(0, 30)}`, exact: true })).toHaveValue(expectedPayout.toFixed(2))
    const applied = app.waitForResponse(response => new URL(response.url()).pathname === '/api/integrations/stores/orders/apply' && response.request().method() === 'POST')
    await app.getByRole('button', { name: 'Import order', exact: true }).click()
    const response = await applied
    expect(response.ok(), await response.text()).toBeTruthy()
    const { order_id } = await response.json()
    data.track('/orders', order_id)
    const order = await data.get<Order>(`/orders/${order_id}`)
    expect(order.items).toHaveLength(payload.items.length)
    expect(Number(order.sales_tax)).toBe(payload.totals.tax)
    expect(Number(order.items.find(item => item.description === payload.items[0].name)?.sales_tax)).toBe(payload.totals.tax)
    expect(Number(order.items.find(item => item.description === payload.items[0].name)?.price_sold)).toBe(expectedPayout)
    const software = order.items.filter(item => item.description !== payload.items[0].name)
    for (const item of software) {
      expect(item.status).toBe('no_payment_necessary')
      expect(Number(item.price_sold)).toBe(0)
      expect(item.payment_id).toBeNull()
    }
    await expect(app).toHaveURL(url => url.pathname === '/')
    await app.getByRole('button', { name: 'Filter by status', exact: true }).click()
    await app.getByRole('checkbox', { name: 'No payment necessary', exact: true }).check()
    await app.getByRole('button', { name: 'Filter by status', exact: true }).click()
    const region = app.getByRole('region', { name: `Order ${order_id}`, exact: true })
    await expect(region).toBeVisible()
    // Selecting the filter exercises the server's enum query and the displayed status.
    const statusCells = region.getByRole('cell', { name: 'No payment necessary', exact: true })
    await expect(statusCells).toHaveCount(payload.items.length - 1)
    await expect(statusCells.first().getByRole('combobox')).toHaveValue('no_payment_necessary')
    const filtered = await data.get<Order[]>(`/orders?status=no_payment_necessary&search=${encodeURIComponent(payload.externalOrder.id)}`)
    expect(filtered.some(entry => entry.id === order_id)).toBeTruthy()
  })
}

test('Costco bulk review shows allocated line tax and an order tax summary', async ({ app, data }) => {
  const payload = {
    store: 'costco', externalOrder: { id: data.name('costco-tax-review') },
    orderDiscount: 169.99, totals: { tax: 90 },
    items: [
      { name: 'Samsung TV', quantities: { ordered: 2 }, pricing: { unitPrice: 300 } },
      { name: 'Soundbar', quantities: { ordered: 1 }, pricing: { unitPrice: 400 } },
      { name: 'Allstate Protection Plan', quantities: { ordered: 2 }, pricing: { unitPrice: 34.99 } },
      { name: 'Microsoft 365', quantities: { ordered: 1 }, pricing: { unitPrice: .01 } },
    ],
  }
  const response = await data.request.post('/api/integrations/stores/orders/bulk-session', {
    headers: data.headers, data: { orders: [payload] },
  })
  expect(response.ok(), await response.text()).toBeTruthy()
  const { token } = await response.json()
  await app.goto(`/import-review/bulk?token=${encodeURIComponent(token)}`)
  for (const [index, tax] of [54, 36, 0, 0].entries()) {
    const row = app.getByRole('row').filter({ has: app.getByRole('textbox', { name: `Description for item ${index + 1}`, exact: true }) })
    await expect(row.getByRole('cell').nth(6)).toHaveText(`$${tax.toFixed(2)}`)
  }
  const summary = app.getByRole('row').filter({ has: app.getByRole('cell', { name: 'Sales tax', exact: true }) })
  await expect(summary.getByRole('cell', { name: '$90.00', exact: true })).toBeVisible()
  const total = app.getByRole('row').filter({ has: app.getByRole('cell', { name: 'Order total', exact: true }) })
  await expect(total.getByRole('cell', { name: '$990.00', exact: true })).toHaveCount(2)
})
