import { expect, test, createThroughUI } from './fixtures'
import type { BrowserProfile, StoreAccount } from '../../src/api/types'

test('main navigation and grouped settings support direct loads and history', async ({ app }) => {
  const mainNav = app.getByRole('navigation', { name: 'Main navigation', exact: true })
  await expect(mainNav.getByRole('link')).toHaveText(['Orders', 'Duplicate audit', 'Receiving', 'Payments', 'Log', 'Settings'])
  await app.locator('header a[href="/settings/account"]').click()
  await expect(app).toHaveURL(/\/settings\/account$/)
  await mainNav.getByRole('link', { name: 'Settings', exact: true }).click()
  await expect(app).toHaveURL(/\/settings\/account$/)
  await expect(app.getByRole('heading', { name: 'Account', exact: true })).toBeVisible()
  const sections = app.getByRole('navigation', { name: 'Settings sections', exact: true })
  const routes = [
    ['buying-groups', 'Buying groups', 'Buying groups'],
    ['stores', 'Stores', 'Stores'],
    ['ignored-zip-codes', 'Ignored zip codes', 'Ignored zip codes'],
    ['payment-methods', 'Payment methods', 'Payment methods'],
    ['rewards', 'Rewards', 'Rewards'],
    ['portals', 'Portals', 'Portals'],
    ['extension', 'Browser extension', 'Browser Extension'],
  ]
  for (const [slug, label, heading] of routes) {
    await sections.getByRole('link', { name: label, exact: true }).click()
    await expect(app).toHaveURL(new RegExp(`/settings/${slug}$`))
    await expect(sections.getByRole('link', { name: label, exact: true })).toHaveAttribute('aria-current', 'page')
    await expect(mainNav.getByRole('link', { name: 'Settings' })).toHaveAttribute('aria-current', 'page')
    await expect(mainNav.getByRole('link', { name: 'Orders', exact: true })).not.toHaveAttribute('aria-current')
    await expect(app.getByRole('heading', { name: heading, exact: true })).toBeVisible()
  }
  await app.reload()
  await expect(app.getByRole('heading', { name: 'Browser Extension', exact: true })).toBeVisible()
  await app.goBack()
  await expect(app).toHaveURL(/\/settings\/portals$/)
  await app.goForward()
  await expect(app).toHaveURL(/\/settings\/extension$/)
  await mainNav.getByRole('link', { name: 'Log', exact: true }).click()
  await expect(app).toHaveURL(/\/browser-import-log$/)
  await expect(sections).toHaveCount(0)
  await app.getByRole('link', { name: 'Stores', exact: true }).click()
  await expect(app).toHaveURL(/\/settings\/stores$/)
})

for (const [old, target, heading] of [
  ['stores', 'stores', 'Stores'], ['buying-groups', 'buying-groups', 'Buying groups'],
  ['rewards', 'rewards', 'Rewards'], ['payment-methods', 'payment-methods', 'Payment methods'],
  ['portals', 'portals', 'Portals'], ['extension', 'extension', 'Browser Extension'],
  ['browser-automation', 'stores', 'Stores'], ['profile', 'account', 'Account'],
]) {
  test(`legacy /${old} preserves query and fragment`, async ({ app }) => {
    await app.goto(`/${old}?legacy=yes#section`)
    await expect(app).toHaveURL(new RegExp(`/settings/${target}\\?legacy=yes#section$`))
    await expect(app.getByRole('heading', { name: heading, exact: true })).toBeVisible()
  })
}

test('store and subaccount profiles are created in context and can be moved and removed', async ({ app, data }) => {
  const store = await data.create('/stores', { name: data.name('profiles') })
  const account = await data.create<StoreAccount>(`/stores/${store.id}/accounts`, { name: data.name('account') })
  await app.goto(`/settings/stores?store_id=${store.id}&account_id=${account.id}`)
  const storePanel = app.getByRole('region', { name: `${data.name('profiles')} store browser profile`, exact: true })
  const accountPanel = app.getByRole('listitem', { name: `Store account ${account.id}`, exact: true })
  await expect(accountPanel).toBeVisible()
  await expect(storePanel.getByRole('button', { name: 'Add profile' })).toBeDisabled()
  await storePanel.getByRole('combobox', { name: 'Retailer', exact: true }).selectOption('walmart')
  const direct = await createThroughUI(app, '/browser-profiles', () => storePanel.getByRole('button', { name: 'Add profile' }).click())
  await expect(storePanel).toContainText('Store profile')
  await expect(storePanel.getByRole('button', { name: 'Log in', exact: true })).toBeVisible()
  await accountPanel.getByRole('combobox', { name: 'Retailer', exact: true }).selectOption('amazon')
  const sub = await createThroughUI(app, '/browser-profiles', () => accountPanel.getByRole('button', { name: 'Add profile' }).click())
  await expect(accountPanel.getByRole('button', { name: 'Log in', exact: true })).toBeVisible()
  const profiles = await data.get<BrowserProfile[]>('/browser-profiles')
  expect(profiles.find(p => p.id === direct.id)).toMatchObject({ store_id: store.id, store_account_id: null, retailer: 'walmart' })
  expect(profiles.find(p => p.id === sub.id)).toMatchObject({ store_id: store.id, store_account_id: account.id, retailer: 'amazon' })
  await accountPanel.getByLabel('pages', { exact: true }).fill('4')
  await accountPanel.getByRole('button', { name: 'Save schedule', exact: true }).click()
  await expect(accountPanel.getByRole('button', { name: 'Save schedule', exact: true })).toBeDisabled()
  await app.reload()
  await expect(accountPanel.getByRole('button', { name: 'Log in', exact: true })).toBeVisible()
  await expect(accountPanel.getByLabel('pages', { exact: true })).toHaveValue('4')
  expect(await data.get<BrowserProfile>(`/browser-profiles/${sub.id}`)).toMatchObject({ full_check_enabled: false, full_check_max_pages: 4 })

  const other = await data.create('/stores', { name: data.name('other') })
  // Editing another store clears an account deep link belonging to the previous store.
  await app.reload()
  const otherRow = app.getByRole('row', { name: `Store ${other.id}`, exact: true })
  await otherRow.getByRole('button', { name: 'Edit store and accounts', exact: true }).click()
  await expect(app).toHaveURL(new RegExp(`store_id=${other.id}$`))
  await otherRow.getByRole('button', { name: 'Cancel', exact: true }).click()
  await app.getByRole('row', { name: `Store ${store.id}`, exact: true }).getByRole('button', { name: 'Manage', exact: true }).click()
  await storePanel.getByRole('button', { name: 'Move profile', exact: true }).click()
  await storePanel.getByLabel('Profile owner', { exact: true }).selectOption(`store:${other.id}`)
  await expect(storePanel.getByRole('button', { name: 'Add profile' })).toBeVisible()
  expect(await data.get<BrowserProfile>(`/browser-profiles/${direct.id}`)).toMatchObject({ store_id: other.id, store_account_id: null })
  await app.getByRole('row', { name: `Store ${other.id}`, exact: true }).getByRole('button', { name: 'Manage', exact: true }).click()
  const otherPanel = app.getByRole('region', { name: `${data.name('other')} store browser profile`, exact: true })
  app.once('dialog', dialog => dialog.dismiss())
  await otherPanel.getByRole('button', { name: 'Delete profile', exact: true }).click()
  expect((await data.get<BrowserProfile>(`/browser-profiles/${direct.id}`)).id).toBe(direct.id)
  app.once('dialog', dialog => dialog.accept())
  await otherPanel.getByRole('button', { name: 'Delete profile', exact: true }).click()
  await expect(otherPanel.getByRole('button', { name: 'Add profile' })).toBeVisible()
  await app.goto(`/settings/stores?account_id=${account.id}`)
  await expect(app).toHaveURL(new RegExp(`store_id=${store.id}`))
  await accountPanel.getByRole('button', { name: 'Delete account', exact: true }).click()
  await expect(app.getByRole('dialog')).toContainText(`Browser profiles (${account.name})`)
  await app.getByRole('dialog').getByRole('button', { name: 'Cancel', exact: true }).click()
  await expect(accountPanel).toBeVisible()
  await accountPanel.getByRole('button', { name: 'Delete account', exact: true }).click()
  await app.getByRole('dialog').getByRole('button', { name: 'Delete', exact: true }).click()
  await expect(accountPanel).toHaveCount(0)
  const deleted = await data.request.get(`/api/browser-profiles/${sub.id}`, { headers: data.headers })
  expect(deleted.status()).toBe(404)
})

test('mobile navigation and settings picker fit phone width', async ({ app, data }) => {
  await app.setViewportSize({ width: 390, height: 844 })
  await app.getByRole('button', { name: 'Open navigation menu', exact: true }).click()
  const mobileNav = app.getByRole('navigation', { name: 'Mobile navigation', exact: true })
  await expect(mobileNav.getByRole('link')).toHaveText(['Orders', 'Duplicate audit', 'Receiving', 'Payments', 'Log', 'Settings'])
  await mobileNav.getByRole('link', { name: 'Settings', exact: true }).click()
  await app.getByLabel('Settings section', { exact: true }).selectOption('/settings/stores')
  await expect(app.getByRole('heading', { name: 'Stores', exact: true })).toBeVisible()
  await expect(app.getByRole('navigation', { name: 'Settings sections', exact: true })).toBeHidden()
  expect(await app.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  await app.reload()
  await expect(app.getByLabel('Settings section', { exact: true })).toHaveValue('/settings/stores')
  const store = await data.create('/stores', { name: data.name('mobile-profile') })
  await data.create('/browser-profiles', { store_id: store.id, retailer: 'costco' })
  await app.goto(`/settings/stores?store_id=${store.id}`)
  const profilePanel = app.getByRole('region', { name: `${data.name('mobile-profile')} store browser profile`, exact: true })
  await expect(profilePanel.getByRole('button', { name: 'Log in', exact: true })).toBeVisible()
  expect(await app.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  await app.getByRole('button', { name: /Switch to (dark|light) mode/ }).click()
  await expect(profilePanel.getByRole('button', { name: 'Move profile', exact: true })).toBeVisible()
})
