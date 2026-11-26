import { test, expect, createThroughUI } from './fixtures'
import { addressKey, generateAddressVariants, type ShippingAddress } from '../../src/utils/addressVariants'

test('a buying group manages multiple addresses and saves variants per address', async ({ app, data }) => {
  const groupName = data.name('address-group')
  const group = await data.create('/buying-groups', { name: groupName })
  const other = await data.create('/buying-groups', { name: data.name('other-group') })
  const groupPage = `/settings/buying-groups/${group.id}`
  const path = `/buying-groups/${group.id}/addresses`
  const section = app.getByRole('region', { name: 'Shipping addresses' })
  const selector = section.getByRole('combobox', { name: 'Shipping address', exact: true })
  await app.addInitScript(() => {
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: async (value: string) => sessionStorage.setItem('copied-address', value) },
    })
  })

  await app.goto('/settings/buying-groups')
  await app.getByRole('listitem').filter({ hasText: groupName }).getByRole('button', { name: 'Edit' }).click()
  await expect(app).toHaveURL(groupPage)
  await expect(app.getByRole('heading', { name: groupName })).toBeVisible()
  await expect(section.getByText('No shipping addresses yet.')).toBeVisible()

  const aliases = app.getByRole('region', { name: 'Import aliases' })
  await aliases.getByLabel('Alias for import matching').fill('Receiving Co')
  await aliases.getByRole('button', { name: 'Add', exact: true }).click()
  await expect(aliases.getByRole('button', { name: 'Remove alias Receiving Co' })).toBeVisible()

  const addAddress = async (label: string, street: string, unit = '') => {
    await section.getByRole('button', { name: 'Add address', exact: true }).click()
    await section.getByLabel('Label', { exact: true }).fill(label)
    await section.getByLabel('Recipient or company').fill('Receiving Co')
    await section.getByLabel('Attention or routing instructions').fill('Member aB-42')
    await section.getByLabel('Address line 1', { exact: true }).fill(street)
    await section.getByLabel('Address line 2', { exact: true }).fill(unit)
    await section.getByLabel('City', { exact: true }).fill('Cambridge')
    await section.getByLabel('State', { exact: true }).fill('MA')
    await section.getByLabel('ZIP code', { exact: true }).fill('02139')
    await section.getByLabel('ZIP+4 extension').fill('0007')
    return createThroughUI(app, path, () => section.getByRole('button', { name: 'Save address', exact: true }).click())
  }

  const depot = await addAddress('Depot', '123 North Main Street', 'Suite 00b')
  await expect(selector).toHaveValue(String(depot.id))
  const office = await addAddress('Office', '64 Oak Rd')
  await expect(selector).toHaveValue(String(office.id))
  await selector.selectOption({ label: 'Depot' })
  await section.getByRole('button', { name: 'Generate variants' }).click()
  const suffix = section.getByRole('article').filter({ has: app.getByRole('heading', { name: 'Street suffix', exact: true }) })
  await expect(suffix).toContainText('123 North Main St')
  await expect(suffix).toContainText('Suite 00b')
  await expect(suffix).toContainText('Member aB-42')
  await expect(section.getByRole('article').filter({ has: app.getByRole('heading', { name: 'ZIP+4 or five-digit ZIP', exact: true }) }))
    .toContainText('02139-0007')
  await suffix.getByRole('button', { name: 'Copy full address' }).click()
  await expect.poll(() => app.evaluate(() => sessionStorage.getItem('copied-address')))
    .toBe('Member aB-42\nReceiving Co\n123 North Main St\nSuite 00b\nCambridge, MA 02139')
  await suffix.getByText('Copy individual fields').click()
  await suffix.getByRole('button', { name: 'Copy zip code' }).click()
  await expect.poll(() => app.evaluate(() => sessionStorage.getItem('copied-address'))).toBe('02139')
  const [generated, copied] = await Promise.all([
    createThroughUI(app, `${path}/${depot.id}/variants`, () => suffix.getByRole('button', { name: 'Save variant: Street suffix' }).click()),
    section.getByRole('article').locator('p').allTextContents(),
  ])
  expect(new Set(copied).size).toBe(copied.length)
  await expect(suffix.getByRole('button', { name: 'Save variant: Street suffix' })).toBeDisabled()

  await section.getByRole('button', { name: 'Add confirmed variant' }).click()
  await section.getByLabel('Label', { exact: true }).fill('Dock delivery')
  await section.getByLabel('Address line 2', { exact: true }).fill('Dock 4')
  await createThroughUI(app, `${path}/${depot.id}/variants`, () => section.getByRole('button', { name: 'Save confirmed variant' }).click())
  await app.reload()
  await expect(aliases.getByRole('button', { name: 'Remove alias Receiving Co' })).toBeVisible()
  await expect(selector).toHaveValue(String(depot.id))
  await expect(section.getByRole('heading', { name: 'Dock delivery' })).toBeVisible()
  await selector.selectOption({ label: 'Office' })
  await expect(section.getByRole('heading', { name: 'Saved variants' })).toHaveCount(0)
  await section.getByRole('button', { name: 'Edit address' }).click()
  await section.getByLabel('Label', { exact: true }).fill('Main office')
  await section.getByRole('button', { name: 'Save address' }).click()
  await expect(selector.getByRole('option', { name: 'Main office' })).toHaveCount(1)

  const otherPath = `/buying-groups/${other.id}/addresses`
  expect(await data.get(otherPath)).toEqual([])
  const wrongGroup = await data.request.delete(`/api${otherPath}/${depot.id}/variants/${generated.id}`, { headers: data.headers })
  expect(wrongGroup.status()).toBe(404)
  const saved = await data.get<ShippingAddress[]>(path)
  expect(saved.map(({ details, variants }) => [details.label, variants.length])).toEqual([['Depot', 2], ['Main office', 0]])
  const duplicate = await data.request.post(`/api${path}/${depot.id}/variants`,
    { headers: data.headers, data: { ...saved[0].variants[0].details, label: 'Duplicate' } })
  expect(duplicate.status()).toBe(409)

  await selector.selectOption({ label: 'Depot' })
  await section.getByRole('button', { name: 'Remove saved variant: Dock delivery' }).click()
  await expect(section.getByRole('heading', { name: 'Dock delivery' })).toHaveCount(0)
  await section.getByRole('button', { name: 'Remove address', exact: true }).click()
  await app.getByRole('dialog').getByRole('button', { name: 'Remove address', exact: true }).click()
  await expect(selector.getByRole('option', { name: 'Depot' })).toHaveCount(0)
  expect((await data.get<ShippingAddress[]>(path)).map(({ id }) => id)).toEqual([office.id])
})

test('the generator preserves street names, routing codes, inline unit identifiers and postal punctuation', () => {
  const source = {
    label: 'Mailroom', recipient: 'Company', attention: 'Member Ab-00',
    address_line1: '014a North St. Suite 00b', address_line2: '',
    city: 'Cambridge', state: 'MA', postal_code: '02139-0123', alternate_city: '', zip_extension: '',
  }
  const variants = generateAddressVariants(source, ['suffix', 'directional', 'unit', 'punctuation', 'case', 'placement'])
  expect(variants.length).toBeGreaterThan(4)
  expect(new Set(variants.map(({ address }) => addressKey(address))).size).toBe(variants.length)
  expect(variants.every(({ address }) => address.address_line1.startsWith('014a ') &&
    !address.address_line1.startsWith('014a N ') &&
    `${address.address_line1} ${address.address_line2}`.includes('00b') &&
    address.attention === 'Member Ab-00' && address.postal_code === '02139-0123')).toBeTruthy()
  expect(variants.some(({ address }) => address.address_line1 === '014a North St.' && address.address_line2 === 'Suite 00b')).toBeTruthy()
  expect(generateAddressVariants({ ...source, address_line1: 'PO Box 7', address_line2: 'Member aB-42' },
    ['suffix', 'directional', 'unit', 'placement'])).toEqual([])
})
