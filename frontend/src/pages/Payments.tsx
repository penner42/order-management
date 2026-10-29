import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import type { Payment } from '../api/types'
import type { PaymentCapability } from './PaymentReview'

interface Capability extends PaymentCapability { last_run_at: string | null; error: string | null; payment_sync_enabled: boolean }
const money = (value: string | number | undefined) => Number(value ?? 0).toLocaleString('en-US', {style: 'currency', currency: 'USD'})
const date = (value: string | null | undefined) => value ? new Date(value).toLocaleDateString() : '—'
const button = 'rounded border border-brand-200 px-3 py-1.5 text-sm disabled:opacity-50'
const input = 'rounded border border-brand-200 bg-white dark:bg-gray-800 px-2 py-1 text-sm'

function PaymentCard({ payment, search }: {payment: Payment; search: string}) {
  const b = payment.allocation_breakdown
  return <section aria-label={`Payment ${payment.payment_id ?? payment.id}`} className="rounded-xl border border-brand-200 dark:border-gray-700 p-4 space-y-2">
    <div className="flex flex-wrap justify-between items-start gap-3">
      <div><h2 className="font-semibold">{payment.buying_group?.name} · Payment {payment.payment_id ?? payment.id} · {money(payment.total_amount)}</h2>
        <p className="text-sm text-ink-muted">{payment.source_details?.source?.status ?? 'Legacy'} · Requested {date(payment.payment_requested_at)} · Sent {date(payment.payment_sent_at)}</p>
        <p className="text-sm">{payment.monarch_receipt ? `${payment.receipt_conflict ? 'Receipt conflict' : 'Received'} · ${payment.monarch_receipt.account_name} · ${date(payment.monarch_receipt.posted_date)}` : 'Awaiting Monarch deposit link'}</p>
      </div>
      {payment.ledger_state === 'imported' && <Link className={button} to={`/payments/${payment.id}/link${search}`}>Open payment</Link>}
    </div>
    {b && <p className="text-sm">Linked items {money(b.linked_amount)} · Corrections {money(b.correction_amount)} · Historical {money(b.historical_amount)} · Adjustments {money(b.adjustment_amount)} · Unresolved {money(b.unresolved_amount)}</p>}
    {payment.allocation_manual && <p className="text-sm text-ink-muted">Manual allocation edits retained{payment.allocation_details?.note ? `: ${payment.allocation_details.note}` : ''}</p>}
    {payment.sync_issues?.map(issue => <p key={issue} className="text-sm text-amber-700 dark:text-amber-300">{issue}</p>)}
    {payment.ledger_state === 'legacy' && !!payment.line_items.length && <ul className="text-sm space-y-1">{payment.line_items.map(li => <li key={li.id}>
      <Link className="underline" to={`/?order_id=${li.item?.order_id}`}>{li.item?.description ?? `Item ${li.item_id}`}</Link> · {money(li.amount ?? '0')}
    </li>)}</ul>}
  </section>
}

export default function Payments() {
  const [params, setParams] = useSearchParams()
  const groupId = params.get('group') ?? params.get('group_id') ?? ''
  const [payments, setPayments] = useState<Payment[]>([])
  const [groups, setGroups] = useState<Capability[]>([])
  const view = params.get('view') ?? 'all'
  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(params)
    if (next.has('group_id') && !next.has('group')) next.set('group', next.get('group_id')!)
    next.delete('group_id')
    if (value) next.set(key, value); else next.delete(key)
    setParams(next)
  }
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
      <label>Buying group <select aria-label="Buying group" className={input} value={groupId} onChange={e => setFilter('group', e.target.value)}><option value="">All groups</option>{groups.map(g => <option key={g.buying_group_id} value={g.buying_group_id}>{g.name}</option>)}</select></label>
      <label>View <select aria-label="Payment view" className={input} value={view} onChange={e => setFilter('view', e.target.value)}><option value="all">All imported payments</option><option value="attention">Needs attention</option><option value="legacy">Legacy reconciliation</option></select></label>
      <button className={button} disabled={busy || loading || !groups.some(g => g.ready && (!groupId || String(g.buying_group_id) === groupId))} onClick={sync}>{busy ? 'Syncing…' : 'Sync now'}</button>
      <strong>Source total {money(total)}</strong>
    </div>
    {view === 'legacy' && <p>These preserved manual payments are excluded from imported totals. A unique matching source reference and total links them during sync.</p>}
    {groups.filter(g => !groupId || String(g.buying_group_id) === groupId).map(g => <p key={g.buying_group_id} className="text-sm text-ink-muted">{g.name}: {g.ready ? `Automatic sync ${g.payment_sync_enabled ? 'on' : 'off'} · Last run ${date(g.last_run_at)}` : g.reason}{g.error && ` · ${g.error}`}</p>)}
    {error && <p role="alert" className="text-red-700">{error}</p>}
    {loading ? <p>Loading payments…</p> : !shown.length ? <p>No payments in this view.</p> : shown.map(p => <PaymentCard key={`${p.id}:${p.allocation_version}`} payment={p} search={params.size ? `?${params}` : ''} />)}
  </div>
}
