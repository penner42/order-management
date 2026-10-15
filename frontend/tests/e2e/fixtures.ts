import { randomUUID } from 'node:crypto'
import { test as base, expect, type APIRequestContext, type Page, type Response } from '@playwright/test'

type Entity = { id: number }
type Cleanup = { path: string; priority: number }
const priority: Record<string, number> = {
  payments: 100, items: 90, orders: 80, shipments: 70,
  'payment-methods': 60, stores: 40, rewards: 30, 'buying-groups': 20,
}

export async function signIn(page: Page, username: string, password: string) {
  await page.goto('/login')
  await page.getByLabel('Username').fill(username)
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
}

export async function createThroughUI(page: Page, path: string, action: () => Promise<void>): Promise<Entity> {
  const [response] = await Promise.all([
    page.waitForResponse(response => new URL(response.url()).pathname === `/api${path}` && response.request().method() === 'POST'),
    action(),
  ])
  expect(response.ok(), `Create ${path}: ${await response.text()}`).toBeTruthy()
  return response.json()
}

export class TestData {
  readonly prefix = `e2e-${randomUUID()}`
  private records = new Map<string, Cleanup>()
  private pending: Promise<void>[] = []
  constructor(readonly request: APIRequestContext, readonly headers: Record<string, string>) {}

  name(suffix: string) { return `${this.prefix}-${suffix}` }

  track(path: string, id: number) {
    const resource = path.split('/')[1]
    const fullPath = `${path}/${id}`
    this.records.set(fullPath, { path: fullPath, priority: path.includes('/accounts') ? 50 : priority[resource] ?? 0 })
  }

  async get<T>(path: string): Promise<T> {
    const response = await this.request.get(`/api${path}`, { headers: this.headers })
    expect(response.ok(), `GET ${path}: ${response.status()}`).toBeTruthy()
    return response.json()
  }

  async create<T extends Entity = Entity>(path: string, data: object): Promise<T> {
    const response = await this.request.post(`/api${path}`, { headers: this.headers, data })
    expect(response.ok(), `POST ${path}: ${await response.text()}`).toBeTruthy()
    const entity: T = await response.json()
    this.track(path, entity.id)
    return entity
  }

  observe = (response: Response) => {
    const path = new URL(response.url()).pathname.replace(/^\/api/, '').replace(/\/$/, '')
    if (response.request().method() !== 'POST' || !response.ok()) return
    if (path === '/integrations/stores/orders/apply') {
      this.pending.push(response.json().then((entity: { order_id: number }) => { this.track('/orders', entity.order_id) }))
      return
    }
    if (!/^\/(stores(?:\/\d+\/accounts)?|rewards|payment-methods|orders|items|payments|buying-groups)$/.test(path)) return
    this.pending.push(response.json().then((entity: Entity) => { this.track(path, entity.id) }))
  }

  async cleanup() {
    const failures: string[] = []
    for (const result of await Promise.allSettled(this.pending)) {
      if (result.status === 'rejected') failures.push(String(result.reason))
    }
    // Imports create shipments internally rather than through a browser POST.
    // Discover only links belonging entirely to the orders this test created.
    const itemIds = new Set<number>()
    for (const record of this.records.values()) {
      if (!/^\/orders\/\d+$/.test(record.path)) continue
      const response = await this.request.get(`/api${record.path}`, { headers: this.headers })
      if (response.status() === 404) continue
      expect(response.ok(), `Discover cleanup items: ${record.path}`).toBeTruthy()
      const order: { items: Entity[] } = await response.json()
      for (const item of order.items) itemIds.add(item.id)
    }
    if (itemIds.size) {
      const shipments = await this.get<Array<Entity & { shipment_items: Array<{ item_id: number }> }>>('/shipments')
      for (const shipment of shipments) {
        if (shipment.shipment_items.length && shipment.shipment_items.every(link => itemIds.has(link.item_id))) {
          this.track('/shipments', shipment.id)
        }
      }
    }
    // For equal priorities, children created after parents are removed first.
    const records = [...this.records.values()].reverse().sort((a, b) => b.priority - a.priority)
    for (const record of records) {
      try {
        const response = await this.request.delete(`/api${record.path}`, { headers: this.headers })
        if (!response.ok() && response.status() !== 404) failures.push(`DELETE ${record.path}: ${await response.text()}`)
        const check = await this.request.get(`/api${record.path}`, { headers: this.headers })
        if (check.status() !== 404) failures.push(`Record remains: ${record.path} (${check.status()})`)
      } catch (error) { failures.push(`${record.path}: ${String(error)}`) }
    }
    expect(failures, 'Test record cleanup failed').toEqual([])
  }
}

export const test = base.extend<{ data: TestData; app: Page }>({
  data: async ({ request, page }, use) => {
    const login = await request.post('/api/auth/login', {
      data: { username: process.env.TEST_ADMIN_USERNAME ?? 'admin', password: process.env.TEST_ADMIN_PASSWORD ?? 'admin' },
    })
    expect(login.ok()).toBeTruthy()
    const { access_token } = await login.json()
    const adminHeaders = { Authorization: `Bearer ${access_token}` }
    const username = `e2e-${randomUUID()}`
    const password = randomUUID()
    const created = await request.post('/api/admin/users', {
      headers: adminHeaders, data: { username, password, role: 'user' },
    })
    expect(created.ok()).toBeTruthy()
    const user: Entity = await created.json()
    try {
      const userLogin = await request.post('/api/auth/login', { data: { username, password } })
      expect(userLogin.ok()).toBeTruthy()
      const token: { access_token: string } = await userLogin.json()
      const data = new TestData(request, { Authorization: `Bearer ${token.access_token}` })
      page.on('response', data.observe)
      try {
        await page.goto('/login')
        await page.evaluate(token => localStorage.setItem('auth_token', token), token.access_token)
        await use(data)
      } finally {
        page.off('response', data.observe)
        await data.cleanup()
      }
    } finally {
      const deleted = await request.delete(`/api/admin/users/${user.id}`, { headers: adminHeaders })
      expect(deleted.ok(), 'Temporary user cleanup failed').toBeTruthy()
      const users: Entity[] = await (await request.get('/api/admin/users', { headers: adminHeaders })).json()
      expect(users.some(entry => entry.id === user.id), 'Temporary user remains').toBeFalsy()
    }
  },
  app: async ({ page, data }, use) => {
    // Depend on data so its authenticated session and cleanup registry are ready.
    void data
    await page.goto('/')
    await expect(page.getByRole('heading', { name: 'Orders', exact: true })).toBeVisible()
    await use(page)
  },
})

export { expect }
