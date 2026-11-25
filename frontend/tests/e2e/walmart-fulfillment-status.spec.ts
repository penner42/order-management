import { test, expect } from './fixtures'

test('Walmart fulfillment status appears beside UPC before and after tracking is assigned', async ({ app, data }) => {
  const store = await data.create('/stores', { name: data.name('store') })
  const trackingNumber = `1Z${data.prefix.replace(/[^a-f0-9]/g, '').slice(-16).toUpperCase()}`
  const stages = [
    { code: 'PLACED', label: 'Placed', upc: '036000291452' },
    { code: 'PREPARING', label: 'Preparing', upc: '042100005264' },
    { code: 'SHIPPED', label: 'On the way', upc: '012345678905', trackingNumber },
    { code: 'DELIVERED', label: 'Delivered' },
  ]
  const payload = {
    store: 'walmart',
    externalOrder: { id: data.name('order'), orderDate: new Date().toISOString() },
    shipments: stages.map(stage => ({
      shipmentId: stage.code, trackingNumber: stage.trackingNumber,
      status: { rawStatusType: stage.code },
    })),
    items: stages.map(stage => ({
      logicalItemId: stage.code, name: stage.label + ' widget', upc: stage.upc,
      quantities: { ordered: 1 }, pricing: { unitPrice: 10 },
      shipments: [{ shipmentId: stage.code, quantity: 1 }],
    })),
  }
  const response = await data.request.post('/api/integrations/stores/orders/apply', {
    headers: data.headers, data: { store_id: store.id, payload },
  })
  expect(response.ok(), await response.text()).toBeTruthy()
  const { order_id } = await response.json()
  data.track('/orders', order_id)
  await app.goto(`/?order_id=${order_id}`)
  const order = app.getByRole('region', { name: `Order ${order_id}`, exact: true })

  for (const width of [1280, 390]) {
    await app.setViewportSize({ width, height: 800 })
    for (const stage of stages) {
      const status = order.getByText(`Status: ${stage.label}`, { exact: true }).filter({ visible: true })
      await expect(status).toBeVisible()
      if (stage.upc) await expect(status.locator('..').getByText(`UPC: ${stage.upc}`, { exact: true })).toBeVisible()
    }
  }
})
