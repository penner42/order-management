import { test, expect } from './fixtures'
import type { BrowserProfile } from '../../src/api/types'

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
