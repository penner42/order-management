import { readFile } from 'node:fs/promises'
import { expect, test } from '@playwright/test'

const retailerUrl = 'https://www.walmart.com/orders/123-456'
const popupPath = '/__extension-test/popup/popup.html'
const jobPath = '/import-review/job/test-job'

test.beforeEach(async ({ context }) => {
  await context.route('**/api/buying-groups/receiving-review/has-pending', route => route.fulfill({ json: false }))
})

test('extension confirms the suggested profile and opens review after the retailer tab moves on', async ({ context, page, baseURL }) => {
  const appBase = baseURL!
  await context.route('https://www.walmart.com/**', route => route.fulfill({
    contentType: 'text/html', body: '<title>Retailer order</title>',
  }))
  await context.route('**/api/auth/me', route => route.fulfill({
    json: { id: 42, username: 'test-user', role: 'user' },
  }))
  await context.route('**/api/browser-profiles', route => route.fulfill({ json: [
    { id: 8, retailer: 'walmart', store_id: 3, store_name: 'Walmart', status: 'ready' },
    { id: 9, retailer: 'walmart', store_id: 3, store_name: 'Walmart', status: 'ready',
      store_account_name: 'shopper@example.test' },
    { id: 10, retailer: 'costco', store_id: 4, store_name: 'Costco', status: 'ready' },
  ] }))
  await context.route('**/api/browser-profiles/9/import-order', async route => {
    expect(route.request().postDataJSON()).toEqual({ retailer: 'walmart', order_number: '123-456' })
    expect(route.request().headers().authorization).toBe('Bearer extension-test-token')
    await route.fulfill({ json: { job_id: 'test-job', profile_id: 9, status: 'queued' } })
  })
  let complete = false
  await context.route('**/api/browser-profiles/jobs/test-job', route => route.fulfill({
    json: complete
      ? { status: 'succeeded', token: 'review-test-token' }
      : { status: 'running', message: 'Capturing order 123-456…' },
  }))
  await context.route('**/api/integrations/stores/orders/bulk-session/*', route => route.fulfill({
    json: { orders: [], store_id: 3, store_account_id: 2 },
  }))
  await context.route('**/api/integrations/stores/orders/bulk-session', async route => {
    expect(route.request().postDataJSON().orders[0].externalOrder.id).toBe('123-456')
    await route.fulfill({ json: { token: 'original-import-token' } })
  })

  await context.route('**/__extension-test/**', async route => {
    const file = new URL(route.request().url()).pathname.replace('/__extension-test/', '')
    let body = await readFile(`/browser-extension/${file}`, 'utf8')
    if (file === 'popup/popup.html') body = body.replace(
      '<script src="popup.js"></script>',
      '<script src="../background.js"></script><script src="popup.js"></script>',
    )
    if (file === 'background.js') body = `(() => {\n${body}\n})();`
    await route.fulfill({ contentType: file.endsWith('.js') ? 'application/javascript' : 'text/html', body })
  })
  await context.exposeFunction('openExtensionTestTab', async (url: string) => {
    const tab = await context.newPage()
    await tab.goto(url)
  })
  await context.addInitScript(({ appBase, retailerUrl, popupPath }) => {
    if (location.origin !== new URL(appBase).origin) return
    localStorage.setItem('auth_token', 'test-user-token')
    if (location.pathname !== popupPath) return
    const onMessage: Array<(msg: unknown, sender: { url: string }, respond: (response: unknown) => void) => void> = []
    const tabData = {
      url: retailerUrl, orderNumber: '123-456', email: 'SHOPPER@example.test',
    }
    const storage: Record<string, unknown> = {
      orderManagerProdApiBaseUrl: appBase + '/api',
      orderManagerExtensionTokenProd: 'extension-test-token',
      walmartOrderDetail: { url: retailerUrl, payload: { order: { id: '123-456' } } },
    }
    const events = { addListener: () => {} }
    Object.defineProperty(window, 'chrome', { value: {
      runtime: {
        getURL: (path: string) => appBase + '/__extension-test/' + path,
        onConnect: events,
        onMessage: { addListener: (listener: (typeof onMessage)[number]) => onMessage.push(listener) },
        sendMessage: (msg: unknown, respond: (response: unknown) => void) => {
          onMessage.forEach(listener => listener(msg, { url: location.href }, respond))
        },
      },
      storage: { local: {
        get: (keys: string[] | string, respond: (rows: Record<string, unknown>) => void) =>
          respond(Object.fromEntries([keys].flat().map(key => [key, storage[key]]))),
      } },
      tabs: {
        onUpdated: events, onRemoved: events,
        query: (_: unknown, respond: (tabs: unknown[]) => void) => respond([{ id: 1, url: retailerUrl }]),
        sendMessage: (_id: number, _msg: unknown, respond: (data: unknown) => void) => respond(tabData),
        create: ({ url }: { url: string }, respond: () => void) => {
          void (window as unknown as { openExtensionTestTab: (url: string) => Promise<void> })
            .openExtensionTestTab(url).then(respond)
        },
      },
    }, configurable: true })
  }, { appBase, retailerUrl, popupPath })

  await page.goto(retailerUrl)
  const popup = await context.newPage()
  await popup.goto(popupPath)
  const select = popup.getByLabel('Automation browser profile')
  await expect(select).toHaveValue('9')
  await expect(select.locator('option')).toHaveCount(3)
  const [originalReview] = await Promise.all([
    context.waitForEvent('page'),
    popup.getByRole('button', { name: 'Import this order', exact: true }).click(),
  ])
  await expect(originalReview).toHaveURL(/\/import-review\/bulk\?token=original-import-token$/)

  const [progress] = await Promise.all([
    context.waitForEvent('page'),
    popup.getByRole('button', { name: 'Import with browser automation' }).click(),
  ])
  await expect(progress).toHaveURL(new RegExp(jobPath))
  await expect(progress.getByRole('status')).toHaveText('Capturing order 123-456…')
  await page.goto('https://www.walmart.com/orders')
  await progress.reload()
  await expect(progress.getByRole('status')).toHaveText('Capturing order 123-456…')
  complete = true
  await expect(progress).toHaveURL(/\/import-review\/bulk\?token=review-test-token$/)
})

test('progress survives app sign-in and gives a helpful message for an unavailable job', async ({ page }) => {
  await page.route('**/api/auth/login', route => route.fulfill({ json: { access_token: 'test-token' } }))
  await page.route('**/api/auth/me', route => route.fulfill({
    json: { id: 42, username: 'test-user', role: 'user' },
  }))
  await page.route('**/api/browser-profiles/jobs/test-job', route => route.fulfill({
    status: 404, json: { detail: 'Job not found.' },
  }))
  await page.goto(jobPath)
  await expect(page).toHaveURL(/\/login\?redirect=/)
  await page.getByLabel('Username').fill('test-user')
  await page.getByLabel('Password').fill('test-password')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page).toHaveURL(new RegExp(jobPath))
  await expect(page.getByRole('alert')).toContainText('Start a new import from the retailer order page.')
})
