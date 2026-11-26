import { test, expect } from './fixtures'

test('Walmart live view sends the measured viewport after the socket opens', async ({ app, data }) => {
  const storeName = data.name('store')
  const store = await data.create('/stores', { name: storeName })
  const account = await data.create(`/stores/${store.id}/accounts`, { name: data.name('account') })
  const profile = await data.create('/browser-profiles', {
    store_id: store.id, store_account_id: account.id, retailer: 'walmart',
  })

  await app.addInitScript(() => {
    window.WebSocket = new Proxy(window.WebSocket, {
      construct(NativeWebSocket, args) {
        if (!String(args[0]).includes('/api/browser-profiles/')) {
          return Reflect.construct(NativeWebSocket, args)
        }
        const events = new AbortController()
        const socket = {
          readyState: Number(NativeWebSocket.CONNECTING),
          onopen: null as (() => void) | null,
          onmessage: null as ((event: MessageEvent) => void) | null,
          send(raw: string) {
            const message = JSON.parse(raw)
            if (message.type === 'resize') {
              document.documentElement.dataset.liveResize = `${message.width}×${message.height}`
            }
          },
          close() { events.abort() },
        }
        window.addEventListener('release-live-socket', () => {
          socket.readyState = NativeWebSocket.OPEN
          socket.onopen?.()
        }, { signal: events.signal })
        window.addEventListener('live-frame', event => {
          socket.onmessage?.(new MessageEvent('message', { data: (event as CustomEvent<string>).detail }))
        }, { signal: events.signal })
        return socket
      },
    })
  })

  const loginUrl = 'https://www.walmart.com/'
  await app.route(`**/api/browser-profiles/${profile.id}/login/start`, route =>
    route.fulfill({ json: { login_url: loginUrl } })
  )
  await app.setViewportSize({ width: 2616, height: 1260 })
  await app.goto('/settings/stores')
  await app.getByRole('button', { name: storeName, exact: true }).click()
  const accountPanel = app.getByRole('listitem', { name: `Store account ${account.id}`, exact: true })
  await accountPanel.getByRole('button', { name: 'Log in', exact: true }).click()

  const pane = app.locator('canvas').locator('..')
  const expected = await pane.evaluate(async element => {
    // Let the first layout-driven measurement run while the socket is connecting.
    await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
    return {
      width: Math.min(2560, Math.floor(element.clientWidth / 16) * 16),
      height: Math.min(1440, Math.floor(element.clientHeight / 16) * 16),
    }
  })
  const html = app.locator('html')
  await expect(html).not.toHaveAttribute('data-live-resize')

  await app.evaluate(() => window.dispatchEvent(new Event('release-live-socket')))
  await expect(html).toHaveAttribute('data-live-resize', `${expected.width}×${expected.height}`)
  await app.evaluate(() => {
    const jpeg = document.createElement('canvas').toDataURL('image/jpeg').split(',')[1]
    window.dispatchEvent(new CustomEvent('live-frame', { detail: JSON.stringify({
      type: 'frame', data: jpeg, metadata: { viewportWidth: 1920, viewportHeight: 912 },
    }) }))
  })
  await expect(app.getByText('1920×912', { exact: true })).toBeVisible()
})
