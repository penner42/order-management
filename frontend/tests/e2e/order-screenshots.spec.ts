import { test, expect } from './fixtures'
import type { Order, Store } from '../../src/api/types'

test('screenshot history shows empty state, versions, image downloads, and errors', async ({ app, data, request }) => {
  const store = await data.create<Store>('/stores', { name: data.name('screenshots') })
  const order = await data.create<Order>('/orders', {
    store_id: store.id, store_order_number: data.name('screenshot-order'),
    items: [{ description: 'Screenshot test purchase', quantity: 1, price_paid: '20.00' }],
  })
  // Real API verifies authentication and the initial empty state.
  const anonymous = await request.get(`/api/orders/${order.id}/screenshots`)
  expect(anonymous.status()).toBe(401)
  const invalid = await request.get(`/api/orders/${order.id}/screenshots/1`, {
    headers: { Authorization: 'Bearer invalid-token' },
  })
  expect(invalid.status()).toBe(401)
  expect(await data.get(`/orders/${order.id}/screenshots`)).toEqual([])
  await app.goto('/')
  await app.getByLabel('Search orders', { exact: true }).fill(order.store_order_number!)
  await app.getByRole('button', { name: 'Screenshot history', exact: true }).click()
  const dialog = app.getByRole('dialog', { name: `Screenshot history · ${order.store_order_number}` })
  await expect(dialog.getByText(/No screenshots yet/)).toBeVisible()
  await dialog.getByRole('button', { name: 'Close', exact: true }).click()

  // UI versions are deterministic fixtures; actual capture/storage is exercised
  // by backend tests against isolated databases and real Chromium pages.
  await app.route(`**/api/orders/${order.id}/screenshots`, route => route.fulfill({ json: [
    { id: 2, captured_at: '2026-11-17T20:00:00Z', summary: 'Totals' },
    { id: 1, captured_at: '2026-11-16T20:00:00Z', summary: 'Initial capture' },
  ] }))
  await app.route(`**/api/orders/${order.id}/screenshots/*`, route => route.fulfill({
    contentType: 'image/png',
    body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aP1sAAAAASUVORK5CYII=', 'base64'),
  }))
  await app.getByRole('button', { name: 'Screenshot history', exact: true }).click()
  await expect(dialog.getByLabel('Screenshot version')).toHaveValue('2')
  await expect(dialog.getByRole('img')).toBeVisible()
  const download = app.waitForEvent('download')
  await dialog.getByRole('link', { name: 'Download PNG' }).click()
  expect((await download).suggestedFilename()).toContain(order.store_order_number!)
  await dialog.getByLabel('Screenshot version').selectOption('1')
  await expect(dialog.getByRole('img')).toHaveAttribute('alt', /captured/)
  await expect(dialog.getByRole('link', { name: 'Download PNG' })).toHaveAttribute('download', /-1.png$/)
  await app.route(`**/api/orders/${order.id}/screenshots/2`, route => route.fulfill({ status: 404 }))
  await dialog.getByLabel('Screenshot version').selectOption('2')
  await expect(dialog.getByRole('alert')).toHaveText('Could not load screenshot')
  await app.keyboard.press('Escape')
  await expect(dialog).toBeHidden()
})
