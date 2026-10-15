import { test, expect } from './fixtures'

test('development blocks group submissions while leaving receiving available', async ({ app, data }) => {
  const group = await data.create('/buying-groups', {
    name: data.name('group'), api_framework: 'bfmr',
    api_username: 'fixture-key', api_password: 'fixture-secret',
    tracking_submit_enabled: true,
  })
  const capabilities = await data.get<{ group_submissions_enabled: boolean }>('/capabilities')
  expect(capabilities.group_submissions_enabled).toBe(false)
  await app.goto('/settings/buying-groups')
  await expect(app.getByText('Group submissions are disabled in this environment. Lookups and receiving checks remain available.', { exact: true })).toBeVisible()
  const row = app.getByRole('listitem').filter({ hasText: data.name('group') })
  await expect(row.getByRole('button', { name: 'Submit trackings now', exact: true })).toBeDisabled()
  await expect(row.getByRole('button', { name: 'Check receiving', exact: true })).toBeEnabled()
  await row.getByRole('button', { name: 'Edit', exact: true }).click()
  await expect(app.getByRole('checkbox', { name: 'Auto-submit tracking', exact: true })).toBeDisabled()
  for (const path of [`/buying-groups/${group.id}/submit-trackings`, '/items/0/submit-tracking']) {
    const response = await data.request.post(`/api${path}`, { headers: data.headers, data: {} })
    expect(response.status()).toBe(403)
    expect((await response.json()).detail).toContain('Group submissions are disabled')
  }
})
