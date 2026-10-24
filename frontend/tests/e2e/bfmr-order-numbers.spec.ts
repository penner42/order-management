import { test, expect } from './fixtures'
import type { BfmrOrderTask } from '../../src/api/types'

test('imports queue BFMR product identities and development blocks submission', async ({ app, data }) => {
  expect((await data.get<{ group_submissions_enabled: boolean }>('/capabilities')).group_submissions_enabled).toBe(false)
  const group = await data.create('/buying-groups', {
    name: data.name('bfmr'), api_framework: 'bfmr', api_username: 'fixture-key', api_password: 'fixture-secret',
    order_number_submit_enabled: true,
  })
  const store = await data.create('/stores', { name: data.name('store') })
  const title = data.name('Imported BFMR widget')
  const response = await data.request.post('/api/integrations/stores/orders/apply', {
    headers: data.headers,
    data: { store_id: store.id, buying_group_id: group.id, payload: {
      store: 'amazon', externalOrder: { id: data.name('order') },
      items: [{ logicalItemId: 'B0D3J71RM7', externalSku: 'B0D3J71RM7', name: title,
        productUrl: 'https://amazon.com/dp/B0D3J71RM7', quantities: { ordered: 2 } }],
    } },
  })
  expect(response.ok(), await response.text()).toBeTruthy()
  const { order_id } = await response.json()
  data.track('/orders', order_id)
  const tasks = await data.get<BfmrOrderTask[]>(`/buying-groups/${group.id}/order-numbers`)
  expect(tasks).toHaveLength(1)
  expect(tasks[0].snapshot.sku).toBe('B0D3J71RM7')
  expect(tasks[0].snapshot.quantity).toBe(2)
  expect(tasks[0].status).toBe('pending')
  await app.goto(`/bfmr-order-review?group=${group.id}`)
  await expect(app.getByRole('heading', { name: 'BFMR order numbers', exact: true })).toBeVisible()
  await expect(app.getByRole('heading', { name: title, exact: true })).toBeVisible()
  await expect(app.getByRole('button', { name: 'Retry reconciliation', exact: true })).toBeDisabled()
  for (const action of ['retry', 'allocations']) {
    const blocked = await data.request.post(`/api/buying-groups/${group.id}/order-numbers/${tasks[0].id}/${action}`, {
      headers: data.headers, data: action === 'allocations' ? { allocations: [{ reserve_id: 'fixture', qty: 2 }] } : {},
    })
    expect(blocked.status()).toBe(403)
  }
  await app.goto('/settings/buying-groups')
  const row = app.getByRole('listitem').filter({ hasText: data.name('bfmr') })
  await row.getByRole('button', { name: 'Edit', exact: true }).click()
  await expect(app.getByRole('checkbox', { name: 'Auto-submit order numbers after import', exact: true })).toBeDisabled()
})

test('review allocates one product across commitments and retains data on refresh failure', async ({ app }) => {
  const task: BfmrOrderTask = {
    id: 123, order_id: 456, buying_group_id: 987654, product_key: 'sku:asin',
    snapshot: { retailer: 'amazon', sku: 'ASIN', title: 'Split commitment widget', product_url: null, order_no: 'ORDER-REVIEW', quantity: 3 },
    allocations: [], status: 'review', message: 'Choose commitments', attempts: 1, next_attempt_at: null,
  }
  let fail = false
  let selected: unknown = null
  await app.route('**/api/capabilities', route => route.fulfill({ json: { group_submissions_enabled: true } }))
  await app.route('**/api/buying-groups', route => route.fulfill({ json: [
    { id: 987654, name: 'Mock BFMR', api_framework: 'bfmr', order_number_submit_enabled: true },
  ] }))
  await app.route('**/api/buying-groups/987654/order-numbers', route => fail
    ? route.fulfill({ status: 502, json: { detail: 'Temporary review failure' } })
    : route.fulfill({ json: [task] }))
  await app.route('**/api/buying-groups/987654/order-numbers/123/candidates', route => route.fulfill({ json: [
    { reserve_id: 'R1', qty: 2, title: 'First commitment', exact_match: true, retailer_links: [{ retailer: 'Amazon', identifier_id: 'ASIN' }] },
    { reserve_id: 'R2', qty: 1, title: 'Second commitment', exact_match: true, retailer_links: [{ retailer: 'Amazon', identifier_id: 'ASIN' }] },
  ] }))
  await app.route('**/api/buying-groups/987654/order-numbers/123/allocations', async route => {
    selected = route.request().postDataJSON()
    task.status = 'pending'
    task.message = 'Manual allocations queued'
    await route.fulfill({ json: task })
  })
  await app.goto('/bfmr-order-review?group=987654')
  await expect(app.getByRole('heading', { name: 'Split commitment widget' })).toBeVisible()
  await app.getByRole('button', { name: 'Refresh commitments', exact: true }).click()
  await app.getByRole('spinbutton', { name: 'Quantity for First commitment (R1)', exact: true }).fill('2')
  await app.getByRole('spinbutton', { name: 'Quantity for Second commitment (R2)', exact: true }).fill('1')
  await app.getByRole('button', { name: 'Save allocations and queue submission (3/3)', exact: true }).click()
  await expect(app.getByText('Manual allocations queued', { exact: true })).toBeVisible()
  expect(selected).toEqual({ allocations: [{ reserve_id: 'R1', qty: 2 }, { reserve_id: 'R2', qty: 1 }] })
  fail = true
  await app.getByRole('button', { name: 'Refresh status', exact: true }).click()
  await expect(app.getByRole('alert')).toContainText('Temporary review failure')
  await expect(app.getByRole('heading', { name: 'Split commitment widget' })).toBeVisible()
})
