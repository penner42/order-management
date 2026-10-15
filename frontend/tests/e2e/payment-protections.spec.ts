import { test, expect } from './fixtures'
import type { Order, Payment } from '../../src/api/types'

for (const viewport of [{ width: 1280, height: 900 }, { width: 390, height: 844 }]) {
  test(`Orders preserves payments and disables payment actions at width ${viewport.width}`, async ({ app, data }) => {
    await app.setViewportSize(viewport)
    const store = await data.create('/stores', { name: data.name('store') })
    const group = await data.create('/buying-groups', { name: data.name('group') })
    const order = await data.create<Order>('/orders', {
      store_id: store.id, buying_group_id: group.id, store_order_number: data.name('order'),
      items: [{ description: data.name('item'), status: 'scanned', quantity: 2, price_sold: 25 }],
    })
    const payment = await data.create<Payment>('/payments', { buying_group_id: group.id })
    const allocation = await data.request.post(`/api/payments/${payment.id}/line-items`, {
      headers: data.headers, data: { item_id: order.items[0].id, amount: 50 },
    })
    expect(allocation.ok()).toBeTruthy()
    const before = await data.get<Payment>(`/payments/${payment.id}`)
    await app.goto('/')
    if (viewport.width < 640) {
      await app.getByRole('button', { name: 'Filters', exact: true }).click()
      const filters = app.getByRole('dialog', { name: 'Filters', exact: true })
      await filters.getByLabel('Search orders', { exact: true }).fill(data.name('order'))
      await filters.getByRole('button', { name: 'Apply', exact: true }).click()
    } else {
      await app.getByLabel('Search orders', { exact: true }).fill(data.name('order'))
    }
    const region = app.getByRole('region', { name: `Order ${order.id}`, exact: true })
    const status = region.locator('select:visible').filter({ has: app.locator('option[value="payment_requested"]') })
    await expect(status).toHaveValue('payment_requested')
    for (const value of ['payment_requested', 'payment_sent', 'payment_received']) {
      await expect(status.locator(`option[value="${value}"]`)).toBeDisabled()
    }
    for (const name of ['Delete order', 'Delete line item', 'Split for separate shipping']) {
      await expect(region.getByRole('button', { name, exact: true })).toBeDisabled()
    }
    await expect(region.getByRole('button', { name: 'Advance to Payment sent' })).toHaveCount(0)
    const saved = app.waitForResponse(response => response.url().endsWith(`/api/items/${order.items[0].id}`) && response.request().method() === 'PATCH')
    await status.selectOption('purchased')
    expect((await saved).ok()).toBeTruthy()
    await expect(status).toHaveValue('payment_requested')
    const after = await data.get<Payment>(`/payments/${payment.id}`)
    expect(after.line_items.map(({ item: _item, ...allocation }) => allocation))
      .toEqual(before.line_items.map(({ item: _item, ...allocation }) => allocation))
    expect(after.payment_requested_at).toBe(before.payment_requested_at)
    expect(after.payment_sent_at).toBeNull()
    expect(after.payment_received_at).toBeNull()

    const headers = data.headers
    const blocked = await data.request.delete(`/api/orders/${order.id}`, { headers })
    expect(blocked.status()).toBe(409)
    const blockedLine = await data.request.delete(`/api/items/${order.items[0].id}`, { headers })
    expect(blockedLine.status()).toBe(409)
    const invalidStatus = await data.request.patch(`/api/items/${order.items[0].id}`, { headers, data: { status: 'payment_sent' } })
    expect(invalidStatus.status()).toBe(422)
  })
}
