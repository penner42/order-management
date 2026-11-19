import { test, expect } from './fixtures'
import type { Item } from '../../src/api/types'

test('GoBuyMe tracking requires an explicit choice when the reservation is ambiguous', async ({ app, data }) => {
  const group = await data.create('/buying-groups', {
    name: data.name('gobuyme'), api_framework: 'gobuyme', api_username: 'buyer@example.com',
    api_password: 'fixture-password', bearer_token: 'fixture-key',
  })
  const store = await data.create('/stores', { name: data.name('store') })
  const order = await data.create<{ id: number; items: Item[] }>('/orders', {
    store_id: store.id, store_order_number: data.name('order'), buying_group_id: group.id,
    items: [{ description: 'GoBuyMe fixture', status: 'shipped', quantity: 2 }],
  })
  const item = order.items[0]
  const tracking = '1Z0000000000000000'
  await data.create('/shipments', { tracking_number: tracking, item_ids: [item.id] })
  const path = `/items/${item.id}/submit-tracking`
  const blocked = await data.request.post(`/api${path}`, { headers: data.headers, data: {} })
  expect(blocked.status()).toBe(403)

  await app.route('**/api/capabilities', route => route.fulfill({ json: { group_submissions_enabled: true } }))
  const requests: unknown[] = []
  await app.route(`**/api${path}`, async route => {
    const body = route.request().postDataJSON()
    requests.push(body)
    if (!body.gobuyme_selection) {
      await route.fulfill({ status: 409, json: { detail: {
        code: 'gobuyme_reservation_choice', message: 'Multiple matching reservations.', tracking, units: 2,
        choices: [
          { id: 'first', title: 'First reservation', deal_number: 'D1', remaining_units: 2, exact_match: true },
          { id: 'second', title: 'Second reservation', deal_number: 'D2', remaining_units: 4, exact_match: true },
        ],
      } } })
      return
    }
    await route.fulfill({ json: { item: { ...item, status: 'submitted' }, message: 'Tracker link verified', affected: 1 } })
  })

  await app.goto(`/?order_id=${order.id}`)
  await app.getByLabel(`Order ${order.id}`, { exact: true }).getByRole('button', { name: 'Submit tracking to buying group' }).click()
  const dialog = app.getByRole('dialog', { name: 'Choose a GoBuyMe reservation' })
  const submit = dialog.getByRole('button', { name: 'Submit tracking' })
  await expect(submit).toBeDisabled()
  expect(requests).toEqual([{}])
  await dialog.getByRole('combobox', { name: 'Tracker reservation' }).selectOption('second')
  await submit.click()
  await expect(dialog).toBeHidden()
  expect(requests).toEqual([{}, { gobuyme_selection: { commitment_id: 'second', tracking, units: 2 } }])
})
