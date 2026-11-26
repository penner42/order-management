import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { api } from '../api/client'
import {
  addressBlock, addressKey, addressVariantMethods, checkoutAddress, checkoutFields, generateAddressVariants,
  type CheckoutAddress, type SavedVariant, type SavedVariantDetails, type ShippingAddress, type ShippingAddressDetails,
} from '../utils/addressVariants'
import { ConfirmDialog } from './ConfirmDialog'

const emptyAddress: ShippingAddressDetails = {
  label: '', recipient: '', attention: '', address_line1: '', address_line2: '',
  city: '', state: '', postal_code: '', alternate_city: '', zip_extension: '',
}
const inputClass = 'block w-full rounded border border-brand-200 dark:border-gray-600 dark:bg-gray-800 px-2 py-1.5'
const buttonClass = 'rounded border border-brand-200 dark:border-gray-600 px-3 py-1.5 text-sm hover:bg-brand-50 dark:hover:bg-gray-700 disabled:opacity-50'
const linkClass = 'text-sm text-brand-600 dark:text-brand-400 hover:underline disabled:opacity-50'
const cardClass = 'rounded-lg border border-brand-200 dark:border-gray-600 p-3'

function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback
}

function AddressForm<T extends SavedVariantDetails>({
  initial, submitLabel, saving, onSubmit, onCancel,
}: {
  initial: T
  submitLabel: string
  saving: boolean
  onSubmit: (value: T) => Promise<void>
  onCancel: () => void
}) {
  const [draft, setDraft] = useState(initial)
  function submit(event: FormEvent) {
    event.preventDefault()
    void onSubmit({ ...draft, state: draft.state.toUpperCase() })
  }
  return (
    <form onSubmit={submit} className={cardClass}>
      <fieldset disabled={saving} className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label className="text-sm">
          Label
          <input className={inputClass} required maxLength={200} value={draft.label}
            onChange={(event) => setDraft({ ...draft, label: event.target.value })} />
        </label>
        {checkoutFields.map(({ key, label }) => (
          <label key={key} className="text-sm">
            {label}
            <input className={inputClass} value={draft[key]}
              required={['address_line1', 'city', 'state', 'postal_code'].includes(key)}
              maxLength={key === 'state' ? 2 : key === 'postal_code' ? 10 : 200}
              placeholder={key === 'address_line2' ? 'Suite 205' : undefined}
              pattern={key === 'state' ? '[A-Za-z]{2}' : key === 'postal_code' ? '[0-9]{5}(-[0-9]{4})?' : undefined}
              onChange={(event) => setDraft({ ...draft, [key]: event.target.value })} />
          </label>
        ))}
        {'alternate_city' in draft && 'zip_extension' in draft && <>
          <label className="text-sm">
            Alternate mailing city
            <input className={inputClass} value={String(draft.alternate_city)} maxLength={200}
              onChange={(event) => setDraft({ ...draft, alternate_city: event.target.value })} />
          </label>
          <label className="text-sm">
            ZIP+4 extension
            <input className={inputClass} value={String(draft.zip_extension)} maxLength={4} pattern="[0-9]{4}" inputMode="numeric"
              onChange={(event) => setDraft({ ...draft, zip_extension: event.target.value })} />
          </label>
        </>}
        <div className="flex gap-2 sm:col-span-2">
          <button className={buttonClass} type="submit">{submitLabel}</button>
          <button className={linkClass} type="button" onClick={onCancel}>Cancel</button>
        </div>
      </fieldset>
    </form>
  )
}

function AddressCard({ title, address, onCopy, children }: {
  title: string
  address: CheckoutAddress
  onCopy: (text: string) => Promise<void>
  children?: ReactNode
}) {
  return (
    <article className={`${cardClass} min-w-0 space-y-2`}>
      <h5 className="font-medium break-words">{title}</h5>
      <p className="whitespace-pre-line break-words text-sm">{addressBlock(address)}</p>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <button type="button" className={linkClass} onClick={() => void onCopy(addressBlock(address))}>Copy full address</button>
        {children}
      </div>
      <details className="text-sm">
        <summary className="cursor-pointer">Copy individual fields</summary>
        <dl className="mt-2 space-y-2">
          {checkoutFields.filter(({ key }) => address[key]).map(({ key, label }) => (
            <div key={key} className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-2">
              <dt className="col-span-2 text-xs text-ink-muted">{label}</dt>
              <dd className="break-words">{address[key]}</dd>
              <dd><button type="button" className={linkClass} aria-label={`Copy ${label.toLowerCase()}`}
                onClick={() => void onCopy(address[key])}>Copy</button></dd>
            </div>
          ))}
        </dl>
      </details>
    </article>
  )
}

export default function BuyingGroupAddresses({ groupId }: { groupId: number }) {
  const path = `/buying-groups/${groupId}/addresses`
  const [addresses, setAddresses] = useState<ShippingAddress[]>([])
  const [selectedId, setSelectedId] = useState<number>()
  const [editing, setEditing] = useState<ShippingAddress | 'new'>()
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [loadAttempt, setLoadAttempt] = useState(0)
  const [saving, setSaving] = useState(false)
  const [confirmRemoval, setConfirmRemoval] = useState<ShippingAddress>()
  const [error, setError] = useState('')
  const selected = addresses.find((address) => address.id === selectedId) ?? addresses[0]

  useEffect(() => {
    let active = true
    api.get<ShippingAddress[]>(path)
      .then((result) => { if (active) setAddresses(result) })
      .catch((e) => { if (active) setLoadError(errorMessage(e, 'Could not load shipping addresses.')) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [path, loadAttempt])

  async function saveAddress(details: ShippingAddressDetails) {
    setSaving(true)
    setError('')
    try {
      const saved = editing && editing !== 'new'
        ? await api.put<ShippingAddress>(`${path}/${editing.id}`, details)
        : await api.post<ShippingAddress>(path, details)
      setAddresses((current) => current.some((address) => address.id === saved.id)
        ? current.map((address) => address.id === saved.id ? saved : address)
        : [...current, saved])
      setSelectedId(saved.id)
      setEditing(undefined)
    } catch (e) {
      setError(errorMessage(e, 'Could not save shipping address.'))
    } finally {
      setSaving(false)
    }
  }

  async function removeAddress(address: ShippingAddress) {
    setConfirmRemoval(undefined)
    setSaving(true)
    setError('')
    try {
      await api.delete(`${path}/${address.id}`)
      setAddresses((current) => current.filter((item) => item.id !== address.id))
    } catch (e) {
      setError(errorMessage(e, 'Could not remove shipping address.'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <section aria-labelledby="group-addresses" className="rounded-lg border border-brand-200 dark:border-gray-700 p-4 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id="group-addresses" className="font-semibold">Shipping addresses</h3>
        {!loading && !loadError && <button type="button" className={buttonClass} disabled={!!editing || saving}
          onClick={() => { setError(''); setEditing('new') }}>Add address</button>}
      </div>
      {loading && <p role="status">Loading shipping addresses…</p>}
      {loadError && <div>
        <p role="alert">{loadError}</p>
        <button type="button" className={linkClass} onClick={() => { setLoadError(''); setLoading(true); setLoadAttempt((n) => n + 1) }}>Retry</button>
      </div>}
      {error && <p role="alert" className="text-sm text-red-600 dark:text-red-400">{error}</p>}
      {!loading && !loadError && addresses.length === 0 && !editing && <p className="text-sm text-ink-muted">No shipping addresses yet.</p>}
      {addresses.length > 0 && <div className="flex flex-wrap items-end gap-2">
        <label className="text-sm">
          Shipping address
          <select className={inputClass} value={selected?.id} disabled={!!editing || saving}
            onChange={(event) => { setError(''); setSelectedId(Number(event.target.value)) }}>
            {addresses.map((address) => <option key={address.id} value={address.id}>{address.details.label}</option>)}
          </select>
        </label>
        {selected && <>
          <button type="button" className={buttonClass} disabled={!!editing || saving}
            onClick={() => { setError(''); setEditing(selected) }}>Edit address</button>
          <button type="button" className={linkClass} disabled={!!editing || saving}
            onClick={() => { setError(''); setConfirmRemoval(selected) }}>Remove address</button>
        </>}
      </div>}
      {editing && <AddressForm key={editing === 'new' ? 'new' : editing.id} initial={editing === 'new' ? emptyAddress : editing.details}
        saving={saving} submitLabel="Save address" onSubmit={saveAddress} onCancel={() => { setError(''); setEditing(undefined) }} />}
      {selected && !editing && <AddressGenerator key={selected.id} source={selected} path={`${path}/${selected.id}/variants`}
        onVariantsChange={(update) => setAddresses((current) => current.map((address) =>
          address.id === selected.id ? { ...address, variants: update(address.variants) } : address))} />}
      <ConfirmDialog open={!!confirmRemoval} message={`Remove ${confirmRemoval?.details.label ?? 'this address'} and its saved variants?`}
        confirmLabel="Remove address" danger onCancel={() => setConfirmRemoval(undefined)}
        onConfirm={() => confirmRemoval && void removeAddress(confirmRemoval)} />
    </section>
  )
}

const firstPageSize = 12

async function copyAddressText(value: string) {
  try {
    await navigator.clipboard.writeText(value)
  } catch {
    const focused = document.activeElement
    const field = document.createElement('textarea')
    field.value = value
    field.readOnly = true
    field.style.cssText = 'position:fixed;left:-9999px;top:0'
    document.body.append(field)
    field.select()
    try {
      if (!document.execCommand('copy')) throw new Error('Copy failed')
    } finally {
      field.remove()
      if (focused instanceof HTMLElement) focused.focus()
    }
  }
}

function AddressGenerator({ source, path, onVariantsChange }: {
  source: ShippingAddress
  path: string
  onVariantsChange: (update: (variants: SavedVariant[]) => SavedVariant[]) => void
}) {
  const [enabled, setEnabled] = useState(addressVariantMethods.map((method) => method.id))
  const [generated, setGenerated] = useState<ReturnType<typeof generateAddressVariants>>()
  const [visibleCount, setVisibleCount] = useState(firstPageSize)
  const [draft, setDraft] = useState<SavedVariantDetails>()
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const original = checkoutAddress(source.details)
  const savedKeys = new Set(source.variants.map(({ details }) => addressKey(details)))

  async function copy(value: string) {
    setError('')
    setMessage('')
    try {
      await copyAddressText(value)
      setMessage('Copied to clipboard.')
    } catch {
      setError('Clipboard access is unavailable. Select the address text to copy it.')
    }
  }

  async function saveVariant(details: SavedVariantDetails) {
    setSaving(true)
    setError('')
    setMessage('')
    try {
      const saved = await api.post<SavedVariant>(path, details)
      onVariantsChange((variants) => [...variants, saved])
      setMessage(`Saved ${saved.details.label}.`)
      setDraft(undefined)
    } catch (e) {
      setError(errorMessage(e, 'Could not save address variant.'))
    } finally {
      setSaving(false)
    }
  }

  async function removeVariant(variant: SavedVariant) {
    setSaving(true)
    setError('')
    setMessage('')
    try {
      await api.delete(`${path}/${variant.id}`)
      onVariantsChange((variants) => variants.filter(({ id }) => id !== variant.id))
      setMessage(`Removed ${variant.details.label}.`)
    } catch (e) {
      setError(errorMessage(e, 'Could not remove address variant.'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-4">
      <h4 className="font-semibold">Address jig generator</h4>
      <fieldset className="space-y-2">
        <legend className="text-sm font-medium">Formatting options</legend>
        <div className="flex flex-wrap gap-x-4 gap-y-2">
          {addressVariantMethods.map(({ id, label }) => <label key={id} className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={enabled.includes(id)} onChange={(event) => {
              setEnabled(event.target.checked ? [...enabled, id] : enabled.filter((option) => option !== id))
              setGenerated(undefined)
            }} />
            {label}
          </label>)}
        </div>
      </fieldset>
      <div className="flex flex-wrap gap-2">
        <button type="button" className={buttonClass} disabled={!enabled.length} onClick={() => {
          setVisibleCount(firstPageSize)
          setGenerated(generateAddressVariants(source.details, enabled))
        }}>Generate variants</button>
        <button type="button" className={buttonClass} disabled={saving || !!draft}
          onClick={() => { setError(''); setDraft({ ...original, label: '' }) }}>Add confirmed variant</button>
      </div>
      {message && <p role="status" className="text-sm">{message}</p>}
      {error && <p role="alert" className="text-sm text-red-600 dark:text-red-400">{error}</p>}
      {draft && <AddressForm initial={draft} saving={saving} submitLabel="Save confirmed variant"
        onSubmit={saveVariant} onCancel={() => { setError(''); setDraft(undefined) }} />}
      {generated?.length === 0 && <p role="status" className="text-sm">No distinct variants for these options.</p>}
      <div className="grid gap-3 sm:grid-cols-2">
        <AddressCard title="Original address" address={original} onCopy={copy} />
        {generated?.slice(0, visibleCount).map(({ address, changes }) => {
          const title = changes.join(' · ')
          const key = addressKey(address)
          return <AddressCard key={key} title={title} address={address} onCopy={copy}>
            <button type="button" className={linkClass} aria-label={`Save variant: ${title}`}
              disabled={saving || savedKeys.has(key) || !!draft}
              onClick={() => void saveVariant({ ...address, label: title })}>{savedKeys.has(key) ? 'Saved' : 'Save variant'}</button>
          </AddressCard>
        })}
      </div>
      {generated && generated.length > visibleCount && <button type="button" className={buttonClass}
        onClick={() => setVisibleCount((count) => count + firstPageSize)}>Show more variants ({generated.length - visibleCount} remaining)</button>}
      {source.variants.length > 0 && <div className="space-y-2">
        <h4 className="font-semibold">Saved variants</h4>
        <div className="grid gap-3 sm:grid-cols-2">
          {source.variants.map((variant) => <AddressCard key={variant.id} title={variant.details.label}
            address={variant.details} onCopy={copy}>
            <button type="button" className={linkClass} disabled={saving}
              aria-label={`Remove saved variant: ${variant.details.label}`}
              onClick={() => void removeVariant(variant)}>Remove saved variant</button>
          </AddressCard>)}
        </div>
      </div>}
    </div>
  )
}
