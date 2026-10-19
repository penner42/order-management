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
  store_order_number: string | null
  store: string | null
  description: string | null
  quantity: number
  status: string
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
  const [result, setResult] = useState<{ items: Candidate[]; total: number }>({ items: [], total: 0 })
  const [selected, setSelected] = useState<Candidate | null>(null)
  const [tracking, setTracking] = useState(receipt.tracking_number)
  const [confirmFull, setConfirmFull] = useState(false)
  const [splitTarget, setSplitTarget] = useState<'kept' | 'split_off'>('kept')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let active = true
    setLoading(true)
    const timer = window.setTimeout(() => {
      api.get<{ items: Candidate[]; total: number }>(
        `/buying-groups/receiving-review/candidates?buying_group_id=${groupId}&page=${page}&q=${encodeURIComponent(search)}`
      ).then(data => { if (active) { setResult(data); setError(null) } })
        .catch(e => { if (active) setError(e instanceof Error ? e.message : 'Could not load order lines') })
        .finally(() => { if (active) setLoading(false) })
    }, 250)
    return () => { active = false; window.clearTimeout(timer) }
  }, [groupId, page, search])

  const quantity = receipt.quantity != null && receipt.quantity > 0 ? receipt.quantity : null
  const conflict = !!selected?.receipt_id?.trim() && selected.receipt_id.trim() !== (receipt.receipt_id ?? '').trim()
  const needsSplit = !!selected && quantity != null && quantity < selected.quantity
  const tooLarge = !!selected && quantity != null && quantity > selected.quantity
  const invalid = !selected || loading || saving || tooLarge || (!quantity && !confirmFull) ||
    (needsSplit && selected?.payment_linked) || (conflict && (!needsSplit || splitTarget === 'kept')) || !receipt.receipt_key
  const inputClass = 'w-full rounded border border-brand-200 dark:border-gray-600 bg-white dark:bg-gray-700 px-3 py-2 text-sm'

  const stage = async () => {
    if (invalid || !selected) return
    setSaving(true)
    setError(null)
    try {
      const next = await api.post<T>('/buying-groups/receiving-review/link', {
        buying_group_id: groupId, receipt_key: receipt.receipt_key, item_id: selected.item_id,
        receipt_tracking_number: tracking, confirm_full_quantity: confirmFull,
        ...(needsSplit ? { split_quantity: quantity, split_target: splitTarget } : {}),
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
        <h2 id="receipt-link-title" className="text-lg font-semibold mb-2">Link receipt to order item</h2>
        <p className="text-sm mb-3">Receipt {receipt.receipt_id || '—'} · {quantity ?? 'Unknown quantity'} × {receipt.description || 'Item'}</p>
        <label className="block text-sm mb-4">Receipt tracking number
          <select className={inputClass} value={tracking} onChange={e => setTracking(e.target.value)} disabled={saving}>
            {[...new Set([receipt.tracking_number, ...(receipt.tracking_numbers ?? [])])].filter(Boolean).map(tn => <option key={tn}>{tn}</option>)}
          </select>
        </label>
        <label className="block text-sm mb-3">Search order number, description, or receipt ID
          <input autoFocus className={inputClass} value={search} disabled={saving} onChange={e => { setSearch(e.target.value); setPage(1); setSelected(null) }} />
        </label>
        <div aria-live="polite" className="text-sm mb-2">{loading ? 'Loading order lines…' : `${result.total} order lines in this buying group`}</div>
        <div className="space-y-2 max-h-64 overflow-y-auto mb-3">
          {!loading && result.items.map(item => (
            <label key={item.item_id} className={`flex gap-3 rounded border p-3 cursor-pointer ${selected?.item_id === item.item_id ? 'border-brand-600' : 'border-brand-200 dark:border-gray-600'}`}>
              <input type="radio" name="receipt-destination" checked={selected?.item_id === item.item_id} disabled={saving}
                onChange={() => { setSelected(item); setConfirmFull(false); setSplitTarget(item.receipt_id && item.receipt_id !== receipt.receipt_id ? 'split_off' : 'kept') }} />
              <span className="text-sm">
                <span className="font-medium">{item.store_order_number || `Order ${item.order_id}`} · {item.store || 'Store'} · {item.quantity} × {item.description || 'Item'}</span>
                <span className="block">Status: {item.status}{item.payment_linked ? ' · Linked to payment' : ''}</span>
                <span className="block font-mono text-xs">Original tracking: {item.tracking_numbers.filter(Boolean).join(', ') || '—'}</span>
                <span className="block text-xs">Receipt: {item.receipt_id || '—'} · Receipt tracking: {item.receipt_tracking_number || '—'}</span>
              </span>
            </label>
          ))}
        </div>
        <div className="flex items-center gap-3 text-sm mb-4">
          <button type="button" disabled={loading || saving || page === 1} onClick={() => { setPage(p => p - 1); setSelected(null) }}>Previous</button>
          <span>Page {page} of {Math.max(1, Math.ceil(result.total / 25))}</span>
          <button type="button" disabled={loading || saving || page * 25 >= result.total} onClick={() => { setPage(p => p + 1); setSelected(null) }}>Next</button>
        </div>
        {selected && !quantity && <label className="block text-sm mb-3"><input type="checkbox" checked={confirmFull} onChange={e => setConfirmFull(e.target.checked)} /> Confirm this receipt covers all {selected.quantity} units on the selected line.</label>}
        {tooLarge && <p className="text-sm text-amber-700 mb-3">This receipt has more units than the selected line. Choose a line with matching quantity.</p>}
        {conflict && <p className="text-sm text-amber-700 mb-3">This line already has a different receipt. Split onto a new line, choose another line, or correct its receipt on Orders.</p>}
        {needsSplit && selected && (
          <div className="rounded border border-brand-200 dark:border-gray-600 p-3 mb-3 text-sm">
            <p>Explicitly split {selected.quantity} units into {quantity} for this receipt and {selected.quantity - quantity!} remaining. Both lines retain the original shipment tracking.</p>
            {selected.payment_linked ? <p className="text-amber-700">Payment-linked lines cannot be split. Update the payment on Payments first.</p> : (
              <label className="block mt-2">Assign receipt to
                <select className={inputClass} value={splitTarget} onChange={e => setSplitTarget(e.target.value as 'kept' | 'split_off')}>
                  <option value="kept" disabled={conflict}>Original line ({quantity} units)</option>
                  <option value="split_off">New line ({quantity} units)</option>
                </select>
              </label>
            )}
            <p className="mt-2">Splitting takes effect when you click below and remains if you later unlink the receipt.</p>
          </div>
        )}
        <p className="text-sm text-ink-muted mb-3">Next, review the scan date and payout in Receiving Review, then apply the receipt.</p>
        {error && <p role="alert" className="text-sm text-red-600 mb-3">{error}</p>}
        <div className="flex justify-end gap-3">
          <button type="button" onClick={onClose} disabled={saving} className="px-3 py-2 border rounded">Cancel</button>
          <button type="button" onClick={stage} disabled={invalid} className="px-3 py-2 rounded bg-brand-600 text-white disabled:opacity-50">{saving ? 'Saving…' : needsSplit ? 'Split and add to review' : 'Add to review'}</button>
        </div>
      </div>
    </div>
  )
}
