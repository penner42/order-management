import { test, expect } from './fixtures'
import type { BrowserProfile } from '../../src/api/types'

test('Settings shows a login notice while store or buying group browsers need sign-in', async ({ app }) => {
  const store = { store_id: 1, retailer: 'walmart', status: 'login_required' }
  const group = { buying_group_id: 2, store_id: null, retailer: 'buyinggroup', status: 'ready' }
  const monarch = { store_id: null, retailer: 'monarch', status: 'login_required' }
  await app.route('**/api/browser-profiles', route => route.fulfill({ json: [store, group, monarch] }))
  await app.route('**/api/buying-groups/receiving-review/has-pending', route => route.fulfill({ json: false }))
  const refresh = () => app.evaluate(() => window.dispatchEvent(new Event('focus')))

  await app.goto('/')
  const settings = app.getByRole('link', { name: 'Settings', exact: true })
  const dot = settings.locator('[aria-hidden="true"]')
  await expect(settings).toHaveAccessibleDescription('Store browser sessions require login')
  await expect(dot).toBeVisible()
  const placement = await dot.evaluate(element => {
    const label = document.createRange()
    label.selectNodeContents(element.parentElement!.firstChild!)
    const text = label.getBoundingClientRect()
    const badge = element.getBoundingClientRect()
    return { gap: badge.left - text.right, rise: (text.top + text.bottom - badge.top - badge.bottom) / 2 }
  })
  expect(placement.gap).toBeGreaterThanOrEqual(0)
  expect(placement.gap).toBeLessThanOrEqual(4)
  expect(placement.rise).toBeGreaterThan(2)

  group.status = 'logged_out'
  await refresh()
  await expect(settings).toHaveAccessibleDescription('Store and buying group browser sessions require login')

  store.status = 'ready'
  await app.setViewportSize({ width: 390, height: 844 })
  await refresh()
  const menu = app.getByRole('button', { name: 'Open navigation menu' })
  await expect(menu).toHaveAccessibleDescription('Buying group browser sessions require login')
  await expect(menu.locator('span[aria-hidden="true"]')).toBeVisible()
  await menu.click()
  await expect(settings).toHaveAccessibleDescription('Buying group browser sessions require login')
  await expect(dot).toBeVisible()

  group.status = 'ready'
  await refresh()
  await expect(settings).toHaveAccessibleDescription('')
  await expect(dot).toHaveCount(0)
})

test('Settings marks the paths to the affected account and buying group sign-in controls', async ({ app, data }) => {
  const storeName = data.name('store')
  const accountName = data.name('account')
  const groupName = data.name('group')
  const store = await data.create('/stores', { name: storeName })
  const account = await data.create(`/stores/${store.id}/accounts`, { name: accountName })
  const storeProfile = await data.create<BrowserProfile>('/browser-profiles', {
    store_id: store.id, store_account_id: account.id, retailer: 'walmart',
  })
  const group = await data.create('/buying-groups', { name: groupName, api_framework: 'buyinggroup' })
  const connection = await data.request.post(`/api/buying-groups/${group.id}/browser-connection`, {
    headers: data.headers, data: {},
  })
  expect(connection.ok()).toBeTruthy()
  const { profile_id: groupProfileId } = await connection.json()
  const profiles = (await data.get<BrowserProfile[]>('/browser-profiles'))
    .filter(profile => profile.id === storeProfile.id || profile.id === groupProfileId)
  await app.route('**/api/browser-profiles', route => route.fulfill({ json: profiles }))

  await app.goto('/settings/account')
  const sections = app.getByRole('navigation', { name: 'Settings sections' })
  const stores = sections.getByRole('link', { name: 'Stores', exact: true })
  const groups = sections.getByRole('link', { name: 'Buying groups', exact: true })
  await expect(stores).toHaveAccessibleDescription('Browser login required')
  await expect(groups).toHaveAccessibleDescription('Browser login required')
  await stores.click()
  const storeButton = app.getByRole('button', { name: storeName, exact: true })
  await expect(storeButton).toHaveAccessibleDescription('Browser login required')
  await storeButton.click()
  const accountPanel = app.getByRole('listitem', { name: `Store account ${account.id}`, exact: true })
  await expect(accountPanel.getByText(accountName, { exact: true })).toHaveAttribute('title', 'Browser login required')
  await expect(accountPanel.getByRole('button', { name: 'Log in', exact: true })).toHaveAccessibleDescription('Browser login required')

  await groups.click()
  const groupRow = app.getByRole('listitem').filter({ hasText: groupName })
  await expect(groupRow.getByText(groupName, { exact: true })).toHaveAttribute('title', 'Browser login required')
  await expect(groupRow.getByRole('button', { name: 'Sign in with browser', exact: true })).toHaveAccessibleDescription('Browser login required')

  await app.setViewportSize({ width: 390, height: 844 })
  const selector = app.getByLabel('Settings section', { exact: true })
  await expect(selector.getByRole('option', { name: 'Buying groups • Browser login required' })).toBeAttached()
  await selector.selectOption('/settings/stores')
  await expect(storeButton).toHaveAccessibleDescription('Browser login required')
})
