import { test, expect } from './fixtures'
import type { BrowserProfile } from '../../src/api/types'

for (const accountProfile of [false, true]) {
  for (const autoApply of [false, true]) {
    test(`manual import ${autoApply ? 'skips' : 'saves for'} review for a ${accountProfile ? 'store account' : 'store'}`, async ({ app, data }) => {
      const store = await data.create('/stores', { name: data.name('import-choice') })
      const account = accountProfile
        ? await data.create(`/stores/${store.id}/accounts`, { name: data.name('account') })
        : null
      const profile = await data.create<BrowserProfile>('/browser-profiles', {
        store_id: store.id, store_account_id: account?.id ?? null, retailer: 'amazon',
      })
      const requests: unknown[] = []
      await app.route(`**/browser-profiles/${profile.id}/import`, async route => {
        requests.push(route.request().postDataJSON())
        await route.fulfill({ status: 200, json: { job_id: 'test-import-choice' } })
      })
      await app.goto(`/settings/stores?store_id=${store.id}`)
      const panel = account
        ? app.getByRole('listitem', { name: `Store account ${account.id}`, exact: true })
        : app.getByRole('region', { name: `${data.name('import-choice')} store browser profile`, exact: true })
      await panel.getByLabel('Pages', { exact: true }).fill('7')
      const importButton = panel.getByRole('button', { name: 'Import now', exact: true })
      const dialog = app.getByRole('dialog', { name: 'Import now', exact: true })
      await importButton.click()
      await expect(dialog).toBeVisible()
      expect(requests).toHaveLength(0)
      await dialog.getByRole('button', { name: 'Cancel', exact: true }).click()
      await expect(dialog).not.toBeVisible()
      await importButton.click()
      await app.keyboard.press('Escape')
      await expect(dialog).not.toBeVisible()
      expect(requests).toHaveLength(0)
      await importButton.click()
      await dialog.getByRole('button', { name: autoApply ? 'Skip review' : 'Save for review', exact: true }).click()
      await expect(dialog).not.toBeVisible()
      await expect.poll(() => requests).toEqual([{ mode: 'full', max_pages: 7, auto_apply: autoApply }])
    })
  }
}

test('store checks have separate toggles, daily full checks and staggered minute intervals', async ({ app, data }) => {
  const store = await data.create('/stores', { name: data.name('store-schedules') })
  const profile = await data.create<BrowserProfile>('/browser-profiles', { store_id: store.id, retailer: 'amazon' })
  await app.goto(`/settings/stores?store_id=${store.id}`)
  const panel = app.getByRole('region', { name: `${data.name('store-schedules')} store browser profile`, exact: true })
  await expect(panel.getByText('Once daily', { exact: true })).toBeVisible()
  await panel.getByRole('checkbox', { name: 'Full check', exact: true }).check()
  await panel.getByRole('checkbox', { name: 'Check unshipped', exact: true }).check()
  await panel.getByLabel('Every (minutes)', { exact: true }).fill('15')
  await panel.getByRole('button', { name: 'Save schedule', exact: true }).click()
  await expect(panel.getByRole('button', { name: 'Save schedule', exact: true })).toBeDisabled()
  const saved = await data.get<BrowserProfile>(`/browser-profiles/${profile.id}`)
  expect(saved).toMatchObject({ full_check_enabled: true, unshipped_check_enabled: true, unshipped_check_interval_minutes: 15 })
  expect(saved.full_check_next_run_at).toBeTruthy()
  expect(saved.unshipped_check_next_run_at).toBeTruthy()
  expect(saved.full_check_next_run_at).not.toBe(saved.unshipped_check_next_run_at)
  await app.reload()
  await expect(panel.getByLabel('Every (minutes)', { exact: true })).toHaveValue('15')
  await panel.getByRole('checkbox', { name: 'Full check', exact: true }).uncheck()
  await panel.getByRole('button', { name: 'Save schedule', exact: true }).click()
  await expect(panel.getByRole('button', { name: 'Save schedule', exact: true })).toBeDisabled()
  expect(await data.get<BrowserProfile>(`/browser-profiles/${profile.id}`)).toMatchObject({
    full_check_enabled: false, full_check_next_run_at: null, unshipped_check_enabled: true,
  })
  await panel.getByRole('checkbox', { name: 'Full check', exact: true }).check()
  await panel.getByRole('checkbox', { name: 'Check unshipped', exact: true }).uncheck()
  await panel.getByRole('button', { name: 'Save schedule', exact: true }).click()
  await expect(panel.getByRole('button', { name: 'Save schedule', exact: true })).toBeDisabled()
  expect(await data.get<BrowserProfile>(`/browser-profiles/${profile.id}`)).toMatchObject({
    full_check_enabled: true, unshipped_check_enabled: false, unshipped_check_next_run_at: null,
  })
})

test('resetting browser data requires confirmation and keeps the saved schedule', async ({ app, data }) => {
  const storeName = data.name('browser-reset')
  const store = await data.create('/stores', { name: storeName })
  const profile = await data.create<BrowserProfile>('/browser-profiles', { store_id: store.id, retailer: 'amazon' })
  const path = `/browser-profiles/${profile.id}`
  const scheduled = { full_check_enabled: true, full_check_max_pages: 7, unshipped_check_enabled: true, unshipped_check_interval_minutes: 15 }
  const save = await data.request.patch(`/api${path}`, { headers: data.headers, data: scheduled })
  expect(save.ok()).toBeTruthy()

  await app.goto(`/settings/stores?store_id=${store.id}`)
  const panel = app.getByRole('region', { name: `${storeName} store browser profile`, exact: true })
  const reset = panel.getByRole('button', { name: 'Reset browser data', exact: true })
  const dialog = app.getByRole('dialog', { name: /Clear all data for this browser profile/ })
  await reset.click()
  await expect(dialog).toBeVisible()
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click()
  await expect(dialog).toBeHidden()
  expect((await data.get<BrowserProfile>(path)).status).toBe('logged_out')

  await reset.click()
  await dialog.getByRole('button', { name: 'Reset browser data', exact: true }).click()
  await expect(panel.getByRole('status')).toHaveText('Browser data cleared. Log in again to resume scheduled checks.')
  await expect(panel.getByRole('button', { name: 'Log in', exact: true })).toBeEnabled()
  await expect(panel.getByRole('checkbox', { name: 'Full check', exact: true })).toBeChecked()
  await expect(panel.getByLabel('Every (minutes)', { exact: true })).toHaveValue('15')
  expect(await data.get<BrowserProfile>(path)).toMatchObject({ ...scheduled, status: 'login_required' })
})
