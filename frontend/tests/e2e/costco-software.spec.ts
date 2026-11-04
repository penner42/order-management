import { test, expect } from './fixtures'
import type { Order, Store } from '../../src/api/types'

test('Costco bundled software imports at zero payout and filters as no payment necessary', async ({ app, data }) => {
  const stores = await data.get<Store[]>('/stores')
  if (!stores.some(store => store.name.toLowerCase() === 'costco')) {
    await data.create('/stores', { name: 'Costco' })
  }
  const payload = {
    store: 'costco',
    externalOrder: { id: data.name('costco-software'), orderDate: new Date().toISOString() },
    orderDiscount: 100.03,
    items: [
      { name: 'Microsoft Surface Pro PC Bundle', quantities: { ordered: 1 }, pricing: { unitPrice: 1000 } },
      { name: 'Microsoft 365 Personal Subscription (e-delivery)', quantities: { ordered: 1 }, pricing: { unitPrice: 0.01 } },
      { name: 'McAfee Total Protection, 1 Device, (E-Delivery)', quantities: { ordered: 2 }, pricing: { unitPrice: 0.01 } },
    ],
  }
  await app.goto(`/import-review#${Buffer.from(JSON.stringify(payload)).toString('base64')}`)
  await expect(app.getByText(payload.items[1].name, { exact: true })).toBeVisible()
  for (const item of payload.items.slice(1)) {
    await expect(app.getByRole('textbox', { name: `Unit payout for ${item.name.slice(0, 30)}`, exact: true })).toHaveValue('0.00')
  }
  await expect(app.getByRole('textbox', { name: `Unit payout for ${payload.items[0].name.slice(0, 30)}`, exact: true })).toHaveValue('900.00')
  const applied = app.waitForResponse(response => new URL(response.url()).pathname === '/api/integrations/stores/orders/apply' && response.request().method() === 'POST')
  await app.getByRole('button', { name: 'Import order', exact: true }).click()
  const response = await applied
  expect(response.ok(), await response.text()).toBeTruthy()
  const { order_id } = await response.json()
  data.track('/orders', order_id)
  const order = await data.get<Order>(`/orders/${order_id}`)
  expect(order.items).toHaveLength(3)
  expect(Number(order.items.find(item => item.description === payload.items[0].name)?.price_sold)).toBe(900)
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
  await expect(statusCells).toHaveCount(2)
  await expect(statusCells.first().getByRole('combobox')).toHaveValue('no_payment_necessary')
  const filtered = await data.get<Order[]>(`/orders?status=no_payment_necessary&search=${encodeURIComponent(payload.externalOrder.id)}`)
  expect(filtered.some(entry => entry.id === order_id)).toBeTruthy()
})
