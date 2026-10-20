import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import type { Payment } from '../api/types'
import { parseDecimal } from '../utils/decimal'

export interface PaymentCapability { buying_group_id: number; name: string; supported: boolean; ready: boolean; reason: string | null; provider: string | null }
interface Allocation { receipt_source_id: number; item_id: number; quantity: number; amount: string }
interface Correction { receipt_source_id: number; item_id: number; amount: string; explanation: string }
interface Content { receipt_ids: number[]; allocations: Allocation[]; corrections: Correction[]; window_start: string | null; window_end: string | null; existing_payment_id?: number | null; payment_bonus?: string; note: string; manual: boolean }
interface Review { id: number; version: number; fingerprint: string; payment_id: number | null; external_id: string; source: { amount: string; status: string; created_at: string | null; sent_at: string | null; memo: string; warnings: string[] }; content: Content; allocated_total: string; difference: string; issues: string[] }
interface Receipt { id: number; external_id: string; covered: boolean; present: boolean; payload: { amount: string; quantity: number | null; description: string; created_at: string | null; void: boolean; tracking_numbers: string[] } }
interface LocalItem { id: number; description: string; quantity: number; price_sold: string; receipt_id: string | null; remaining: string; status: string; order_id: number }
interface Outstanding { receipt_source_id: number; external_id: string; original_amount: string; covered_amount: string; current_amount: string; amount: string; present: boolean; origin_payment_id: number; items: { item_id: number; amount: string }[] }
interface GroupReview { capability: PaymentCapability; reviews: Review[]; receipts: Receipt[]; items: LocalItem[]; corrections: Outstanding[] }

const field = 'rounded border border-brand-200 dark:border-gray-600 bg-white dark:bg-gray-800 px-2 py-1 text-sm text-ink'
const button = 'rounded border border-brand-200 dark:border-gray-600 px-3 py-1.5 text-sm text-ink disabled:opacity-50 hover:bg-brand-50 dark:hover:bg-gray-700'
const money = (v: string | number) => Number(v).toLocaleString('en-US', { style: 'currency', currency: 'USD' })
const date = (v: string | null) => v ? new Date(v).toLocaleString() : '—'
function localInput(v: string | null) {
  if (!v) return ''
  const d = new Date(v)
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16)
}

function ReviewCard({ review, group, payments, update, busy, run }: { review: Review; group: GroupReview; payments: Payment[]; update: (g: GroupReview) => void; busy: boolean; run: (fn: () => Promise<void>) => Promise<void> }) {
  const [content, setContent] = useState<Content>(structuredClone(review.content))
  const [dirty, setDirty] = useState(false)
  const [showAll, setShowAll] = useState(false)
  const [itemSearch, setItemSearch] = useState('')
  const confirmed = review.payment_id !== null
  function edit(next: Content) { setContent(next); setDirty(true) }
  const save = (rebuild = false) => run(async () => {
    update(await api.patch<GroupReview>(`/payment-review/drafts/${review.id}`, { version: review.version, content, rebuild_window: rebuild }))
  })
  function toggleReceipt(receipt: Receipt) {
    const included = content.receipt_ids.includes(receipt.id)
    const matches = group.items.filter(i => i.receipt_id?.trim() === receipt.external_id)
    edit({ ...content, receipt_ids: included ? content.receipt_ids.filter(id => id !== receipt.id) : [...content.receipt_ids, receipt.id],
      allocations: included ? content.allocations.filter(a => a.receipt_source_id !== receipt.id) : [...content.allocations,
        ...matches.map(i => ({ receipt_source_id: receipt.id, item_id: i.id, quantity: i.quantity, amount: (Number(i.price_sold) * i.quantity).toFixed(2) }))] })
  }
  function changeAllocation(index: number, patch: Partial<Allocation>) {
    edit({ ...content, allocations: content.allocations.map((a, n) => n === index ? { ...a, ...patch } : a) })
  }
  const selected = group.receipts.filter(r => content.receipt_ids.includes(r.id))
  const available = group.items.filter(i => `${i.id} ${i.description} ${i.receipt_id ?? ''}`.toLowerCase().includes(itemSearch.toLowerCase()))
  const total = content.allocations.reduce((sum, a) => sum + Number(a.amount), 0) + content.corrections.reduce((sum, c) => sum + Number(c.amount), 0) + Number(content.payment_bonus ?? 0)
  return <section aria-label={`Payment ${review.external_id}`} className="rounded-xl border border-brand-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-4 space-y-4">
    <div className="flex flex-wrap justify-between gap-2">
      <div><h2 className="font-semibold text-lg text-ink">Payment {review.external_id} · {money(review.source.amount)}</h2>
        <p className="text-sm text-ink-muted">{review.source.status} · Created {date(review.source.created_at)} · Sent {date(review.source.sent_at)}</p>
        {review.source.memo && <p className="text-sm text-ink-muted">{review.source.memo}</p>}</div>
      {confirmed && <Link className="underline" to="/payments">Confirmed as payment #{review.payment_id}</Link>}
    </div>
    {review.source.warnings?.map(w => <p className="text-amber-700 dark:text-amber-300 text-sm" key={w}>{w}</p>)}
    {review.issues.length > 0 && <ul className="list-disc pl-5 text-sm text-amber-800 dark:text-amber-300">{review.issues.map(issue => <li key={issue}>{issue}</li>)}</ul>}
    {!confirmed && <div className="flex flex-wrap items-end gap-3">
      <label className="text-sm text-ink">After <input type="datetime-local" className={`${field} block`} aria-label="Receipt window start" value={localInput(content.window_start)} onChange={e => edit({ ...content, window_start: e.target.value ? new Date(e.target.value).toISOString() : null })} /></label>
      <label className="text-sm text-ink">Through <input type="datetime-local" className={`${field} block`} aria-label="Receipt window end" value={localInput(content.window_end)} onChange={e => edit({ ...content, window_end: e.target.value ? new Date(e.target.value).toISOString() : null })} /></label>
      <button className={button} disabled={busy} onClick={() => save(true)}>Rebuild receipt selection</button>
    </div>}
    <div>
      <h3 className="font-medium text-ink">Receipt coverage</h3>
      {!confirmed && <><p className="text-sm text-ink-muted">Link received order items below. Split larger lines on Orders when needed.</p>
        <label className="block text-sm text-ink my-2"><input type="checkbox" checked={showAll} onChange={e => setShowAll(e.target.checked)} /> Show other receipts to include</label>
        <input className={`${field} w-full max-w-sm mb-2`} aria-label="Search local items" placeholder="Search local items by name, ID, or receipt" value={itemSearch} onChange={e => setItemSearch(e.target.value)} /></>}
      <div className="space-y-3">
        {(showAll ? group.receipts.filter(r => !r.covered || content.receipt_ids.includes(r.id)) : selected).map(receipt => {
          const included = content.receipt_ids.includes(receipt.id)
          return <div key={receipt.id} className="rounded border border-brand-100 dark:border-gray-700 p-3">
            <label className="flex gap-2 text-sm text-ink"><input type="checkbox" aria-label={`Include receipt ${receipt.external_id}`} checked={included} disabled={confirmed || busy || (!included && (!receipt.present || receipt.payload.void))} onChange={() => toggleReceipt(receipt)} />
              <span>Receipt {receipt.external_id} · {money(receipt.payload.amount)} · Qty {receipt.payload.quantity ?? 'unknown'} · {date(receipt.payload.created_at)}<br />{receipt.payload.description}</span></label>
            {included && <div className="mt-2 space-y-2">
              {content.allocations.map((a, index) => a.receipt_source_id === receipt.id && <div key={index} className="flex flex-wrap gap-2 items-center">
                <select className={`${field} max-w-full`} aria-label={`Receipt ${receipt.external_id} item ${index + 1}`} disabled={confirmed || busy} value={a.item_id} onChange={e => changeAllocation(index, { item_id: Number(e.target.value) })}>
                  {group.items.filter(i => i.id === a.item_id || available.includes(i)).map(i => <option key={i.id} value={i.id}>#{i.id} {i.description} · {i.status} · {money(i.remaining)} remaining</option>)}
                </select>
                <label className="text-sm text-ink">Qty <input className={`${field} w-20`} aria-label={`Allocation ${index + 1} quantity`} type="number" min="1" value={a.quantity} disabled={confirmed || busy} onChange={e => changeAllocation(index, { quantity: Number(e.target.value) })} /></label>
                <label className="text-sm text-ink">Amount <input className={`${field} w-28`} aria-label={`Allocation ${index + 1} amount`} type="number" step="0.01" min="0.01" value={a.amount} disabled={confirmed || busy} onChange={e => changeAllocation(index, { amount: e.target.value })} /></label>
                {!confirmed && <button className={button} disabled={busy} onClick={() => edit({ ...content, allocations: content.allocations.filter((_, n) => n !== index) })}>Remove item</button>}
              </div>)}
              {!confirmed && <button className={button} disabled={busy || !available.length} onClick={() => {
                const i = available.find(item => !content.allocations.some(a => a.receipt_source_id === receipt.id && a.item_id === item.id))
                if (i) edit({ ...content, allocations: [...content.allocations, { receipt_source_id: receipt.id, item_id: i.id, quantity: i.quantity, amount: (Number(i.price_sold) * i.quantity).toFixed(2) }] })
              }}>Add local item</button>}
            </div>}
          </div>
        })}
        {!selected.length && <p className="text-sm text-ink-muted">No receipts selected.</p>}
      </div>
    </div>
    <div className="space-y-2">
      <h3 className="font-medium text-ink">Carried receipt corrections</h3>
      {content.corrections.map((c, index) => <div key={index} className="flex flex-wrap gap-2 items-center text-sm text-ink">
        <span>Receipt {group.receipts.find(r => r.id === c.receipt_source_id)?.external_id} · Item #{c.item_id}</span>
        <input className={`${field} w-28`} type="number" step="0.01" aria-label={`Correction ${index + 1} amount`} value={c.amount} disabled={confirmed || busy} onChange={e => edit({ ...content, corrections: content.corrections.map((x, n) => n === index ? { ...x, amount: e.target.value } : x) })} />
        <input className={`${field} flex-1 min-w-48`} aria-label={`Correction ${index + 1} explanation`} value={c.explanation} disabled={confirmed || busy} onChange={e => edit({ ...content, corrections: content.corrections.map((x, n) => n === index ? { ...x, explanation: e.target.value } : x) })} />
        {!confirmed && <button className={button} disabled={busy} onClick={() => edit({ ...content, corrections: content.corrections.filter((_, n) => n !== index) })}>Remove correction</button>}
      </div>)}
      {!confirmed && <select aria-label="Add outstanding correction" className={`${field} max-w-full`} value="" disabled={busy} onChange={e => {
        const c = group.corrections.find(x => x.receipt_source_id === Number(e.target.value))
        if (!c) return
        const sum = c.items.reduce((n, i) => n + Number(i.amount), 0)
        let used = 0
        const corrections = c.items.map((i, n) => {
          const value = n === c.items.length - 1 ? Number(c.amount) - used : Math.round(Number(c.amount) * Number(i.amount) / sum * 100) / 100
          used += value
          return { receipt_source_id: c.receipt_source_id, item_id: i.item_id, amount: value.toFixed(2), explanation: 'Receipt correction carried to this payment' }
        }).filter(c => Number(c.amount) !== 0)
        edit({ ...content, corrections: [...content.corrections, ...corrections] })
      }}><option value="">Add an outstanding correction…</option>{group.corrections.filter(c => c.present && Number(c.amount) !== 0 && !content.corrections.some(x => x.receipt_source_id === c.receipt_source_id)).map(c => <option key={c.receipt_source_id} value={c.receipt_source_id}>Receipt {c.external_id}: {money(c.amount)} from payment #{c.origin_payment_id}</option>)}</select>}
    </div>
    {!confirmed && <>
      <label className="block text-sm text-ink">Explicit bonus or fee (explanation required)<input className={`${field} block w-40`} type="number" step="0.01" value={content.payment_bonus ?? '0.00'} onChange={e => edit({ ...content, payment_bonus: e.target.value })} /></label>
      <label className="block text-sm text-ink">Review explanation<textarea className={`${field} block w-full`} disabled={busy} value={content.note} onChange={e => edit({ ...content, note: e.target.value })} /></label>
      <label className="block text-sm text-ink">Link an existing local payment
        <select className={`${field} block w-full max-w-xl`} disabled={busy} value={content.existing_payment_id ?? ''} onChange={e => edit({ ...content, existing_payment_id: e.target.value ? Number(e.target.value) : null })}>
          <option value="">Create a new payment</option>
          {content.existing_payment_id && !payments.some(p => p.id === content.existing_payment_id) && <option value={content.existing_payment_id} disabled>Previously selected payment is unavailable — choose another payment</option>}
          {payments.map(payment => {
            const amount = payment.total_amount ?? payment.line_items.reduce((sum, li) => sum + parseDecimal(li.amount), 0) + parseDecimal(payment.payment_bonus) + (payment.corrections ?? []).reduce((sum, c) => sum + c.amount, 0)
            const unavailable = payment.source_details || group.reviews.some(r => r.payment_id === payment.id) ? 'Already linked' : payment.monarch_receipt ? 'Monarch confirmed' : null
            return <option key={payment.id} value={payment.id} disabled={!!unavailable}>{payment.payment_id?.trim() || 'No payment ID'} · {money(amount)} · {date(payment.payment_requested_at)}{unavailable ? ` · ${unavailable}` : ''}</option>
          })}
        </select>
      </label>
      <div className="flex flex-wrap items-center gap-3 text-ink"><strong>Allocated {money(total)} · Difference {money(Number(review.source.amount) - total)}</strong>
        <button className={button} disabled={busy || !dirty} onClick={() => save()}>Save review</button>
        <button className={`${button} bg-brand-100 dark:bg-gray-700`} disabled={busy || dirty || review.issues.length > 0} onClick={() => run(async () => update(await api.post<GroupReview>(`/payment-review/drafts/${review.id}/confirm`, { version: review.version, fingerprint: review.fingerprint })))}>Confirm payment</button>
        {dirty && <span className="text-sm text-ink-muted">Save changes to validate before confirming.</span>}
      </div>
    </>}
  </section>
}

export default function PaymentReviewPage() {
  const [params, setParams] = useSearchParams()
  const groupId = Number(params.get('group')) || null
  const [capabilities, setCapabilities] = useState<PaymentCapability[]>([])
  const [group, setGroup] = useState<GroupReview | null>(null)
  const [payments, setPayments] = useState<Payment[]>([])
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => { api.get<PaymentCapability[]>('/payment-review/capabilities').then(setCapabilities).catch(e => setError(String(e))) }, [])
  useEffect(() => {
    let active = true
    setGroup(null); setError(null)
    setPayments([])
    setLoading(Boolean(groupId))
    if (groupId) {
      setLoading(true)
      Promise.all([
        api.get<GroupReview>(`/payment-review/groups/${groupId}`),
        api.get<Payment[]>(`/payments?buying_group_id=${groupId}`),
      ]).then(([g, localPayments]) => {
        if (active) {
          setGroup(g)
          setPayments(localPayments.filter(p => p.buying_group_id === groupId))
        }
      }).catch(e => { if (active) setError(String(e)) }).finally(() => { if (active) setLoading(false) })
    }
    return () => { active = false }
  }, [groupId])
  async function run(fn: () => Promise<void>) {
    setBusy(true); setError(null)
    try { await fn() } catch (e) { setError(e instanceof Error ? e.message : String(e)) } finally { setBusy(false) }
  }
  return <div className="space-y-5">
    <Link to="/payments" className="underline text-brand-700 dark:text-brand-300">Payments</Link>
    <h1 className="text-2xl font-semibold text-ink">Payment review</h1>
    <p className="text-sm text-ink-muted">Review portal payments against received items. PAID means sent; confirm bank receipt separately through Monarch or Payments.</p>
    <div className="flex flex-wrap gap-3 items-center">
      <select className={field} aria-label="Buying group" value={groupId ?? ''} disabled={busy} onChange={e => setParams(e.target.value ? { group: e.target.value } : {})}><option value="">Select a buying group</option>{capabilities.map(c => <option key={c.buying_group_id} value={c.buying_group_id}>{c.name}{!c.supported ? ' (not supported yet)' : !c.ready ? ' (configuration needed)' : ''}</option>)}</select>
      <button className={button} disabled={busy || !group?.capability.ready} onClick={() => run(async () => setGroup(await api.post<GroupReview>(`/payment-review/groups/${groupId}/refresh`, {})))}>{busy ? 'Working…' : 'Check payments'}</button>
      {group?.capability.reason && <p className="text-sm text-ink-muted">{group.capability.reason}</p>}
    </div>
    {error && <p role="alert" className="text-red-700 dark:text-red-400 whitespace-pre-wrap">{error}</p>}
    {loading && <p className="text-ink-muted">Loading review…</p>}
    {group && <>
      {group.corrections.length > 0 && <section className="rounded border border-amber-300 p-3 text-sm text-ink"><h2 className="font-medium">Outstanding receipt corrections</h2>{group.corrections.map(c => <p key={c.receipt_source_id}>Receipt {c.external_id}: originally {money(c.original_amount)}, covered {money(c.covered_amount)}, now {money(c.current_amount)} · Carry {money(c.amount)}{!c.present ? ' · Missing source: review required' : ''}</p>)}<p>Corrections stay queued until assigned and confirmed on a later payment. To move a saved correction, remove and save it on its current draft first.</p></section>}
      {!group.reviews.length && <p className="text-ink-muted">No imported payments yet. Check payments to load the group’s history.</p>}
      <p className="text-sm text-ink-muted">{group.reviews.filter(r => !r.payment_id).length} pending payments · {group.reviews.filter(r => r.payment_id).length} confirmed · {group.receipts.length} receipts</p>
      {[...group.reviews].reverse().map((review, index) => <details key={`${groupId}:${review.id}:${review.version}`} open={index === 0}>
        <summary className="cursor-pointer p-3 text-ink font-medium">Payment {review.external_id} · {money(review.source.amount)} · {review.payment_id ? 'Confirmed' : `${review.issues.length} review issues`}</summary>
        <ReviewCard review={review} group={group} payments={payments} update={setGroup} busy={busy} run={run} />
      </details>)}
      {group.receipts.some(r => !r.covered && !group.reviews.some(d => d.content.receipt_ids.includes(r.id))) && <section className="rounded border border-brand-200 dark:border-gray-700 p-4 text-ink"><h2 className="font-medium">Receipts outside payment windows</h2>{group.receipts.filter(r => !r.covered && !group.reviews.some(d => d.content.receipt_ids.includes(r.id))).map(r => <p className="text-sm" key={r.id}>Receipt {r.external_id} · {money(r.payload.amount)} · {r.payload.description}</p>)}</section>}
    </>}
  </div>
}
