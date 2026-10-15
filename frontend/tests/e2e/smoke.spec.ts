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
  await app.getByRole('link', { name: 'Settings', exact: true }).click()
  await app.getByRole('link', { name: 'Stores', exact: true }).click()
  expect((await stores).ok()).toBeTruthy()
  await expect(app.getByRole('heading', { name: 'Stores', exact: true })).toBeVisible()
})

test('tracking proxy reaches the package tracking service for a signed-in user', async ({ request, data }) => {
  const response = await request.post('/tracking/link', {
    headers: data.headers,
    data: { tracking_numbers: ['1Z999AA10123456784'] },
  })
  expect(response.ok()).toBeTruthy()
  const body = await response.json()
  expect(body.results[0].carrier).toBe('UPS')
  expect(body.results[0].url).toContain('ups.com')
})

test('API documentation, tracking calls and store accounts reject anonymous and invalid tokens', async ({ request }) => {
  const paths = [
    '/api/store-accounts', '/tracking/', '/tracking/docs', '/tracking/redoc',
    '/tracking/openapi.json', '/tracking/docs/oauth2-redirect',
    '/tracking/ups-token', '/tracking/track?tracking_number=1Z999AA10123456784',
    ...['/docs', '/redoc', '/openapi.json', '/docs/oauth2-redirect'].map(path => `http://backend:8000${path}`),
    ...['/docs', '/redoc', '/openapi.json', '/ups-token', '/track?tracking_number=1Z999AA10123456784'].map(path => `http://package-tracking:8080${path}`),
  ]
  const credentialHeaders: Record<string, string>[] = [{}, { Authorization: 'Bearer invalid-token' }]
  for (const headers of credentialHeaders) {
    for (const path of paths) {
      const response = await request.get(path, { headers })
      expect(response.status(), path).toBe(401)
      expect(response.headers()['www-authenticate'], path).toBe('Bearer')
    }
    const response = await request.post('/tracking/link', {
      headers, data: { tracking_numbers: ['1Z999AA10123456784'] },
    })
    expect(response.status()).toBe(401)
  }
})

test('signed-in users can access protected documentation and store accounts', async ({ request, data }) => {
  for (const path of [
    '/api/store-accounts', '/tracking/docs', '/tracking/redoc', '/tracking/openapi.json',
    'http://backend:8000/docs', 'http://backend:8000/redoc', 'http://backend:8000/openapi.json',
  ]) {
    const response = await request.get(path, { headers: data.headers })
    expect(response.ok(), path).toBeTruthy()
    if (path.endsWith('/openapi.json')) {
      const schema = await response.json()
      expect(schema.openapi).toBeTruthy()
      if (path.startsWith('/tracking')) {
        for (const operation of [schema.paths['/link'].post, schema.paths['/track'].get, schema.paths['/ups-token'].get]) {
          expect(operation.security).toEqual([{ HTTPBearer: [] }])
        }
      }
    }
  }
})
