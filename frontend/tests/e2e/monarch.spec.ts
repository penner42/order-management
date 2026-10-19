import { expect, test } from './fixtures'
import type { MonarchSettings, MonarchReviewData } from '../../src/api/monarch'

function settings(): MonarchSettings {
  return {
    profile: { id: 999999, integration: 'monarch', store_id: null, store_account_id: null, retailer: 'monarch', status: 'ready', last_error: null,
      last_import_at: null, full_check_enabled: false, full_check_cron: '0 0 * * *', full_check_max_pages: 3, full_check_last_run_at: null,
      unshipped_check_enabled: false, unshipped_check_cron: '0 */6 * * *', unshipped_check_last_run_at: null },
    accounts: [{ id: 'a', name: 'Receiving checking', type: 'depository' }, { id: 'b', name: 'Investment cash', type: 'brokerage' }],
    selected_account_ids: ['a'], sender_rules: { '42': ['Sender'] }, daily_enabled: false, next_check_at: null, last_check_at: null, last_error: null,
  }
}

test('Monarch settings saves accounts and keywords, logs in, and checks a backfill', async ({ app }) => {
  let state = settings()
  let saved: unknown = null
  let checked: unknown = null
  await app.route('**/api/buying-groups', route => route.fulfill({ json: [{ id: 42, name: 'Example group' }] }))
  await app.route('**/api/integrations/monarch**', async route => {
    const path = new URL(route.request().url()).pathname
    const method = route.request().method()
    if (path.endsWith('/checks')) {
      checked = route.request().postDataJSON()
      await route.fulfill({ json: { id: 'check-1', profile_id: 999999, kind: 'monarch_check', status: 'queued' } })
    } else if (path.includes('/jobs/')) {
      state = { ...state, last_check_at: '2026-10-19T16:00:00Z' }
      await route.fulfill({ json: { id: 'check-1', profile_id: 999999, kind: 'monarch_check', status: 'succeeded', message: 'Checked 2 transactions' } })
    } else if (method === 'PATCH') {
      saved = route.request().postDataJSON()
      state = { ...state, ...route.request().postDataJSON() }
      await route.fulfill({ json: state })
    } else await route.fulfill({ json: state })
  })
  await app.route('**/api/browser-profiles/999999/login/*', async route => {
    const done = route.request().url().endsWith('/done')
    state = { ...state, profile: { ...state.profile!, status: done ? 'ready' : 'login_in_progress' } }
    await route.fulfill({ json: done ? state.profile : { profile_id: 999999, login_url: 'https://app.monarch.com' } })
  })
  let disconnectLive: (() => void) | undefined
  await app.routeWebSocket(url => url.pathname === '/api/browser-profiles/999999/live', socket => {
    socket.onMessage(() => {})
    socket.send(JSON.stringify({ type: 'status', url: 'https://app.monarch.com' }))
    disconnectLive = () => socket.close({ code: 1012, reason: 'Server restarting' })
  })
  await app.goto('/settings/monarch')
  await expect(app.getByRole('heading', { name: 'Monarch', exact: true })).toBeVisible()
  const accountsToggle = app.locator('summary').filter({ hasText: 'Cash and investment accounts' })
  await expect(accountsToggle).toHaveText('Cash and investment accounts (1 selected of 2)')
  await accountsToggle.click()
  await expect(app.getByLabel('Receiving checking')).toBeHidden()
  await expect(app.getByRole('textbox', { name: 'Example group', exact: true })).toBeVisible()
  await accountsToggle.click()
  await app.getByLabel('Investment cash').check()
  await expect(accountsToggle).toHaveText('Cash and investment accounts (2 selected of 2)')
  await app.getByRole('textbox', { name: 'Example group', exact: true }).fill('Sender\nACH sender')
  await app.getByLabel('Check daily at 8:00 a.m. America/Los_Angeles').check()
  await app.getByRole('button', { name: 'Save Monarch settings' }).click()
  await expect(app.getByRole('status')).toHaveText('Settings saved')
  expect(saved).toEqual({ selected_account_ids: ['a', 'b'], sender_rules: { '42': ['Sender', 'ACH sender'] }, daily_enabled: true })
  await app.getByRole('button', { name: 'Log in to Monarch' }).click()
  await expect(app.getByLabel('Address bar')).toHaveValue('https://app.monarch.com')
  await expect(app.getByLabel('Address bar')).toBeEnabled()
  disconnectLive?.()
  await expect(app.getByText(/Live browser connection closed/)).toBeVisible()
  await expect(app.getByLabel('Address bar')).toBeDisabled()
  await app.getByRole('button', { name: 'Done', exact: true }).click()
  await expect(app.getByRole('status')).toContainText('Connected')
  await app.getByLabel('Start date').fill('2026-01-01')
  await app.getByLabel('End date').fill('2026-10-19')
  await app.getByRole('button', { name: 'Check now', exact: true }).click()
  await expect(app.getByRole('status')).toHaveText('Checked 2 transactions')
  expect(checked).toEqual({ start_date: '2026-01-01', end_date: '2026-10-19' })
})

test('Monarch review requires exact whole payments, confirms combined deposits, and undoes', async ({ app }) => {
  const choices = [
    { id: 1, reference: 'P1', buying_group_id: 42, buying_group_name: 'Example group', amount: '10.01', fingerprint: 'first', date: '2026-10-01' },
    { id: 2, reference: 'P2', buying_group_id: 43, buying_group_name: 'Other group', amount: '19.99', fingerprint: 'second', date: '2026-10-02' },
  ]
  let state: MonarchReviewData = { payments: choices, deposits: [{ id: 3, transaction_id: 'transaction', account_name: 'Bank', posted_date: '2026-10-10', amount: '30.00', merchant: 'Sender', description: 'ACH sender', revision: 1,
    pending: false, dismissed: false, eligible: true, confirmation_id: null, confirmation: null, evidence_changed: false,
    suggestions: [{ payment_ids: [1, 2], reason: 'Sender keyword, exact total, and within 30 days' }], search_truncated: false }] }
  let confirmed: unknown = null
  await app.route('**/api/integrations/monarch/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/confirm')) {
      confirmed = route.request().postDataJSON()
      state = { payments: [], deposits: [{ ...state.deposits[0], eligible: false, confirmation_id: 5,
        confirmation: { deposit: { amount: '30.00', posted_date: '2026-10-10' }, payments: choices } }] }
      await route.fulfill({ json: { confirmation_id: 5 } })
    } else if (path.endsWith('/undo')) {
      state = { payments: choices, deposits: [{ ...state.deposits[0], eligible: true, confirmation_id: null, confirmation: null }] }
      await route.fulfill({ json: { undone: true } })
    } else await route.fulfill({ json: state })
  })
  await app.goto('/payments')
  await app.getByRole('link', { name: 'Monarch review', exact: true }).click()
  await expect(app.getByRole('button', { name: 'Confirm receipt', exact: true })).toBeDisabled()
  await app.getByText('Select payments manually', { exact: true }).click()
  await app.getByLabel(/Example group · P1/).check()
  await expect(app.getByText('Selected total: $10.01 · Difference: $19.99')).toBeVisible()
  await expect(app.getByRole('button', { name: 'Confirm receipt', exact: true })).toBeDisabled()
  await app.getByLabel(/Other group · P2/).check()
  await expect(app.getByRole('button', { name: 'Confirm receipt', exact: true })).toBeEnabled()
  await app.getByRole('button', { name: 'Confirm receipt', exact: true }).click()
  await expect(app.getByText('No deposits in this view.')).toBeVisible()
  expect(confirmed).toEqual({ revision: 1, payments: [{ id: 1, fingerprint: 'first' }, { id: 2, fingerprint: 'second' }] })
  await app.getByRole('combobox', { name: 'Show', exact: true }).selectOption('confirmed')
  await expect(app.getByText(/Confirmed payments: Example group P1/)).toBeVisible()
  await app.getByRole('button', { name: 'Undo confirmation' }).click()
  await app.getByRole('combobox', { name: 'Show', exact: true }).selectOption('review')
  await app.getByRole('button', { name: /Choose Example group/ }).click()
  await expect(app.getByRole('button', { name: 'Confirm receipt', exact: true })).toBeEnabled()
})

test('Monarch review blocks pending deposits', async ({ app }) => {
  await app.route('**/api/integrations/monarch/review', route => route.fulfill({ json: {
    payments: [], deposits: [{ id: 3, transaction_id: 'pending', account_name: 'Bank', posted_date: '2026-10-10', amount: '30.00', merchant: 'Pending sender', description: 'ACH', revision: 1, pending: true, dismissed: false, eligible: false, confirmation_id: null, confirmation: null, evidence_changed: false, suggestions: [], search_truncated: false }],
  } }))
  await app.goto('/payments/monarch')
  await expect(app.getByText(/Pending or no longer present/)).toBeVisible()
  await expect(app.getByRole('button', { name: 'Confirm receipt', exact: true })).toHaveCount(0)
})
