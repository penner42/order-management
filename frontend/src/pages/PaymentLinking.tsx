import { useEffect, useRef, useState } from 'react'
import { Link, useBlocker, useLocation, useParams } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import type { Payment } from '../api/types'
import { availableQuantity, cents, decimal, money, remaining, sum, validate } from '../utils/paymentLinking'
import type { Allocation, Workspace, WorkspaceItem } from '../utils/paymentLinking'

const button = 'rounded-lg border border-brand-200 dark:border-gray-600 px-3 py-2 text-sm hover:bg-brand-50 dark:hover:bg-gray-700 disabled:opacity-40 disabled:cursor-not-allowed'
const primary = `${button} bg-brand-100 dark:bg-gray-700 font-semibold`
const input = 'rounded-lg border border-brand-200 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm max-w-full'
const panel = 'rounded-xl border border-brand-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-4 space-y-3 min-w-0'
const date = (value: string | null) => value ? new Date(value).toLocaleDateString() : '—'
const snapshot = (allocations: Allocation[], historical: string, adjustment: string, note: string) => JSON.stringify({allocations, historical, adjustment, note})
const message = (e: unknown) => e instanceof Error ? e.message : 'Request failed'

export default function PaymentLinking() {
  const {paymentId} = useParams()
  return <WorkspaceEditor key={paymentId} paymentId={Number(paymentId)} />
}

function WorkspaceEditor({paymentId}: {paymentId: number}) {
  const location = useLocation()
  const [workspace, setWorkspace] = useState<Workspace | null>(null)
  const [rows, setRows] = useState<Allocation[]>([])
  const [unassigned, setUnassigned] = useState<NonNullable<Workspace['unassigned_links']>>([])
  const [assigningItem, setAssigningItem] = useState<number | null>(null)
  const [historical, setHistorical] = useState('0')
  const [adjustment, setAdjustment] = useState('0')
  const [note, setNote] = useState('')
  const [noteEdited, setNoteEdited] = useState(false)
  const [baseline, setBaseline] = useState('')
  const [selectedReceipt, setSelectedReceipt] = useState<number | null>(null)
  const [addedReceipts, setAddedReceipts] = useState<number[]>([])
  const [selectedItems, setSelectedItems] = useState<number[]>([])
  const [query, setQuery] = useState('')
  const [pending, setPending] = useState<Allocation[] | null>(null)
  const [editingKey, setEditingKey] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [stale, setStale] = useState(false)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [navigation, setNavigation] = useState<Payment[]>([])
  const mounted = useRef(true)
  const loadSequence = useRef(0)
  const pendingEditor = useRef<HTMLElement | null>(null)
  const dirty = !!workspace && (snapshot(rows, historical, adjustment, note) !== baseline || pending !== null || JSON.stringify(unassigned) !== JSON.stringify(workspace.unassigned_links ?? []))
  const blocker = useBlocker(dirty || busy)
  useEffect(() => {
    if (blocker.state === 'blocked') {
      if (!busy && window.confirm('Discard unsaved payment changes and leave?')) blocker.proceed()
      else blocker.reset()
    }
  }, [blocker, busy])
  useEffect(() => {
    if (!dirty && !busy) return
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty, busy])
  function apply(data: Workspace) {
    const h = data.payment.allocation_breakdown.historical_amount
    const a = data.payment.allocation_breakdown.adjustment_amount
    const n = data.payment.allocation_details.note ?? ''
    setWorkspace(data); setRows(data.allocations); setUnassigned(data.unassigned_links ?? []); setAssigningItem(null); setHistorical(h); setAdjustment(a); setNote(n)
    setBaseline(snapshot(data.allocations, h, a, n)); setNoteEdited(false)
    setSelectedReceipt(data.receipts.find(r => r.associated)?.id ?? null)
    setSelectedItems([]); setAddedReceipts([]); setPending(null); setError(null); setLoadError(null); setStale(false)
  }
  useEffect(() => {
    if (pending) {
      pendingEditor.current?.scrollIntoView({behavior: 'smooth', block: 'center'})
      pendingEditor.current?.querySelector('input')?.focus({preventScroll: true})
    }
  }, [pending !== null])
  async function load() {
    const sequence = ++loadSequence.current
    setBusy(true); setLoadError(null)
    try {
      const data = await api.get<Workspace>(`/payments/${paymentId}/linking-workspace`)
      if (mounted.current && sequence === loadSequence.current) apply(data)
    } catch (e) { if (mounted.current && sequence === loadSequence.current) setLoadError(message(e)) }
    finally { if (mounted.current && sequence === loadSequence.current) setBusy(false) }
  }
  useEffect(() => {
    mounted.current = true
    void load()
    return () => { mounted.current = false }
    // Payment changes remount the editor, discarding state only after navigation approval.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paymentId])
  useEffect(() => {
    let active = true
    api.get<Payment[]>('/payments').then(p => { if (active) setNavigation(p) }).catch(() => { /* Back link remains available. */ })
    return () => { active = false }
  }, [])
  const filters = new URLSearchParams(location.search)
  const group = filters.get('group') ?? filters.get('group_id')
  const nav = navigation.filter(p => p.ledger_state === 'imported' && (!group || String(p.buying_group_id) === group)
    && (filters.get('view') !== 'attention' || p.sync_issues?.length || p.receipt_conflict))
  const index = nav.findIndex(p => p.id === paymentId)
  const back = `/payments${location.search}`
  if (!workspace) return <div className="space-y-4"><Link to={back} className="underline">Back to payments</Link>
    <h1 className="text-2xl font-semibold">Link payment items</h1>
    {loadError ? <><p role="alert">{loadError}</p><button className={button} onClick={load}>Retry loading payment</button></> : <p>Loading payment workspace…</p>}</div>
  const w = workspace, p = w.payment
  const receipt = w.receipts.find(r => r.id === selectedReceipt)
  const unassignedAmount = unassigned.reduce((n, a) => n + cents(a.amount), 0)
  const paymentRemaining = cents(p.total_amount) - sum(rows) - unassignedAmount - cents(historical) - cents(adjustment) - cents(p.allocation_breakdown.correction_amount)
  const errorForDraft = validate(w, rows, historical, adjustment)
  const advancedChanged = cents(historical) !== cents(p.allocation_breakdown.historical_amount) || cents(adjustment) !== cents(p.allocation_breakdown.adjustment_amount)
  const advancedExplained = !advancedChanged || (noteEdited && !!note.trim())
  const same = (a: Allocation, b: Allocation) => a.receipt_source_id === b.receipt_source_id && a.item_id === b.item_id && a.quantity === b.quantity && cents(a.amount) === cents(b.amount)
  const added = rows.filter(a => !w.allocations.some(b => same(a, b))).length
  const removed = w.allocations.filter(a => !rows.some(b => same(a, b))).length
    + (w.unassigned_links ?? []).filter(a => !unassigned.some(b => b.item_id === a.item_id)).length
  const generatedNote = `Payment item links: ${added} added or updated, ${removed} removed or replaced.`
  const visibleReceipts = w.receipts.filter(r => r.associated || addedReceipts.includes(r.id) || rows.some(a => a.receipt_source_id === r.id))
  function qty(item: WorkspaceItem) { return availableQuantity(item, selectedReceipt ?? -1, rows) }
  function canSelect(item: WorkspaceItem) {
    if (unassigned.some(a => a.item_id === item.id) || !item.eligible || remaining(item, rows) <= 0 || qty(item) <= 0) return false
    if (!receipt) return true
    return receipt.present && !receipt.void && !rows.some(a => a.item_id === item.id && a.receipt_source_id === receipt.id)
      && (!receipt.other_commitments.length || receipt.other_commitments.some(c => c.item_id === item.id))
  }
  const normalized = (s: string) => s.trim().toLowerCase()
  function exact(item: WorkspaceItem) {
    if (!receipt) return false
    return (item.receipt_id ?? '').split(/[,;]/).some(id => normalized(id) === normalized(receipt.external_id))
      || item.tracking_numbers.some(t => receipt.tracking_numbers.some(r => normalized(r) === normalized(t)))
  }
  const candidates = w.items.filter(i => !unassigned.some(a => a.item_id === i.id) && i.eligible && remaining(i, rows) > 0)
    .filter(i => !query || [i.description, i.order_number ?? '', String(i.order_id), i.store, i.receipt_id ?? '', ...i.tracking_numbers].join(' ').toLowerCase().includes(query.toLowerCase()))
    .sort((a, b) => Number(exact(b)) - Number(exact(a)) || a.order_id - b.order_id || a.id - b.id)
  const orders = new Map<number, WorkspaceItem[]>()
  candidates.forEach(i => orders.set(i.order_id, [...(orders.get(i.order_id) ?? []), i]))
  function chooseReceipt(id: number) { setSelectedReceipt(id); setSelectedItems([]); setPending(null); setError(null) }
  function link() {
    if (!receipt) return
    const chosen = w.items.filter(i => selectedItems.includes(i.id) && canSelect(i))
    if (!chosen.length) return
    const proposal = chosen.map(i => ({receipt_source_id: receipt.id, item_id: i.id, quantity: qty(i), amount: decimal(remaining(i, rows))}))
    const issue = validate(w, [...rows, ...proposal], historical, adjustment)
    setNotice('')
    if (issue) { setPending(proposal); setAssigningItem(null); setEditingKey(null); setError(`${issue} Review quantities and amounts below.`) }
    else { setRows([...rows, ...proposal]); setSelectedItems([]); setError(null) }
  }
  function confirmPending() {
    if (!pending) return
    const existing = editingKey ? rows.filter(a => `${a.receipt_source_id}:${a.item_id}` !== editingKey) : rows
    const issue = validate(w, [...existing, ...pending], historical, adjustment)
    if (issue) { setError(issue); return }
    setRows([...existing, ...pending]);
    if (assigningItem !== null) setUnassigned(unassigned.filter(a => a.item_id !== assigningItem))
    setAssigningItem(null); setPending(null); setEditingKey(null); setSelectedItems([]); setError(null); setNotice('')
  }
  async function save(reset = false) {
    if (reset && !window.confirm('Reset to automatic matching? This replaces manual links and unsaved changes.')) return
    setBusy(true); setError(null); setNotice('')
    let persisted = false
    try {
      const updated = reset
        ? await api.post<{allocation_version: number}>(`/payments/${paymentId}/allocations/reset`, {version: p.allocation_version})
        : await api.patch<{allocation_version: number}>(`/payments/${paymentId}/allocations`, {version: p.allocation_version, allocations: rows,
          historical_amount: historical, adjustment_amount: adjustment, note: (noteEdited ? note.trim() : generatedNote) || generatedNote})
      persisted = true
      // The write succeeded even if the subsequent evidence refresh fails.
      setWorkspace({...w, unassigned_links: unassigned, payment: {...p, allocation_version: updated.allocation_version}})
      setBaseline(snapshot(rows, historical, adjustment, note))
      const fresh = await api.get<Workspace>(`/payments/${paymentId}/linking-workspace`)
      if (mounted.current) { apply(fresh); setNotice(reset ? 'Automatic matching restored.' : 'Payment changes saved.') }
    } catch (e) {
      if (mounted.current) {
        setError(persisted ? `Changes saved, but evidence could not refresh: ${message(e)}. Reload before continuing.` : message(e))
        setStale(persisted || (e instanceof ApiError && e.status === 409))
      }
    } finally { if (mounted.current) setBusy(false) }
  }
  function reload() {
    if (!dirty || window.confirm('Reload this payment and discard your unsaved changes?')) void load()
  }
  return <div className="space-y-4 text-ink">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <Link to={back} className="underline">← Back to payments</Link>
      <nav aria-label="Payment navigation" className="flex gap-3 text-sm">
        {index > 0 && <Link className="underline" to={`/payments/${nav[index - 1].id}/link${location.search}`}>Previous payment</Link>}
        {index >= 0 && index < nav.length - 1 && <Link className="underline" to={`/payments/${nav[index + 1].id}/link${location.search}`}>Next payment</Link>}
      </nav>
    </div>
    <header className={`${panel} sticky top-14 z-10 shadow-sm`}>
      <div className="flex flex-wrap items-start justify-between gap-3"><div>
        <p className="text-sm text-ink-muted">{p.group_name}</p>
        <h1 className="text-2xl font-semibold">Payment {p.payment_id ?? p.id}</h1>
        <p className="text-sm text-ink-muted">{p.status} · Requested {date(p.payment_requested_at)} · Sent {date(p.payment_sent_at)}</p>
        <p className="text-sm">{p.monarch_receipt ? `${p.receipt_conflict ? 'Receipt conflict' : 'Received'} · ${p.monarch_receipt.account_name} · ${date(p.monarch_receipt.posted_date)}` : 'Awaiting Monarch deposit link'}</p>
      </div><div className="flex flex-wrap gap-2 items-center">
        <span className="text-sm" role="status">{notice || (dirty ? 'Unsaved changes' : 'All changes saved')}</span>
        <button className={primary} disabled={busy || stale || !dirty || !!errorForDraft || !advancedExplained || !!pending || !!unassigned.length} onClick={() => save()}>Save changes</button>
      </div></div>
      <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm"><strong>Source total {money(cents(p.total_amount))}</strong><span>Linked items {money(sum(rows) + unassignedAmount)}</span><strong>Unresolved {money(paymentRemaining)}</strong></div>
    </header>
    {p.sync_issues.map(issue => <p key={issue} className="text-sm text-amber-700 dark:text-amber-300">{issue}</p>)}
    {p.receipt_conflict && <p className="text-amber-700">Payment or deposit evidence changed. Reconcile the deposit in <Link to="/payments/monarch" className="underline">Monarch review</Link>.</p>}
    {(error || loadError) && <p role="alert" className="text-red-700 dark:text-red-300">{error || loadError}</p>}
    {stale && <button className={button} disabled={busy} onClick={reload}>Reload payment</button>}
    {loadError && !stale && <button className={button} disabled={busy} onClick={reload}>Retry loading payment</button>}
    {errorForDraft && <p className="text-sm text-amber-700 dark:text-amber-300">{errorForDraft}</p>}
    <fieldset disabled={busy || stale} className="min-w-0 space-y-4">
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 items-start">
      <section aria-label="Payment items" className={panel}>
        <div><h2 className="text-lg font-semibold">Payment items</h2><p className="text-sm text-ink-muted">Select a group receipt, then choose local items on the right.</p></div>
        {!visibleReceipts.length && <p className="text-sm">No receipts identified for this payment. Add a group receipt to start linking.</p>}
        {visibleReceipts.map(r => {
          const linked = rows.filter(a => a.receipt_source_id === r.id)
          return <article key={r.id} className={`rounded-lg border p-3 space-y-2 ${selectedReceipt === r.id ? 'border-brand-500 bg-brand-50 dark:bg-gray-800' : 'border-brand-100 dark:border-gray-700'}`}>
            <label className="flex items-start gap-2 cursor-pointer"><input type="radio" name="target-receipt" aria-label={`Select receipt ${r.external_id}`} checked={selectedReceipt === r.id} onChange={() => chooseReceipt(r.id)} className="mt-1" />
              <span className="min-w-0 break-words"><strong>{r.description || 'No description'}</strong><span className="block text-sm">Receipt {r.external_id} · Qty {r.quantity ?? 'unknown'} · {money(cents(r.amount))}</span></span></label>
            <p className="text-sm">Available receipt balance {money(cents(r.baseline_amount) - cents(r.other_amount) - sum(linked))}</p>
            {(!r.present || r.void) && <p className="text-sm text-amber-700">{r.void ? 'Void receipt' : 'Missing from source'} — existing links can be removed.</p>}
            {linked.map(a => <div key={a.item_id} className="rounded-lg bg-white dark:bg-gray-900 p-2 text-sm space-y-2">
              <p className="break-words">{w.items.find(i => i.id === a.item_id)?.description ?? `Item ${a.item_id}`} · Qty {a.quantity} · {money(cents(a.amount))}</p>
              <div className="flex gap-2"><button className={button} onClick={() => {setPending([{...a}]); setAssigningItem(null); setEditingKey(`${a.receipt_source_id}:${a.item_id}`); setError(null)}}>Edit link</button>
                <button className={button} onClick={() => {setRows(rows.filter(row => row !== a)); setPending(null); setError(null); setNotice('')}}>Unlink item</button></div>
            </div>)}
          </article>
        })}
        <label className="block text-sm">Add group receipt<select aria-label="Add group receipt" value="" className={`${input} block w-full mt-1`} onChange={e => {
          const id = Number(e.target.value); if (id) {setAddedReceipts([...addedReceipts, id]); chooseReceipt(id)}
        }}><option value="">Choose another receipt…</option>{w.receipts.filter(r => r.present && !r.void && !visibleReceipts.some(v => v.id === r.id)).map(r => <option key={r.id} value={r.id}>{r.external_id} · {r.description} · {money(cents(r.amount))}</option>)}</select></label>
        {!!unassigned.length && <div className="space-y-3 border-t pt-3">
          <h3 className="font-semibold">Preserved links awaiting receipt assignment</h3>
          <p className="text-sm">These saved links have no group receipt evidence. Assign a receipt or explicitly unlink each item before saving.</p>
          {unassigned.map(a => <div key={a.item_id} className="space-y-2 text-sm">
            <p>{w.items.find(i => i.id === a.item_id)?.description ?? `Item ${a.item_id}`} · {money(cents(a.amount))}</p>
            <div className="flex flex-wrap gap-2"><button className={button} disabled={!receipt?.present || receipt.void} onClick={() => {
              if (!receipt) return
              setPending([{...a, receipt_source_id: receipt.id}]); setAssigningItem(a.item_id); setEditingKey(null); setError(null)
            }}>Assign to selected receipt</button>
              <button className={button} onClick={() => {setUnassigned(unassigned.filter(row => row !== a)); setNotice('')}}>Unlink preserved item</button></div>
          </div>)}
        </div>}
        {!!p.corrections.length && <div className="space-y-2 text-sm border-t pt-3"><h3 className="font-semibold">Receipt corrections</h3>{p.corrections.map((c, i) => <p key={i}>Item {c.item_id} · {money(cents(c.amount))} · {c.explanation}</p>)}</div>}
      </section>
      <section aria-label="Available order items" className={panel}>
        <h2 className="text-lg font-semibold">Available order items</h2>
        <p className="text-sm">{receipt ? `Linking to receipt ${receipt.external_id}` : 'Choose a payment receipt on the left.'}</p>
        <input className={`${input} w-full`} type="search" aria-label="Search available items" placeholder="Search items, orders, receipts or tracking" value={query} onChange={e => setQuery(e.target.value)} />
        <button className={primary} disabled={!receipt || !selectedItems.some(id => w.items.some(i => i.id === id && canSelect(i))) || !!pending} onClick={link}>Link selected{selectedItems.length ? ` (${selectedItems.length})` : ''}</button>
        {!orders.size && <p className="text-sm">No available items match. Only received items with remaining payout capacity appear here.</p>}
        {[...orders].map(([orderId, items]) => <details key={orderId} open className="rounded-lg border border-brand-100 dark:border-gray-700 p-3">
          <summary className="cursor-pointer font-semibold break-words">{items[0].store} · Order {items[0].order_number || orderId} · {items.length} items</summary>
          <div className="flex flex-wrap justify-between gap-2 text-sm my-3"><label className="flex gap-2 items-center"><input type="checkbox" aria-label={`Select order ${items[0].order_number || orderId}`} disabled={!receipt || !items.some(canSelect)} checked={items.some(canSelect) && items.filter(canSelect).every(i => selectedItems.includes(i.id))} onChange={e => {
            const ids = items.filter(canSelect).map(i => i.id)
            setSelectedItems(e.target.checked ? [...new Set([...selectedItems, ...ids])] : selectedItems.filter(id => !ids.includes(id)))
          }} />Select eligible items</label><Link className="underline" to={`/?order_id=${orderId}`}>Open order</Link></div>
          <div className="space-y-3">{items.map(i => <label key={i.id} className="flex items-start gap-2 text-sm cursor-pointer">
            <input type="checkbox" aria-label={`Select item ${i.description}`} checked={selectedItems.includes(i.id)} disabled={!receipt || !canSelect(i)} onChange={e => setSelectedItems(e.target.checked ? [...selectedItems, i.id] : selectedItems.filter(id => id !== i.id))} className="mt-1" />
            <span className="min-w-0 break-words"><strong>{i.description}</strong>{exact(i) && <span className="ml-2 text-xs text-green-700 dark:text-green-300">Receipt / tracking match</span>}
              <span className="block">{qty(i) > 0 ? `Qty available ${qty(i)}` : 'Units committed to another receipt'} · {money(remaining(i, rows))} remaining</span>
              {qty(i) <= 0 && <span className="block text-ink-muted">Choose the receipt already covering these units.</span>}
              {(cents(i.other_amount) !== 0 || rows.some(a => a.item_id === i.id)) && <span className="block text-ink-muted">Partially covered · Other payments {money(cents(i.other_amount))}</span>}
              <span className="block text-ink-muted">{i.receipt_id ? `Receipt ${i.receipt_id} · ` : ''}{i.tracking_numbers.join(', ')}</span>
            </span></label>)}</div>
        </details>)}
      </section>
    </div>
    {pending && <section ref={pendingEditor} aria-label="Edit link amounts" className={panel}>
      <h2 className="font-semibold">Review link quantities and amounts</h2>
      <p className="text-sm">Receipt {w.receipts.find(r => r.id === pending[0]?.receipt_source_id)?.external_id} · Enter the intended values. Amounts are not reduced automatically to fit a balance.</p>
      {pending.map((a, index) => <div key={`${a.receipt_source_id}:${a.item_id}`} className="flex flex-wrap gap-3 items-center">
        <span className="text-sm">{w.items.find(i => i.id === a.item_id)?.description}</span>
        <label className="text-sm">Quantity<input className={`${input} block w-24`} aria-label={`Link ${index + 1} quantity`} type="number" min="1" step="1" value={a.quantity} onChange={e => setPending(pending.map((r, n) => n === index ? {...r, quantity: Number(e.target.value)} : r))} /></label>
        <label className="text-sm">Amount<input className={`${input} block w-32`} aria-label={`Link ${index + 1} amount`} type="number" min="0.01" step="0.01" value={a.amount} onChange={e => setPending(pending.map((r, n) => n === index ? {...r, amount: e.target.value} : r))} /></label>
      </div>)}
      <div className="flex gap-2"><button className={primary} onClick={confirmPending}>Apply links to draft</button><button className={button} onClick={() => {setPending(null); setError(null)}}>Cancel link edit</button></div>
    </section>}
    <section className={panel}>
      <label className="block text-sm">Explanation<textarea aria-label="Adjustment explanation" className={`${input} block w-full mt-1`} value={noteEdited ? note : dirty ? generatedNote : note} placeholder="An explanation is generated when you link or unlink items" onChange={e => {setNote(e.target.value); setNoteEdited(true)}} /></label>
      <details><summary className="cursor-pointer font-semibold">Other adjustments</summary>
        <div className="space-y-3 mt-3"><p className="text-sm">Historical amounts represent purchases outside local history. Changing these amounts requires your explanation.</p>
          <div className="flex flex-wrap gap-3"><label className="text-sm">Historical amount<input aria-label="Historical amount" className={`${input} block w-36`} type="number" min="0" step="0.01" value={historical} onChange={e => setHistorical(e.target.value)} /></label>
          <label className="text-sm">Explained adjustment<input aria-label="Explained adjustment" className={`${input} block w-36`} type="number" step="0.01" value={adjustment} onChange={e => setAdjustment(e.target.value)} /></label></div>
          {!advancedExplained && <p className="text-sm text-amber-700">Write an explanation for the historical amount or adjustment change.</p>}
          <button className={button} onClick={() => save(true)}>Reset to automatic matching</button>
        </div>
      </details>
    </section>
    </fieldset>
  </div>
}
