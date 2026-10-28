import { test, expect, createThroughUI } from './fixtures'
import type { Locator, Page } from '@playwright/test'
import type { Order, PaymentMethod, Shipment, Store } from '../../src/api/types'

test.setTimeout(60_000)

async function saveOnBlur(page: Page, input: Locator, value: string, path: string) {
  await expect(input).toBeEnabled()
  await input.fill(value)
  const saved = page.waitForResponse(response => new URL(response.url()).pathname === `/api${path}` && response.request().method() === 'PATCH')
  await input.blur()
  expect((await saved).ok()).toBeTruthy()
  await expect(input).toBeEnabled()
}

test('session survives refresh and an API 401 returns to sign in', async ({ app }) => {
  await app.reload()
  await expect(app.getByRole('heading', { name: 'Orders', exact: true })).toBeVisible()
  await app.route('**/api/stores', route => route.fulfill({ status: 401, json: { detail: 'Session expired' } }))
  await app.getByRole('link', { name: 'Settings', exact: true }).click()
  await app.getByRole('link', { name: 'Stores', exact: true }).click()
  await expect(app).toHaveURL(/\/login$/)
  expect(await app.evaluate(() => localStorage.getItem('auth_token'))).toBeNull()
})

test('stores and accounts support edits, persistence and confirmed deletion', async ({ app, data }) => {
  const name = data.name('store')
  await app.goto('/settings/stores')
  await app.getByPlaceholder('New store name').fill(name)
  const store = await createThroughUI(app, '/stores', () => app.getByRole('button', { name: 'Add store', exact: true }).click())
  const row = app.getByRole('row', { name: `Store ${store.id}`, exact: true })
  await row.getByRole('button', { name: 'Edit store and accounts', exact: true }).click()
  const accountName = data.name('account')
  await app.getByPlaceholder('New account name').fill(accountName)
  const account = await createThroughUI(app, `/stores/${store.id}/accounts`, () => app.getByRole('button', { name: 'Add account', exact: true }).click())
  const accountRow = app.getByRole('listitem', { name: `Store account ${account.id}`, exact: true })
  await accountRow.getByRole('button', { name: 'Edit account' }).click()
  await accountRow.getByLabel('Account name').fill(`${accountName}-edited`)
  await accountRow.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(accountRow).toContainText(`${accountName}-edited`)
  await row.getByLabel('Store name').fill(`${name}-edited`)
  await row.getByRole('button', { name: 'Save', exact: true }).click()
  await app.reload()
  await expect(row).toContainText(`${name}-edited`)
  await expect(row).toContainText(`${accountName}-edited`)

  await row.getByRole('button', { name: 'Edit store and accounts', exact: true }).click()
  await accountRow.getByRole('button', { name: 'Delete account' }).click()
  await app.getByRole('dialog').getByRole('button', { name: 'Cancel', exact: true }).click()
  await expect(accountRow).toContainText(`${accountName}-edited`)
  await accountRow.getByRole('button', { name: 'Delete account' }).click()
  await app.getByRole('dialog').getByRole('button', { name: 'Delete', exact: true }).click()
  await expect(accountRow).toHaveCount(0)
  await row.getByRole('button', { name: 'Cancel', exact: true }).click()
  await row.getByRole('button', { name: 'Delete store', exact: true }).click()
  await app.getByRole('dialog').getByRole('button', { name: 'Cancel', exact: true }).click()
  await expect(row).toBeVisible()
  await row.getByRole('button', { name: 'Delete store', exact: true }).click()
  await app.getByRole('dialog').getByRole('button', { name: 'Delete store', exact: true }).click()
  await expect(row).toHaveCount(0)
  await app.reload()
  await expect(row).toHaveCount(0)
})

test('rewards support edits, persistence and confirmed deletion', async ({ app, data }) => {
  const name = data.name('reward')
  await app.goto('/settings/rewards')
  await app.getByPlaceholder('New reward name').fill(name)
  const reward = await createThroughUI(app, '/rewards', () => app.getByRole('button', { name: 'Add', exact: true }).click())
  const row = app.getByRole('listitem', { name: `Reward ${reward.id}`, exact: true })
  await row.getByRole('button', { name: 'Edit', exact: true }).click()
  await row.getByLabel('Reward name').fill(`${name}-edited`)
  await row.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(row).toContainText(`${name}-edited`)
  await app.reload()
  await expect(row).toContainText(`${name}-edited`)
  await row.getByRole('button', { name: 'Delete', exact: true }).click()
  await app.getByRole('dialog').getByRole('button', { name: 'Cancel' }).click()
  await expect(row).toBeVisible()
  await row.getByRole('button', { name: 'Delete', exact: true }).click()
  await app.getByRole('dialog').getByRole('button', { name: 'Delete', exact: true }).click()
  await expect(row).toHaveCount(0)
  await app.reload()
  await expect(row).toHaveCount(0)
})

test('parent and child payment methods save rewards and per-store earnings', async ({ app, data }) => {
  const store = await data.create('/stores', { name: data.name('earnings-store') })
  const rewardName = data.name('points')
  await data.create('/rewards', { name: rewardName })
  const parentName = data.name('card')
  await app.goto('/settings/payment-methods')
  await app.getByRole('button', { name: 'Add payment method', exact: true }).click()
  await app.getByPlaceholder('e.g. Visa ****1234, PayPal - Amex').fill(parentName)
  await app.getByLabel('Rewards type').fill(rewardName)
  await app.getByRole('option', { name: rewardName, exact: true }).click()
  const parent = await createThroughUI(app, '/payment-methods', () => app.getByRole('button', { name: 'Add', exact: true }).click())
  const parentRow = app.getByRole('listitem', { name: `Payment method ${parent.id}`, exact: true })
  await expect(parentRow).toContainText(rewardName)
  await parentRow.getByRole('button', { name: '+ Sub-method', exact: true }).click()
  const childName = data.name('child-card')
  await app.getByPlaceholder('Sub-method label (inherits reward)').fill(childName)
  const child = await createThroughUI(app, '/payment-methods', () => app.getByRole('button', { name: 'Add', exact: true }).click())

  for (const [method, name, rate] of [[child, childName, '3.5'], [parent, parentName, '2.25']] as const) {
    const row = app.getByRole('listitem', { name: `Payment method ${method.id}`, exact: true })
    await row.getByRole('button', { name: 'Edit', exact: true }).click()
    await row.getByLabel('Payment method label').fill(`${name}-edited`)
    await row.getByLabel(`Points per dollar at ${data.name('earnings-store')}`, { exact: true }).fill(rate)
    const saved = app.waitForResponse(response => new URL(response.url()).pathname === `/api/payment-methods/${method.id}/store-earnings` && response.request().method() === 'PUT')
    await row.getByRole('button', { name: 'Save', exact: true }).click()
    expect((await saved).ok()).toBeTruthy()
    await expect(row).toContainText(`${name}-edited`)
    const earnings = await data.get<Array<{ store_id: number; points_per_dollar: string | number }>>(`/payment-methods/${method.id}/store-earnings`)
    expect(Number(earnings.find(entry => entry.store_id === store.id)?.points_per_dollar)).toBe(Number(rate))
  }
  // Updating the parent must preserve its already-edited child in local state.
  const childRow = app.getByRole('listitem', { name: `Payment method ${child.id}`, exact: true })
  await expect(childRow).toContainText(`${childName}-edited`)
  await app.reload()
  await expect(parentRow).toContainText(`${parentName}-edited`)
  await expect(childRow).toContainText(`${childName}-edited`)
  await expect(childRow).toContainText(rewardName)
  const persisted = await data.get<PaymentMethod>(`/payment-methods/${parent.id}`)
  expect(persisted.sub_methods?.map(method => method.id)).toContain(child.id)
})

test('orders create a line item and persist quantity, price and payout edits', async ({ app, data }) => {
  const store = await data.create('/stores', { name: data.name('order-store') })
  await app.reload()
  const order = await createThroughUI(app, '/orders', () => app.getByRole('button', { name: 'New order', exact: true }).click())
  const region = app.getByRole('region', { name: `Order ${order.id}`, exact: true })
  await expect(region).toBeVisible()
  // New order creation also creates its first line item.
  await expect.poll(async () => (await data.get<Order>(`/orders/${order.id}`)).items.length).toBe(1)
  const persistedOrder = await data.get<Order>(`/orders/${order.id}`)
  const item = persistedOrder.items[0]
  await region.getByLabel('Store or account…', { exact: true }).fill(data.name('order-store'))
  const storeSaved = app.waitForResponse(response => new URL(response.url()).pathname === `/api/orders/${order.id}` && response.request().method() === 'PATCH')
  await app.getByRole('option').filter({ hasText: data.name('order-store') }).click()
  expect((await storeSaved).ok()).toBeTruthy()
  await saveOnBlur(app, region.getByLabel('Order number', { exact: true }), data.name('order-number'), `/orders/${order.id}`)
  await saveOnBlur(app, region.getByRole('textbox', { name: 'Description', exact: true }), data.name('item'), `/items/${item.id}`)
  await saveOnBlur(app, region.getByRole('spinbutton', { name: 'Quantity', exact: true }), '2', `/items/${item.id}`)
  await saveOnBlur(app, region.getByRole('textbox', { name: 'Purchase price', exact: true }), '10.25', `/items/${item.id}`)
  await saveOnBlur(app, region.getByRole('textbox', { name: 'Payout price', exact: true }), '15.50', `/items/${item.id}`)
  await expect(region).toContainText('$20.50')
  await expect(region).toContainText('$31.00')
  await app.reload()
  await app.getByLabel('Search orders', { exact: true }).fill(data.name('order-number'))
  await expect(region.getByRole('spinbutton', { name: 'Quantity', exact: true })).toHaveValue('2')
  await expect(region.getByRole('textbox', { name: 'Purchase price', exact: true })).toHaveValue('10.25')
  await expect(region.getByRole('textbox', { name: 'Payout price', exact: true })).toHaveValue('15.50')
  const updated = await data.get<Order>(`/orders/${order.id}`)
  expect(updated.store_id).toBe(store.id)
  expect(updated.items[0].quantity).toBe(2)
})

test('Walmart import reviews and applies physical items without email delivery', async ({ app, data }) => {
  const stores = await data.get<Store[]>('/stores')
  if (!stores.some(store => store.name.toLowerCase() === 'walmart')) await data.create('/stores', { name: 'Walmart' })
  const trackingNumber = `1Z${data.prefix.replace(/[^a-f0-9]/g, '').slice(-16).toUpperCase()}`
  const payload = {
    store: 'walmart', externalOrder: { id: data.name('import'), orderDate: new Date().toISOString() },
    shipments: [{ shipmentId: 'box', trackingNumber }, { shipmentId: 'email', trackingNumber: 'Sent via email' }],
    items: [
      { logicalItemId: 'physical', name: data.name('physical'), quantities: { ordered: 1 }, pricing: { unitPrice: 10 }, shipments: [{ shipmentId: 'box', quantity: 1 }] },
      { logicalItemId: 'digital', name: data.name('digital'), quantities: { ordered: 1 }, pricing: { unitPrice: 5 }, shipments: [{ shipmentId: 'email', quantity: 1 }] },
    ],
  }
  await app.goto(`/import-review#${Buffer.from(JSON.stringify(payload)).toString('base64')}`)
  await expect(app.getByText(data.name('physical'), { exact: true })).toBeVisible()
  await expect(app.getByText(data.name('digital'), { exact: true })).toHaveCount(0)
  const applied = app.waitForResponse(response => new URL(response.url()).pathname === '/api/integrations/stores/orders/apply' && response.request().method() === 'POST')
  await app.getByRole('button', { name: 'Import order', exact: true }).click()
  const response = await applied
  expect(response.ok(), await response.text()).toBeTruthy()
  const { order_id } = await response.json()
  data.track('/orders', order_id)
  // Register generated shipments before assertions so failures still clean up.
  const shipments = await data.get<Shipment[]>('/shipments')
  const createdShipments = shipments.filter(shipment => shipment.tracking_number === trackingNumber)
  for (const shipment of createdShipments) data.track('/shipments', shipment.id)
  const order = await data.get<Order>(`/orders/${order_id}`)
  expect(order.items.map(item => item.description)).toEqual([data.name('physical')])
  expect(createdShipments).toHaveLength(1)
  expect(createdShipments[0].shipment_items.map(link => link.item_id)).toContain(order.items[0].id)
  expect(order.items[0].status).toBe('shipped')

  // Re-import must keep an existing shipped item's original tracking intact.
  const reimport = await data.request.post('/api/integrations/stores/orders/apply', {
    headers: data.headers,
    data: { payload: { ...payload, items: [payload.items[0]], shipments: [{ shipmentId: 'box', trackingNumber: `${trackingNumber}CHANGED` }] } },
  })
  expect(reimport.ok(), await reimport.text()).toBeTruthy()
  const original = await data.get<Shipment>(`/shipments/${createdShipments[0].id}`)
  expect(original.tracking_number).toBe(trackingNumber)
  expect(original.shipment_items.map(link => link.item_id)).toContain(order.items[0].id)
  expect((await data.get<Order>(`/orders/${order_id}`)).items).toHaveLength(1)
  await expect(app).toHaveURL(url => url.pathname === '/')
  await app.reload()
  await app.getByLabel('Search orders', { exact: true }).fill(data.name('import'))
  await expect(app.getByRole('region', { name: `Order ${order_id}`, exact: true }).getByRole('textbox', { name: 'Description', exact: true })).toHaveValue(data.name('physical'))
})

test('payments use group synchronization instead of manual creation', async ({ app }) => {
  await app.goto('/payments')
  await expect(app.getByRole('heading', { name: 'Payments', exact: true })).toBeVisible()
  await expect(app.getByRole('button', { name: 'Add payment', exact: true })).toHaveCount(0)
  await expect(app.getByRole('link', { name: 'Monarch review', exact: true })).toBeVisible()
  await app.getByLabel('Payment view').selectOption('legacy')
  await expect(app.getByText('These preserved manual payments are excluded from imported totals.', { exact: false })).toBeVisible()
})
