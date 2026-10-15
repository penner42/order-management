import { randomUUID } from 'node:crypto'
import { expect, test, type Page } from '@playwright/test'

const adminUsername = process.env.TEST_ADMIN_USERNAME ?? 'admin'
const adminPassword = process.env.TEST_ADMIN_PASSWORD ?? 'admin'

async function signIn(page: Page, username: string, password: string) {
  await page.goto('/login')
  await page.getByLabel('Username').fill(username)
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
}

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

test('a regular user can load orders and stores from PostgreSQL', async ({ page, request }) => {
  const login = await request.post('/api/auth/login', {
    data: { username: adminUsername, password: adminPassword },
  })
  expect(login.ok()).toBeTruthy()
  const { access_token } = await login.json()
  const headers = { Authorization: `Bearer ${access_token}` }
  const username = `e2e-${randomUUID()}`
  const password = randomUUID()
  const created = await request.post('/api/admin/users', {
    headers, data: { username, password, role: 'user' },
  })
  expect(created.ok()).toBeTruthy()
  const user = await created.json()

  try {
    const ordersResponse = page.waitForResponse(r => r.url().includes('/api/orders') && r.request().method() === 'GET')
    await signIn(page, username, password)
    expect((await ordersResponse).ok()).toBeTruthy()
    await expect(page.getByRole('heading', { name: 'Orders', exact: true })).toBeVisible()
    const storesResponse = page.waitForResponse(r => new URL(r.url()).pathname.replace(/\/$/, '') === '/api/stores')
    await page.getByRole('link', { name: 'Stores', exact: true }).click()
    expect((await storesResponse).ok()).toBeTruthy()
    await expect(page.getByRole('heading', { name: 'Stores', exact: true })).toBeVisible()
  } finally {
    const deleted = await request.delete(`/api/admin/users/${user.id}`, { headers })
    expect(deleted.ok()).toBeTruthy()
  }
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
