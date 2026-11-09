import { test, expect } from './fixtures'

test('extension download requests the displayed release and build', async ({ app }) => {
  const filename = 'order_manager_browser_integration-9.8.7.xpi'
  const fingerprint = 'current-release-source'
  await app.route('**/api/browser-extension', route => route.fulfill({
    json: {
      status: 'ready', error: null, available: true,
      meta: {
        version: '9.8.7', fingerprint, built_at: '2026-11-09T18:00:00Z',
        chrome: null, firefox: { filename, size_bytes: 32, updated_at: '2026-11-09T18:00:00Z' },
      },
    },
  }))
  let requestedUrl = ''
  await app.route('**/api/browser-extension/download/firefox?**', route => {
    requestedUrl = route.request().url()
    return route.fulfill({
      contentType: 'application/x-xpinstall',
      headers: { 'Cache-Control': 'no-store' },
      body: 'current signed package fixture',
    })
  })
  await app.goto('/settings/extension')
  const downloaded = app.waitForEvent('download')
  await app.getByRole('button', { name: 'Download for Firefox', exact: true }).click()
  const download = await downloaded
  const query = new URL(requestedUrl).searchParams
  expect(query.get('filename')).toBe(filename)
  expect(query.get('build')).toBe(fingerprint)
  expect(download.suggestedFilename()).toBe(filename)
  const stream = await download.createReadStream()
  const chunks: Buffer[] = []
  for await (const chunk of stream) chunks.push(Buffer.from(chunk))
  expect(Buffer.concat(chunks).toString()).toBe('current signed package fixture')
})
