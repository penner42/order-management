import { test, expect, type TestData } from './fixtures'
import type { BfmrOrderTask } from '../../src/api/types'

async function importedOrder(data: TestData) {
  const group = await data.create('/buying-groups', {
    name: data.name('bfmr-manual'), api_framework: 'bfmr', api_username: 'fixture-key', api_password: 'fixture-secret',
    order_number_submit_enabled: false,
  })
  const store = await data.create('/stores', { name: data.name('costco') })
  const response = await data.request.post('/api/integrations/stores/orders/apply', {
    headers: data.headers, data: { store_id: store.id, buying_group_id: group.id, payload: {
      store: 'costco', externalOrder: { id: data.name('order') },
      items: [{ logicalItemId: '2040144', externalSku: '2040144', name: 'ASUS manual registration laptop', quantities: { ordered: 2 } }],
    } },
  })
  expect(response.ok(), await response.text()).toBeTruthy()
  const { order_id } = await response.json()
  data.track('/orders', order_id)
  const snapshot: BfmrOrderTask['snapshot'] = {
    retailer: 'costco', sku: '2040144', title: 'ASUS manual registration laptop', product_url: null,
    order_no: data.name('order'), quantity: 2,
  }
  return { group, order_id, snapshot, base: `/api/buying-groups/${group.id}/order-numbers/orders/${order_id}` }
}

test('Orders previews BFMR matches without automation or tasks and dev blocks writes', async ({ app, data }) => {
  const { group, order_id, snapshot, base } = await importedOrder(data)
  let writes = 0
  app.on('request', request => { if (request.url().endsWith(`${base}/submit`)) writes++ })
  await app.route(`**${base}/preview`, route => route.fulfill({ json: { order_no: snapshot.order_no, products: [{
    product_key: 'sku:2040144', snapshot, task: null,
    candidates: [{ reserve_id: 'R1', title: 'ASUS commitment', qty: 4, exact_match: true, eligible: true, reason: null }],
  }] } }))
  await app.goto(`/?order_id=${order_id}`)
  await app.getByLabel(`Order ${order_id}`, { exact: true }).getByRole('button', { name: 'BFMR order number', exact: true }).click()
  const dialog = app.getByRole('dialog', { name: 'BFMR order number', exact: true })
  await expect(dialog.getByText('Preview only:', { exact: false })).toBeVisible()
  await expect(dialog.getByText('ASUS commitment · 4 available · Exact product match')).toBeVisible()
  await expect(dialog.getByRole('spinbutton')).toHaveValue('2')
  await expect(dialog.getByRole('checkbox')).not.toBeChecked()
  await expect(dialog.getByRole('button', { name: 'Submit order number', exact: true })).toHaveCount(0)
  await dialog.getByRole('button', { name: 'Close', exact: true }).click()
  await expect(app.getByLabel(`Order ${order_id}`, { exact: true }).getByRole('button', { name: 'BFMR order number', exact: true })).toBeFocused()
  expect(writes).toBe(0)
  const tasks = await data.get<BfmrOrderTask[]>(`/buying-groups/${group.id}/order-numbers`)
  expect(tasks).toHaveLength(0)
  const blocked = await data.request.post(`${base}/submit`, { headers: data.headers, data: {
    products: [{ product_key: 'sku:2040144', snapshot, allocations: [{ reserve_id: 'R1', qty: 2 }] }],
  } })
  expect(blocked.status()).toBe(403)
})

test('Orders submits selected products with split commitments and shows partial outcomes inline', async ({ app, data }) => {
  const { group, order_id, snapshot, base } = await importedOrder(data)
  await app.route('**/api/capabilities', route => route.fulfill({ json: { group_submissions_enabled: true } }))
  let task: BfmrOrderTask | null = null
  let failRefresh = false
  await app.route(`**${base}/preview`, route => failRefresh
    ? route.fulfill({ status: 502, json: { detail: 'Commitment lookup failed' } })
    : route.fulfill({ json: { order_no: snapshot.order_no, products: [{ product_key: 'sku:2040144', snapshot, task,
      candidates: [
        { reserve_id: 'R1', title: 'First commitment', qty: 1, exact_match: true, eligible: true, reason: null },
        { reserve_id: 'R2', title: 'Second commitment', qty: 1, exact_match: true, eligible: true, reason: null },
      ],
    }] } }))
  let writes = 0
  await app.route(`**${base}/submit`, async route => {
    const body = route.request().postDataJSON()
    expect(body.products).toHaveLength(1)
    if (writes++ === 0) {
      expect(body.products[0].allocations).toEqual([{ reserve_id: 'R1', qty: 1 }, { reserve_id: 'R2', qty: 1 }])
      task = { id: 1, order_id, buying_group_id: group.id, product_key: 'sku:2040144', snapshot,
        allocations: [{ reserve_id: 'R1', qty: 1, status: 'submitted', purchase_id: 'P1' }, { reserve_id: 'R2', qty: 1, status: 'sending' }],
        status: 'review', message: 'Previous outcome is uncertain. Reconcile before sending.', attempts: 1, next_attempt_at: null }
    } else {
      expect(body.products[0].allocations).toEqual([])
      task = { ...task!, status: 'submitted', message: 'Order number registered with BFMR.', allocations: [
        { reserve_id: 'R1', qty: 1, status: 'submitted', purchase_id: 'P1' },
        { reserve_id: 'R2', qty: 1, status: 'submitted', purchase_id: 'P2' },
      ] }
    }
    await route.fulfill({ json: [task] })
  })
  await app.goto(`/?order_id=${order_id}`)
  await app.getByLabel(`Order ${order_id}`, { exact: true }).getByRole('button', { name: 'BFMR order number', exact: true }).click()
  const dialog = app.getByRole('dialog', { name: 'BFMR order number', exact: true })
  const submit = dialog.getByRole('button', { name: 'Submit order number', exact: true })
  await expect(submit).toBeDisabled()
  await dialog.getByRole('checkbox').check()
  await dialog.getByRole('spinbutton').nth(0).fill('1')
  await expect(submit).toBeDisabled()
  await dialog.getByRole('spinbutton').nth(1).fill('1')
  failRefresh = true
  await dialog.getByRole('button', { name: 'Refresh commitments' }).click()
  await expect(dialog.getByRole('alert')).toHaveText('Commitment lookup failed')
  await expect(dialog.getByRole('spinbutton').nth(0)).toHaveValue('1')
  failRefresh = false
  await submit.click()
  await expect(dialog.getByText('0/1 products registered.', { exact: false })).toBeVisible()
  await expect(dialog.getByRole('checkbox')).not.toBeChecked()
  await dialog.getByRole('checkbox').check()
  await submit.click()
  await expect(dialog.getByText('1/1 products registered.', { exact: false })).toBeVisible()
  await expect(dialog.getByRole('checkbox')).toBeDisabled()
  expect(writes).toBe(2)
  await expect(app).toHaveURL(new RegExp(`/\\?order_id=${order_id}`))
})
