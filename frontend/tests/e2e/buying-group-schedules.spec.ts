import { test, expect } from './fixtures'

type Group = {
  id: number
  tracking_submit_enabled: boolean
  schedule_interval_minutes: number
  schedule_next_run_at: string | null
  received_check_enabled: boolean
}

test('each group has one interval and saving rebuilds the stagger', async ({ app, data }) => {
  const first = await data.create<Group>('/buying-groups', {
    name: data.name('first'), api_framework: 'bfmr', received_check_enabled: true,
    schedule_interval_minutes: 360,
  })
  const second = await data.create<Group>('/buying-groups', {
    name: data.name('second'), api_framework: 'bfmr', received_check_enabled: true,
    schedule_interval_minutes: 360,
  })
  const before = await data.get<Group>(`/buying-groups/${second.id}`)
  await app.goto('/settings/buying-groups')
  const row = app.getByRole('listitem').filter({ hasText: data.name('first') })
  await row.getByRole('button', { name: 'Edit', exact: true }).click()
  await expect(app.getByRole('checkbox', { name: 'Auto-check receiving', exact: true })).toBeChecked()
  await expect(app.getByRole('checkbox', { name: 'Auto-submit tracking', exact: true })).not.toBeChecked()
  await expect(app.getByRole('spinbutton')).toHaveCount(1)
  await app.getByLabel('Run every (minutes)').fill('180')
  await app.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(row.getByText('Every 180 minutes', { exact: false })).toBeVisible()
  const updated = await data.get<Group>(`/buying-groups/${first.id}`)
  const other = await data.get<Group>(`/buying-groups/${second.id}`)
  expect(updated.schedule_interval_minutes).toBe(180)
  expect(updated.tracking_submit_enabled).toBe(false)
  expect(updated.schedule_next_run_at).toBeTruthy()
  expect(updated.schedule_next_run_at).not.toBe(other.schedule_next_run_at)
  expect(other.schedule_next_run_at).not.toBe(before.schedule_next_run_at)

  await row.getByRole('button', { name: 'Edit', exact: true }).click()
  await app.getByRole('checkbox', { name: 'Auto-check receiving', exact: true }).uncheck()
  await app.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(row.getByText('Receiving: off', { exact: false })).toBeVisible()
  expect((await data.get<Group>(`/buying-groups/${first.id}`)).schedule_next_run_at).toBeNull()
})
