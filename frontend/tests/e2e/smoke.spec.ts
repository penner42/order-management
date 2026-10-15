import { randomUUID } from 'node:crypto'
import { expect, test, signIn } from './fixtures'

const adminUsername = process.env.TEST_ADMIN_USERNAME ?? 'admin'
const adminPassword = process.env.TEST_ADMIN_PASSWORD ?? 'admin'

test('protected pages redirect to sign in', async ({ page }) => {
  await page.goto('/stores')
  await expect(page).toHaveURL(/\/login$/)
  await expect(page.getByLabel('Username')).toBeVisible()
})

test('invalid credentials show an error from the API', async ({ page }) => {
  await signIn(page, `missing-${randomUUID()}`, 'invalid-password')
  await expect(page.getByRole('alert')).toBeVisible()
  await expect(page).toHaveURL(/\/login$/)
})

test('admin can sign in and sign out through the frontend proxy', async ({ page }) => {
  await signIn(page, adminUsername, adminPassword)
  await expect(page.getByRole('heading', { name: 'Admin', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Users', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Sign out', exact: true }).click()
  await expect(page).toHaveURL(/\/login$/)
})

test('a regular user can load orders and stores from PostgreSQL', async ({ app }) => {
  const orders = app.waitForResponse(response => response.url().includes('/api/orders') && response.request().method() === 'GET')
  await app.reload()
  expect((await orders).ok()).toBeTruthy()
  await expect(app.getByRole('heading', { name: 'Orders', exact: true })).toBeVisible()
  const stores = app.waitForResponse(response => new URL(response.url()).pathname.replace(/\/$/, '') === '/api/stores')
  await app.getByRole('link', { name: 'Stores', exact: true }).click()
  expect((await stores).ok()).toBeTruthy()
  await expect(app.getByRole('heading', { name: 'Stores', exact: true })).toBeVisible()
})

test('tracking proxy reaches the package tracking service', async ({ request }) => {
  const response = await request.post('/tracking/link', {
    data: { tracking_numbers: ['1Z999AA10123456784'] },
  })
  expect(response.ok()).toBeTruthy()
  const body = await response.json()
  expect(body.results[0].carrier).toBe('UPS')
  expect(body.results[0].url).toContain('ups.com')
})
