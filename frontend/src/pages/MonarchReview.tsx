import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import { MONARCH, cents, dollars, type MonarchDeposit, type MonarchPaymentChoice, type MonarchReviewData } from '../api/monarch'

const buttonClass = 'rounded border border-brand-200 dark:border-gray-600 px-3 py-2 disabled:opacity-50'

function DepositReview({ deposit, payments, refresh }: { deposit: MonarchDeposit; payments: MonarchPaymentChoice[]; refresh: () => Promise<void> }) {
  const [selected, setSelected] = useState<number[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const selectedPayments = payments.filter(p => selected.includes(p.id))
  const total = selectedPayments.reduce((sum, p) => sum + cents(p.amount), 0)
  const difference = cents(deposit.amount) - total
  const visible = payments.filter(p => selected.includes(p.id) || `${p.reference ?? ''} ${p.buying_group_name} ${p.amount} ${p.date}`.toLowerCase().includes(search.toLowerCase()))
  async function action(work: () => Promise<unknown>) {
    setBusy(true); setError('')
    try { await work(); setSelected([]); await refresh() }
    catch (e) { setError(e instanceof Error ? e.message : 'Request failed') }
    finally { setBusy(false) }
  }
  return <article className="rounded-lg border border-brand-200 dark:border-gray-600 p-4 space-y-3" aria-label={`${deposit.merchant} ${deposit.posted_date}`}>
    <h2 className="text-lg font-semibold">{deposit.merchant} · {dollars(cents(deposit.amount))}</h2>
    <p>{deposit.posted_date} · {deposit.account_name}</p>
    <p className="break-words">{deposit.description}</p>
    {error && <p role="alert" className="text-red-700 dark:text-red-300">{error}</p>}
    {deposit.evidence_changed && <p role="alert">Monarch evidence changed since confirmation. Review the linked receipt and undo if needed.</p>}
    {deposit.confirmation_id ? <>
      <p>Confirmed payments: {deposit.confirmation?.payments.map(p => `${p.buying_group_name} ${p.reference ?? ''} (${dollars(cents(p.amount))})`).join(', ')}</p>
      <p>Receipt evidence: {deposit.confirmation?.deposit.posted_date} · {dollars(cents(deposit.confirmation?.deposit.amount ?? '0'))}</p>
      <button className={buttonClass} disabled={busy} onClick={() => void action(() => api.post(`${MONARCH}/confirmations/${deposit.confirmation_id}/undo`, {}))}>Undo confirmation</button>
    </> : deposit.dismissed ? <button className={buttonClass} disabled={busy} onClick={() => void action(() => api.patch(`${MONARCH}/deposits/${deposit.id}`, { dismissed: false }))}>Restore deposit</button> : <>
      {!deposit.eligible && <p>{deposit.pending ? 'Pending or no longer present in Monarch. Receipt confirmation is unavailable.' : 'Select this receiving account in Monarch settings to confirm.'}</p>}
      {deposit.eligible && <>
        <h3 className="font-semibold">Suggested matches</h3>
        {!deposit.suggestions.length && <p>No exact matches suggested. Choose whole payments below.</p>}
        {deposit.search_truncated && <p>Combination search limit reached. Other matches may exist; select payments manually.</p>}
        {deposit.suggestions.map((suggestion, index) => <div key={index} className="space-y-1">
          <button className={buttonClass} disabled={busy} onClick={() => setSelected(suggestion.payment_ids)}>
            Choose {suggestion.payment_ids.map(id => { const p = payments.find(row => row.id === id); return p ? `${p.buying_group_name} ${p.reference ?? ''} (${dollars(cents(p.amount))})` : 'Unavailable payment' }).join(' + ')}
          </button><p className="text-sm">{suggestion.reason}</p>
        </div>)}
        <details open={selected.length > 0 || undefined}>
          <summary className="cursor-pointer font-medium">Select payments manually</summary>
          <label className="block my-2">Find payments <input className="rounded border p-2 dark:bg-gray-800 dark:border-gray-600" value={search} onChange={e => setSearch(e.target.value)} /></label>
          <div className="max-h-72 overflow-auto space-y-2">
            {visible.map(p => <label key={p.id} className="flex items-center gap-2">
              <input type="checkbox" disabled={busy} checked={selected.includes(p.id)} onChange={e => setSelected(e.target.checked ? [...selected, p.id] : selected.filter(id => id !== p.id))} />
              {p.buying_group_name} · {p.reference ?? 'No reference'} · {p.date} · {dollars(cents(p.amount))}
              {p.received_at && <span className="text-sm text-ink-muted">Already marked received · {p.received_at.slice(0, 10)}</span>}
            </label>)}
          </div>
        </details>
        <p>Selected total: {dollars(total)} · Difference: {dollars(difference)}</p>
        <button className={`${buttonClass} bg-brand-600 text-white`} disabled={busy || !selected.length || difference !== 0 || selectedPayments.length !== selected.length} onClick={() => void action(() => api.post(`${MONARCH}/deposits/${deposit.id}/confirm`, {
          revision: deposit.revision, payments: selectedPayments.map(p => ({ id: p.id, fingerprint: p.fingerprint })),
        }))}>Confirm receipt</button>
      </>}
      <button className={`${buttonClass} ml-2`} disabled={busy} onClick={() => void action(() => api.patch(`${MONARCH}/deposits/${deposit.id}`, { dismissed: true }))}>Dismiss deposit</button>
    </>}
  </article>
}

export default function MonarchReview() {
  const [data, setData] = useState<MonarchReviewData | null>(null)
  const [error, setError] = useState('')
  const [filter, setFilter] = useState('review')
  async function refresh() { setData(await api.get<MonarchReviewData>(`${MONARCH}/review`)) }
  useEffect(() => { refresh().catch(e => setError(e.message)) }, [])
  const deposits = data?.deposits.filter(d => filter === 'all' || (filter === 'confirmed' ? !!d.confirmation_id : !d.confirmation_id && !d.dismissed)) ?? []
  return <div className="space-y-4 text-ink dark:text-gray-100">
    <h1 className="text-2xl font-semibold">Monarch review</h1>
    <div className="flex gap-4"><Link className="underline" to="/payments">Payments</Link><Link className="underline" to="/settings/monarch">Monarch settings and checks</Link></div>
    <p>Link posted deposits to whole payments, including payments already marked received without a Monarch link. Confirmation sets received dates to the Monarch transaction’s posted date. Selected payments must exactly equal the deposit.</p>
    {error && <p role="alert">{error}</p>}
    <label>Show <select className="rounded border p-2 dark:bg-gray-800 dark:border-gray-600" value={filter} onChange={e => setFilter(e.target.value)}>
      <option value="review">Awaiting review</option><option value="confirmed">Confirmed</option><option value="all">All deposits</option>
    </select></label>
    <button className={`${buttonClass} ml-3`} onClick={() => { setError(''); void refresh().catch(e => setError(e.message)) }}>Refresh</button>
    {data && !deposits.length && <p>No deposits in this view.</p>}
    {deposits.map(deposit => <DepositReview key={`${deposit.id}:${deposit.revision}:${deposit.confirmation_id ?? ''}`} deposit={deposit} payments={data?.payments ?? []} refresh={refresh} />)}
  </div>
}
