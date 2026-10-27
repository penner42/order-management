import { test, expect } from '@playwright/test'
import { signIn } from './fixtures'

test('admin database transfer exposes export and confirms an import before uploading', async ({ page }) => {
  await page.route('**/api/admin/database-transfer', route => route.fulfill({ json: { import_enabled: true } }))
  let uploads = 0
  await page.route('**/api/admin/import', route => {
    uploads += 1
    expect(route.request().headers()['content-type']).toContain('multipart/form-data')
    return route.fulfill({ status: 400, json: { detail: 'Invalid database export. Existing data was preserved.' } })
  })
  await signIn(page, process.env.TEST_ADMIN_USERNAME ?? 'admin', process.env.TEST_ADMIN_PASSWORD ?? 'admin')
  await expect(page).toHaveURL(/\/admin$/)
  await expect(page.getByRole('button', { name: 'Export database', exact: true })).toBeVisible()
  const importButton = page.getByRole('button', { name: 'Import database', exact: true })
  await expect(importButton).toBeDisabled()
  await page.getByLabel('Database export file').setInputFiles({
    name: 'production.json', mimeType: 'application/json', buffer: Buffer.from('{}'),
  })
  await importButton.click()
  await expect(page.getByRole('dialog')).toContainText('production.json')
  await page.getByRole('button', { name: 'Cancel', exact: true }).click()
  expect(uploads).toBe(0)
  await importButton.click()
  await page.getByRole('button', { name: 'Replace database', exact: true }).click()
  await expect(page.getByText('Invalid database export. Existing data was preserved.', { exact: true })).toBeVisible()
  expect(uploads).toBe(1)
})

test('admin import is hidden when the environment disables it', async ({ page }) => {
  await page.route('**/api/admin/database-transfer', route => route.fulfill({ json: { import_enabled: false } }))
  await signIn(page, process.env.TEST_ADMIN_USERNAME ?? 'admin', process.env.TEST_ADMIN_PASSWORD ?? 'admin')
  await expect(page).toHaveURL(/\/admin$/)
  await expect(page.getByRole('button', { name: 'Export database', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Import database', exact: true })).toHaveCount(0)
})
