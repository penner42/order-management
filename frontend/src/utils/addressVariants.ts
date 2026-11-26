export const checkoutFields = [
  { key: 'recipient', label: 'Recipient or company' },
  { key: 'attention', label: 'Attention or routing instructions' },
  { key: 'address_line1', label: 'Address line 1' },
  { key: 'address_line2', label: 'Address line 2' },
  { key: 'city', label: 'City' },
  { key: 'state', label: 'State' },
  { key: 'postal_code', label: 'ZIP code' },
] as const

export type CheckoutAddress = Record<typeof checkoutFields[number]['key'], string>
export type ShippingAddressDetails = CheckoutAddress & {
  label: string
  alternate_city: string
  zip_extension: string
}
export type SavedVariantDetails = CheckoutAddress & { label: string }

export type SavedVariant = { id: number; details: SavedVariantDetails }
export type ShippingAddress = { id: number; details: ShippingAddressDetails; variants: SavedVariant[] }
export type GeneratedVariant = { address: CheckoutAddress; changes: string[] }

export function checkoutAddress(source: CheckoutAddress): CheckoutAddress {
  return {
    recipient: source.recipient,
    attention: source.attention,
    address_line1: source.address_line1,
    address_line2: source.address_line2,
    city: source.city,
    state: source.state,
    postal_code: source.postal_code,
  }
}

export function addressKey(address: CheckoutAddress): string {
  return JSON.stringify(checkoutFields.map(({ key }) => address[key]))
}

export function addressBlock(address: CheckoutAddress): string {
  return [address.attention, address.recipient, address.address_line1, address.address_line2,
    `${address.city}, ${address.state} ${address.postal_code}`].filter(Boolean).join('\n')
}

// Common forms from USPS Publication 28, Appendix C1.
const streetSuffixes = {
  Alley: 'Aly', Avenue: 'Ave', Boulevard: 'Blvd', Circle: 'Cir', Court: 'Ct',
  Cove: 'Cv', Crescent: 'Cres', Crossing: 'Xing', Drive: 'Dr', Expressway: 'Expy',
  Extension: 'Ext', Freeway: 'Fwy', Highway: 'Hwy', Lane: 'Ln', Loop: 'Loop',
  Parkway: 'Pkwy', Place: 'Pl', Plaza: 'Plz', Road: 'Rd', Square: 'Sq',
  Street: 'St', Terrace: 'Ter', Trail: 'Trl', Turnpike: 'Tpke', Way: 'Way',
}
const directionals = {
  North: 'N', South: 'S', East: 'E', West: 'W',
  Northeast: 'NE', Northwest: 'NW', Southeast: 'SE', Southwest: 'SW',
}
const unitDesignators = {
  Apartment: 'Apt', Building: 'Bldg', Department: 'Dept', Floor: 'Fl',
  Room: 'Rm', Suite: 'Ste', Unit: 'Unit',
}

function findForm(token: string, forms: Record<string, string>) {
  const bare = token.toLowerCase().replace(/\.$/, '')
  return Object.entries(forms).find(([full, short]) => [full.toLowerCase(), short.toLowerCase()].includes(bare))
}

function alternateForm(token: string, forms: Record<string, string>): string {
  const entry = findForm(token, forms)
  if (!entry) return token
  const [full, short] = entry
  if (full === short) return token
  return token.toLowerCase() === full.toLowerCase() ? short : full
}

function streetParts(line: string) {
  const tokens = line.split(/\s+/)
  const last = tokens.length - 1
  const suffix = findForm(tokens[last], streetSuffixes) ? last : last - 1
  const hasPostdirection = suffix === last - 1 && findForm(tokens[last], directionals)
  if (!/^\d+[A-Za-z]?(?:-\d+)?$/.test(tokens[0]) ||
      suffix < 2 || !findForm(tokens[suffix], streetSuffixes) ||
      (suffix !== last && !hasPostdirection)) return null
  return { tokens, suffix, last, hasPostdirection }
}

function replaceStreetTokens(line: string, tokens: string[]): string {
  let index = 0
  return line.replace(/\S+/g, () => tokens[index++])
}

function changeSuffix(line: string): string {
  const parts = streetParts(line)
  if (!parts) return line
  const { tokens, suffix } = parts
  tokens[suffix] = alternateForm(tokens[suffix], streetSuffixes)
  return replaceStreetTokens(line, tokens)
}

function changeDirectionals(line: string): string {
  const parts = streetParts(line)
  if (!parts) return line
  const { tokens, suffix, last, hasPostdirection } = parts
  if (suffix > 2 && !findForm(tokens[2], directionals)) {
    tokens[1] = alternateForm(tokens[1], directionals)
  }
  if (hasPostdirection) tokens[last] = alternateForm(tokens[last], directionals)
  return replaceStreetTokens(line, tokens)
}

function unitParts(line: string) {
  const match = /^(\S+)( +[A-Za-z0-9-]+)$/.exec(line)
  if (!match || !findForm(match[1], unitDesignators)) return null
  return { designator: match[1], identifier: match[2] }
}

function splitStreetAndUnit(line: string) {
  const match = /^(.*\S)( +)(\S+ +\S+)$/.exec(line)
  if (match && unitParts(match[3]) && streetParts(match[1])) {
    return { street: match[1], separator: match[2], unit: match[3] }
  }
  return { street: line, separator: '', unit: '' }
}

function formatStreet(address: CheckoutAddress, transform: (value: string) => string): CheckoutAddress {
  const { street, separator, unit } = splitStreetAndUnit(address.address_line1)
  return { ...address, address_line1: transform(street) + separator + unit }
}

function formatUnit(address: CheckoutAddress, transform: (value: string) => string): CheckoutAddress {
  const { street, separator, unit } = splitStreetAndUnit(address.address_line1)
  const parts = unitParts(unit || address.address_line2)
  if (!parts) return address
  const formatted = transform(parts.designator) + parts.identifier
  return unit
    ? { ...address, address_line1: street + separator + formatted }
    : { ...address, address_line2: formatted }
}

function uppercaseStreet(line: string): string {
  if (!streetParts(line)) return line
  return line.replace(/^(\S+\s+)(.+)$/, (_, number: string, name: string) => number + name.toUpperCase())
}

function removeAbbreviationPeriods(line: string): string {
  const parts = streetParts(line)
  if (!parts) return line
  const { tokens, suffix, last, hasPostdirection } = parts
  const prefix = suffix > 2 && findForm(tokens[1], directionals) && !findForm(tokens[2], directionals)
  const indices = [suffix, ...(hasPostdirection ? [last] : []), ...(prefix ? [1] : [])]
  for (const index of indices) tokens[index] = tokens[index].replace(/\.$/, '')
  return replaceStreetTokens(line, tokens)
}

type Method = {
  id: string
  label: string
  apply: (address: CheckoutAddress, source: ShippingAddressDetails) => CheckoutAddress
}

export const addressVariantMethods: Method[] = [
  { id: 'suffix', label: 'Street suffix', apply: (a) => formatStreet(a, changeSuffix) },
  { id: 'directional', label: 'Street direction', apply: (a) => formatStreet(a, changeDirectionals) },
  { id: 'unit', label: 'Unit label', apply: (a) => formatUnit(a, (unit) => alternateForm(unit, unitDesignators)) },
  { id: 'punctuation', label: 'Remove abbreviation periods', apply: (a) =>
    formatUnit(formatStreet(a, removeAbbreviationPeriods), (unit) => unit.replace(/\.$/, '')),
  },
  { id: 'city', label: 'Alternate mailing city', apply: (a, source) => ({
    ...a, city: source.alternate_city || a.city,
  }) },
  { id: 'zip', label: 'ZIP+4 or five-digit ZIP', apply: (a, source) => ({
    ...a, postal_code: a.postal_code.includes('-') ? a.postal_code.slice(0, 5)
      : source.zip_extension ? `${a.postal_code}-${source.zip_extension}` : a.postal_code,
  }) },
  { id: 'case', label: 'Uppercase street and city', apply: (a) => ({
    ...formatStreet(a, uppercaseStreet),
    city: a.city.toUpperCase(),
  }) },
  { id: 'placement', label: 'Change unit line placement', apply: (a) => {
    const { street, unit } = splitStreetAndUnit(a.address_line1)
    if (unit) return a.address_line2 ? a : { ...a, address_line1: street, address_line2: unit }
    return unitParts(a.address_line2)
      ? { ...a, address_line1: `${a.address_line1} ${a.address_line2}`, address_line2: '' }
      : a
  } },
]

export function generateAddressVariants(source: ShippingAddressDetails, enabled: string[]): GeneratedVariant[] {
  const original = checkoutAddress(source)
  const variants: GeneratedVariant[] = [{ address: original, changes: [] }]
  const seen = new Set([addressKey(original)])
  // Apply placement last so formatting never changes the unit identifier.
  for (const method of addressVariantMethods.filter((option) => enabled.includes(option.id))) {
    for (const previous of [...variants]) {
      const address = method.apply(previous.address, source)
      const key = addressKey(address)
      if (seen.has(key)) continue
      seen.add(key)
      variants.push({ address, changes: [...previous.changes, method.label] })
    }
  }
  return variants.slice(1).sort((a, b) => a.changes.length - b.changes.length)
}
