import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import type { Payment } from '../api/types'
import type { PaymentCapability } from './PaymentReview'

interface Capability extends PaymentCapability { last_run_at: string | null; error: string | null; payment_sync_enabled: boolean }
interface Allocation { receipt_source_id: number; item_id: number; quantity: number; amount: string }
interface Evidence {
  receipts: { id: number; external_id: string; present: boolean; payload: { description: string; amount: string; quantity: number | null; void: boolean } }[]
  items: { id: number; label: string; quantity: number; order_id: number; remaining: string }[]
}
const money = (value: string | number | undefined) => Number(value ?? 0).toLocaleString('en-US', {style: 'currency', currency: 'USD'})
const date = (value: string | null | undefined) => value ? new Date(value).toLocaleDateString() : '—'
const button = 'rounded border border-brand-200 px-3 py-1.5 text-sm disabled:opacity-50'
const input = 'rounded border border-brand-200 bg-white dark:bg-gray-800 px-2 py-1 text-sm'

function PaymentCard({ payment, saved }: {payment: Payment; saved: () => Promise<void>}) {
  const [open, setOpen] = useState(false)
  const [evidence, setEvidence] = useState<Evidence | null>(null)
  const [allocations, setAllocations] = useState<Allocation[]>([])
  const [historical, setHistorical] = useState('0')
  const [adjustment, setAdjustment] = useState('0')
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const b = payment.allocation_breakdown
  const legacy = payment.ledger_state !== 'imported'
  async function edit() {
    setError(null); setBusy(true)
    try {
      setEvidence(await api.get<Evidence>(`/payment-review/groups/${payment.buying_group_id}`))
      setAllocations(payment.source_details?.receipts.map(r => ({receipt_source_id: r.source_id, item_id: r.item_id, quantity: r.quantity, amount: r.amount})) ?? [])
      setHistorical(b?.historical_amount ?? '0'); setAdjustment(b?.adjustment_amount ?? '0')
      setNote(payment.allocation_details?.note ?? ''); setOpen(true)
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not load evidence') }
    finally { setBusy(false) }
  }
  async function save(reset = false) {
    setBusy(true); setError(null)
    try {
      if (reset) await api.post(`/payments/${payment.id}/allocations/reset`, {version: payment.allocation_version})
      else await api.patch(`/payments/${payment.id}/allocations`, {version: payment.allocation_version, allocations, historical_amount: historical, adjustment_amount: adjustment, note})
      await saved(); setOpen(false)
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not save allocations') }
    finally { setBusy(false) }
  }
  function update(index: number, patch: Partial<Allocation>) { setAllocations(rows => rows.map((r, i) => i === index ? {...r, ...patch} : r)) }
  return <section aria-label={`Payment ${payment.payment_id ?? payment.id}`} className="rounded-xl border border-brand-200 dark:border-gray-700 p-4 space-y-3">
    <div className="flex flex-wrap justify-between gap-3">
      <div><h2 className="font-semibold">{payment.buying_group?.name} · Payment {payment.payment_id ?? payment.id} · {money(payment.total_amount)}</h2>
        <p className="text-sm text-ink-muted">{payment.source_details?.source?.status ?? 'Legacy'} · Requested {date(payment.payment_requested_at)} · Sent {date(payment.payment_sent_at)}</p>
        <p className="text-sm">{payment.monarch_receipt ? `${payment.receipt_conflict ? 'Receipt conflict' : 'Received'} · ${payment.monarch_receipt.account_name} · ${date(payment.monarch_receipt.posted_date)}` : 'Awaiting Monarch deposit link'}</p>
      </div>
      {!legacy && <button className={button} disabled={busy} onClick={edit}>Adjust allocations</button>}
    </div>
    {b && <p className="text-sm">Linked items {money(b.linked_amount)} · Corrections {money(b.correction_amount)} · Historical {money(b.historical_amount)} · Adjustments {money(b.adjustment_amount)} · Unresolved {money(b.unresolved_amount)}</p>}
    {payment.allocation_manual && <p className="text-sm text-ink-muted">Manual allocation edits retained{payment.allocation_details?.note ? `: ${payment.allocation_details.note}` : ''}</p>}
    {payment.sync_issues?.map(issue => <p key={issue} className="text-sm text-amber-700 dark:text-amber-300">{issue}</p>)}
    {!!payment.source_details?.receipt_evidence?.length && <details className="text-sm">
      <summary className="cursor-pointer">Group receipts ({payment.source_details.receipt_evidence.length})</summary>
      <ul className="mt-2 space-y-1">{payment.source_details.receipt_evidence.map(r => <li key={r.source_id}>Receipt {r.external_id} · {r.description || 'No description'} · Qty {r.quantity ?? 'unknown'} · Receipt total {money(r.amount)} · {date(r.created_at)}{r.present ? '' : ' · Missing from source'}</li>)}</ul>
    </details>}
    {payment.receipt_conflict && <p role="alert" className="text-amber-700">Payment or deposit evidence changed. Unlink and reconcile the deposit in <Link className="underline" to="/payments/monarch">Monarch review</Link>.</p>}
    {!!payment.line_items.length && <ul className="text-sm space-y-1">{payment.line_items.map(li => <li key={li.id}>
      <Link className="underline" to={`/?order_id=${li.item?.order_id}`}>{li.item?.description ?? `Item ${li.item_id}`}</Link> · {money(li.amount ?? '0')}
    </li>)}</ul>}
    {!!payment.corrections?.length && <ul className="text-sm space-y-1">{payment.corrections.map(c => <li key={c.id}>Receipt correction · Item {c.item_id} · {money(c.amount)} · {c.explanation}</li>)}</ul>}
    {open && evidence && <div className="space-y-3 border-t border-brand-100 pt-3">
      <p className="text-sm">Group totals and dates update on sync. Explain allocation changes below. Historical amounts represent purchases outside your local history.</p>
      {allocations.map((a, i) => <div className="flex flex-wrap gap-2" key={i}>
        <select className={input} aria-label={`Allocation ${i + 1} receipt`} value={a.receipt_source_id} onChange={e => update(i, {receipt_source_id: Number(e.target.value)})}>
          {evidence.receipts.filter(r => r.id === a.receipt_source_id || (r.present && !r.payload.void)).map(r => <option key={r.id} value={r.id}>Receipt {r.external_id} · {r.payload.description} · {money(r.payload.amount)}</option>)}
        </select>
        <select className={`${input} max-w-full`} aria-label={`Allocation ${i + 1} item`} value={a.item_id} onChange={e => update(i, {item_id: Number(e.target.value)})}>
          {evidence.items.map(item => <option key={item.id} value={item.id}>{item.label} · {money(item.remaining)} remaining</option>)}
        </select>
        <label>Qty <input className={`${input} w-20`} aria-label={`Allocation ${i + 1} quantity`} type="number" min="1" value={a.quantity} onChange={e => update(i, {quantity: Number(e.target.value)})} /></label>
        <label>Amount <input className={`${input} w-28`} aria-label={`Allocation ${i + 1} amount`} type="number" step="0.01" min="0.01" value={a.amount} onChange={e => update(i, {amount: e.target.value})} /></label>
        <button className={button} onClick={() => setAllocations(rows => rows.filter((_, n) => n !== i))}>Remove item</button>
      </div>)}
      <button className={button} disabled={!evidence.receipts.some(r => r.present && !r.payload.void) || !evidence.items.length} onClick={() => {
        const r = evidence.receipts.find(r => r.present && !r.payload.void); const item = evidence.items[0]
        if (r && item) setAllocations(rows => [...rows, {receipt_source_id: r.id, item_id: item.id, quantity: item.quantity, amount: '0.01'}])
      }}>Add local item</button>
      <div className="flex flex-wrap gap-3">
        <label>Historical amount <input className={`${input} w-32`} aria-label="Historical amount" type="number" min="0" step="0.01" value={historical} onChange={e => setHistorical(e.target.value)} /></label>
        <label>Explained adjustment <input className={`${input} w-32`} aria-label="Explained adjustment" type="number" step="0.01" value={adjustment} onChange={e => setAdjustment(e.target.value)} /></label>
      </div>
      <label className="block">Explanation <textarea className={`${input} block w-full`} aria-label="Adjustment explanation" value={note} onChange={e => setNote(e.target.value)} /></label>
      <div className="flex gap-2"><button className={button} disabled={busy || !note.trim()} onClick={() => save()}>Save adjustments</button>
        <button className={button} disabled={busy} onClick={() => save(true)}>Reset to automatic matching</button>
        <button className={button} disabled={busy} onClick={() => setOpen(false)}>Cancel</button></div>
    </div>}
    {error && <p role="alert" className="text-red-700">{error}</p>}
  </section>
}

export default function Payments() {
  const [params, setParams] = useSearchParams()
  const groupId = params.get('group') ?? params.get('group_id') ?? ''
  const [payments, setPayments] = useState<Payment[]>([])
  const [groups, setGroups] = useState<Capability[]>([])
  const [view, setView] = useState('all')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  async function load() {
    const [p, g] = await Promise.all([api.get<Payment[]>('/payments'), api.get<Capability[]>('/payment-review/capabilities')])
    setPayments(p); setGroups(g)
  }
  useEffect(() => { load().catch(e => setError(String(e))).finally(() => setLoading(false)) }, [])
  async function sync() {
    setBusy(true); setError(null)
    const failures: string[] = []
    for (const g of groups.filter(g => g.ready && (!groupId || String(g.buying_group_id) === groupId))) {
      try { await api.post(`/payment-review/groups/${g.buying_group_id}/refresh`, {}) }
      catch (e) { failures.push(`${g.name}: ${e instanceof Error ? e.message : 'Sync failed'}`) }
    }
    try { await load() } catch (e) { failures.push(String(e)) }
    setError(failures.length ? failures.join(' · ') : null); setBusy(false)
  }
  const filtered = payments.filter(p => !groupId || String(p.buying_group_id) === groupId)
  const imported = filtered.filter(p => p.ledger_state === 'imported')
  const shown = view === 'legacy' ? filtered.filter(p => p.ledger_state === 'legacy') : imported.filter(p => view !== 'attention' || p.sync_issues?.length || p.receipt_conflict)
  const total = imported.filter(p => !p.source_details?.source?.void && p.source_details?.present !== false).reduce((sum, p) => sum + Number(p.total_amount ?? 0), 0)
  return <div className="space-y-5 text-ink">
    <div className="flex flex-wrap justify-between gap-3"><div><h1 className="text-2xl font-semibold">Payments</h1><p className="text-sm text-ink-muted">Buying-group payments from January 1, 2026. Item matching and bank receipt are tracked separately.</p></div>
      <Link className="underline" to="/payments/monarch">Monarch review</Link></div>
    <div className="flex flex-wrap items-center gap-3">
      <label>Buying group <select aria-label="Buying group" className={input} value={groupId} onChange={e => setParams(e.target.value ? {group: e.target.value} : {})}><option value="">All groups</option>{groups.map(g => <option key={g.buying_group_id} value={g.buying_group_id}>{g.name}</option>)}</select></label>
      <label>View <select aria-label="Payment view" className={input} value={view} onChange={e => setView(e.target.value)}><option value="all">All imported payments</option><option value="attention">Needs attention</option><option value="legacy">Legacy reconciliation</option></select></label>
      <button className={button} disabled={busy || loading || !groups.some(g => g.ready && (!groupId || String(g.buying_group_id) === groupId))} onClick={sync}>{busy ? 'Syncing…' : 'Sync now'}</button>
      <strong>Source total {money(total)}</strong>
    </div>
    {view === 'legacy' && <p>These preserved manual payments are excluded from imported totals. A unique matching source reference and total links them during sync.</p>}
    {groups.filter(g => !groupId || String(g.buying_group_id) === groupId).map(g => <p key={g.buying_group_id} className="text-sm text-ink-muted">{g.name}: {g.ready ? `Automatic sync ${g.payment_sync_enabled ? 'on' : 'off'} · Last run ${date(g.last_run_at)}` : g.reason}{g.error && ` · ${g.error}`}</p>)}
    {error && <p role="alert" className="text-red-700">{error}</p>}
    {loading ? <p>Loading payments…</p> : !shown.length ? <p>No payments in this view.</p> : shown.map(p => <PaymentCard key={`${p.id}:${p.allocation_version}`} payment={p} saved={load} />)}
  </div>
}
