import { test, expect } from './fixtures'

test('Orders shows a tracking reward once, including filtered lines and shared orders on other pages', async ({ app }) => {
  const group = { id: 987650, name: 'Mock BG reward group', api_framework: 'buyinggroup' }
  const makeItem = (id: number, orderId: number) => ({
    id, order_id: orderId, quantity: 3, description: `Reward widget ${id}`, status: 'scanned',
    price_paid: '10.00', price_sold: '20.00', reward: '0.02', receipt_id: 'R1',
  })
  const makeOrder = (id: number, ids: number[]) => ({
    id, store_id: 987650, store_account_id: null, buying_group_id: group.id,
    store_order_number: `REWARD-${id}`, status: 'imported', purchase_date: '2026-10-01T12:00:00Z',
    order_discount: '0', insurance_cost: '0', order_payments: [], items: ids.map(itemId => makeItem(itemId, id)),
    buying_group: group, tracking_rewards: [{ id: 123, tracking_number: 'TRACK', amount: '0.05',
      item_ids: ids, shared_order_count: 2 }],
  })
  const orders = [makeOrder(987651, [987651, 987652]), makeOrder(987653, [987653])]
  await app.route('**/api/buying-groups', route => route.fulfill({ json: [group] }))
  await app.route('**/api/stores', route => route.fulfill({ json: [{ id: 987650, name: 'Mock reward store' }] }))
  await app.route('**/api/shipments', route => route.fulfill({ json: [{
    id: 987650, tracking_number: 'TRACK', shipment_items: [987651, 987652].map(item_id => ({ item_id })),
  }, { id: 987651, tracking_number: 'SHIPPING-ALIAS', shipment_items: [{ item_id: 987653 }] }] }))
  await app.route('**/api/orders/paged?*', async route => {
    const url = new URL(route.request().url())
    const page = Number(url.searchParams.get('page') ?? 1)
    const filtered = !!url.searchParams.get('q')
    const order = filtered ? { ...orders[0], items: [orders[0].items[1]] } : orders[page - 1]
    await route.fulfill({ json: { items: [order], page: filtered ? 1 : page,
      per_page: 25, total: filtered ? 1 : 26, pages: filtered ? 1 : 2, available_buying_group_ids: [group.id] } })
  })
  await app.reload()
  const reward = app.getByText('Scan reward $0.05 · shared across orders', { exact: true })
  await expect(reward).toHaveCount(1)
  await expect(app.getByRole('columnheader', { name: 'Reward', exact: true })).toHaveCount(0)
  await app.getByRole('button', { name: 'Next page', exact: true }).first().click()
  await expect(app.getByText('Warehouse tracking TRACK: Scan reward $0.05 · shared across orders', { exact: true })).toBeVisible()
  await app.getByRole('button', { name: 'Previous page', exact: true }).first().click()
  await expect(reward).toHaveCount(1)
  await app.getByPlaceholder('Search order #, item, store, amount…').last().fill('Reward widget 987652')
  await expect(app.locator('input[value="Reward widget 987651"]')).toHaveCount(0)
  await expect(reward).toHaveCount(1)
})
