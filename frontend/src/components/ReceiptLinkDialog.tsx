import { useEffect, useState } from 'react'
import { api } from '../api/client'

interface Receipt {
  receipt_key?: string | null
  receipt_id: string | null
  tracking_number: string
  tracking_numbers?: string[]
  description: string | null
  quantity: number | null
}

interface Candidate {
  item_id: number
  order_id: number
  buying_group_id: number | null
  buying_group_name: string | null
  store_order_number: string | null
  store: string | null
  description: string | null
  quantity: number
  status: string
  purchase_date: string | null
  submitted_at: string | null
  scanned_at: string | null
  price_sold: string
  expected_amount: string
  receipt_id: string | null
  receipt_tracking_number: string | null
  payment_linked: boolean
  tracking_numbers: (string | null)[]
}

export default function ReceiptLinkDialog<T>({ receipt, groupId, onLinked, onClose }: {
  receipt: Receipt
  groupId: number
  onLinked: (session: T) => void
  onClose: () => void
}) {
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [includeReceived, setIncludeReceived] = useState(false)
  const [includeAllGroups, setIncludeAllGroups] = useState(false)
  const [refreshVersion, setRefreshVersion] = useState(0)
  const [result, setResult] = useState<{ items: Candidate[]; total: number; suggestions?: Candidate[] }>({ items: [], total: 0 })
  const [selected, setSelected] = useState<Candidate[]>([])
  const [tracking, setTracking] = useState(receipt.tracking_number)
  const [confirmFull, setConfirmFull] = useState(false)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let active = true
    setLoading(true)
    const timer = window.setTimeout(() => {
      api.get<typeof result>(
        `/buying-groups/receiving-review/candidates?buying_group_id=${groupId}&page=${page}&q=${encodeURIComponent(search)}&include_received=${includeReceived}&include_all_groups=${includeAllGroups}&receipt_key=${encodeURIComponent(receipt.receipt_key ?? '')}`
      ).then(data => { if (active) { setResult(data); setError(null) } })
        .catch(e => { if (active) setError(e instanceof Error ? e.message : 'Could not load order lines') })
        .finally(() => { if (active) setLoading(false) })
    }, 250)
    return () => { active = false; window.clearTimeout(timer) }
  }, [groupId, page, search, includeReceived, includeAllGroups, receipt.receipt_key, refreshVersion])

  const quantity = receipt.quantity != null && receipt.quantity > 0 ? receipt.quantity : null
  const selectedQuantity = selected.reduce((sum, item) => sum + item.quantity, 0)
  const wrongGroup = (item: Candidate) => item.buying_group_id !== groupId
  const conflict = (item: Candidate) => !!item.receipt_id?.trim() && item.receipt_id.trim() !== (receipt.receipt_id ?? '').trim()
  const invalid = !selected.length || loading || saving || (quantity != null && selectedQuantity !== quantity) ||
    (!quantity && !confirmFull) || selected.some(item => conflict(item) || wrongGroup(item)) || !receipt.receipt_key
  const inputClass = 'w-full rounded border border-brand-200 dark:border-gray-600 bg-white dark:bg-gray-700 px-3 py-2 text-sm'
  const date = (value: string | null) => value ? value.slice(0, 10) : '—'
  const money = (value: string) => value != null && Number.isFinite(Number(value))
    ? new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(Number(value)) : '—'
  const correction = (item: Candidate) => <span className="block text-xs mt-1">
    <a href={`/?order_id=${item.order_id}`} target="_blank" rel="noopener noreferrer" className="text-brand-600 underline">Open order to correct group</a>
    <span className="block">The buying group applies to the whole order. Review its other lines before changing it.</span>
  </span>

  const stage = async () => {
    if (invalid) return
    setSaving(true)
    setError(null)
    try {
      const next = await api.post<T>('/buying-groups/receiving-review/link', {
        buying_group_id: groupId, receipt_key: receipt.receipt_key, item_ids: selected.map(item => item.item_id),
        receipt_tracking_number: tracking, confirm_full_quantity: confirmFull,
      })
      onLinked(next)
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not link receipt')
    } finally { setSaving(false) }
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div role="dialog" aria-modal="true" aria-labelledby="receipt-link-title" className="bg-white dark:bg-gray-800 text-ink dark:text-gray-100 rounded-xl shadow-xl p-6 w-full max-w-3xl max-h-[90vh] overflow-y-auto">
        <h2 id="receipt-link-title" className="text-lg font-semibold mb-2">Link receipt to order items</h2>
        <p className="text-sm mb-3">Receipt {receipt.receipt_id || '—'} · {quantity ?? 'Unknown quantity'} × {receipt.description || 'Item'}</p>
        <label className="block text-sm mb-4">Receipt tracking number
          <select className={inputClass} value={tracking} onChange={e => setTracking(e.target.value)} disabled={saving}>
            {[...new Set([receipt.tracking_number, ...(receipt.tracking_numbers ?? [])])].filter(Boolean).map(tn => <option key={tn}>{tn}</option>)}
          </select>
        </label>
        <label className="block text-sm mb-3">Search order number, description, or receipt ID
          <input autoFocus className={inputClass} value={search} disabled={saving} onChange={e => { setSearch(e.target.value); setPage(1) }} />
        </label>
        <label className="flex items-center gap-2 text-sm mb-3">
          <input type="checkbox" checked={includeAllGroups} disabled={saving} onChange={e => {
            setIncludeAllGroups(e.target.checked); setPage(1)
          }} />
          Search all buying groups
        </label>
        <label className="flex items-center gap-2 text-sm mb-3">
          <input type="checkbox" checked={includeReceived} disabled={saving} onChange={e => {
            setIncludeReceived(e.target.checked); setPage(1)
          }} />
          Show lines with receipts
        </label>
        <button type="button" disabled={saving || loading} className="text-sm text-brand-600 underline mb-3" onClick={() => {
          setSelected([]); setConfirmFull(false); setPage(1); setLoading(true); setRefreshVersion(v => v + 1)
        }}>Refresh order lines</button>
        {!loading && !!result.suggestions?.length && <aside aria-label="Possible matches in other groups" className="rounded border border-amber-300 p-3 mb-3 text-sm">
          <p className="font-medium">Possible match in another group</p>
          <p>These lines share a tracking number with this receipt. Review the order before correcting its group.</p>
          <ul className="space-y-2 mt-2">{result.suggestions.map(item => <li key={item.item_id}>
            {item.store_order_number || `Order ${item.order_id}`} · {item.quantity} × {item.description || 'Item'}
            <span className="block">Buying group: {item.buying_group_name || 'Unassigned'}</span>
            <span className="block font-mono text-xs">Original tracking: {item.tracking_numbers.filter(Boolean).join(', ') || '—'}</span>
            <span className="block">Receipt: {item.receipt_id || '—'} · Receipt tracking: {item.receipt_tracking_number || '—'}{item.payment_linked ? ' · Linked to payment' : ''}</span>
            {correction(item)}
          </li>)}</ul>
        </aside>}
        <div aria-live="polite" className="text-sm mb-2">{loading ? 'Loading order lines…' : `${result.total} order lines ${includeAllGroups ? 'across all buying groups' : 'in this buying group'}`}</div>
        <div className="space-y-2 max-h-64 overflow-y-auto mb-3">
          {!loading && result.items.map(item => (
            <label key={item.item_id} className={`flex gap-3 rounded border p-3 ${selected.some(i => i.item_id === item.item_id) ? 'border-brand-600' : 'border-brand-200 dark:border-gray-600'}`}>
              <input type="checkbox" checked={selected.some(i => i.item_id === item.item_id)}
                disabled={saving || (!selected.some(i => i.item_id === item.item_id) &&
                  (wrongGroup(item) || conflict(item) || (quantity != null && selectedQuantity + item.quantity > quantity)))}
                onChange={e => {
                  setSelected(prev => e.target.checked ? [...prev, item] : prev.filter(i => i.item_id !== item.item_id))
                  setConfirmFull(false)
                }} />
              <span className="text-sm">
                <span className="font-medium">{item.store_order_number || `Order ${item.order_id}`} · {item.store || 'Store'} · {item.quantity} × {item.description || 'Item'}</span>
                <span className="block">Status: {item.status}{item.payment_linked ? ' · Linked to payment' : ''}</span>
                <span className="block">Buying group: {item.buying_group_name || 'Unassigned'}</span>
                {wrongGroup(item) && correction(item)}
                <span className="block">Expected payout: {money(item.expected_amount)} total · {money(item.price_sold)} per unit</span>
                <span className="block text-xs">Purchased: {date(item.purchase_date)} · Submitted: {date(item.submitted_at)} · Received: {date(item.scanned_at)}</span>
                <span className="block font-mono text-xs">Original tracking: {item.tracking_numbers.filter(Boolean).join(', ') || '—'}</span>
                <span className="block text-xs">Receipt: {item.receipt_id || '—'} · Receipt tracking: {item.receipt_tracking_number || '—'}</span>
                {conflict(item) && <span className="block text-xs text-amber-700">Already has a different receipt.</span>}
                {quantity != null && item.quantity > quantity - selectedQuantity && !selected.some(i => i.item_id === item.item_id) &&
                  <span className="block text-xs text-amber-700">Exceeds the remaining receipt quantity. Split this line manually on Orders first.</span>}
              </span>
            </label>
          ))}
        </div>
        <div className="flex items-center gap-3 text-sm mb-4">
          <button type="button" disabled={loading || saving || page === 1} onClick={() => setPage(p => p - 1)}>Previous</button>
          <span>Page {page} of {Math.max(1, Math.ceil(result.total / 25))}</span>
          <button type="button" disabled={loading || saving || page * 25 >= result.total} onClick={() => setPage(p => p + 1)}>Next</button>
        </div>
        <p aria-live="polite" className="text-sm mb-2">Selected {selectedQuantity}{quantity != null ? ` of ${quantity}` : ''} units across {selected.length} order items.</p>
        {selected.length > 0 && <ul className="text-sm mb-3">{selected.map(item => <li key={item.item_id}>
          {item.store_order_number || `Order ${item.order_id}`} · {item.quantity} × {item.description || 'Item'} · Expected payout: {money(item.expected_amount)}{' '}
          <button type="button" disabled={saving} className="text-brand-600" onClick={() => {
            setSelected(prev => prev.filter(i => i.item_id !== item.item_id)); setConfirmFull(false)
          }} aria-label={`Remove ${item.description || item.item_id}`}>Remove</button>
        </li>)}</ul>}
        {selected.length > 0 && !quantity && <label className="block text-sm mb-3"><input type="checkbox" checked={confirmFull} disabled={saving} onChange={e => setConfirmFull(e.target.checked)} /> Confirm this receipt covers all {selectedQuantity} units on the selected lines.</label>}
        <p className="text-sm mb-3">Select whole lines totaling the receipt quantity. Split larger lines manually on Orders first. Linked items apply and unlink together.</p>
        <p className="text-sm text-ink-muted mb-3">Next, review the scan date and payout in Receiving Review, then apply the receipt.</p>
        {error && <p role="alert" className="text-sm text-red-600 mb-3">{error}</p>}
        <div className="flex justify-end gap-3">
          <button type="button" onClick={onClose} disabled={saving} className="px-3 py-2 border rounded">Cancel</button>
          <button type="button" onClick={stage} disabled={invalid} className="px-3 py-2 rounded bg-brand-600 text-white disabled:opacity-50">{saving ? 'Saving…' : 'Add to review'}</button>
        </div>
      </div>
    </div>
  )
}
