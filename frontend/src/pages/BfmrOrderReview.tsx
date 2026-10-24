import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import type { BfmrOrderTask, BuyingGroup } from '../api/types'
import { useGroupSubmissionsEnabled } from '../utils/capabilities'

type Candidate = {
  reserve_id: string; qty: number; title: string; exact_match: boolean
  reserved_at: string | null; order_deadline: string | null
  retailer_links: { retailer: string; identifier_id: string }[]
}

function TaskReview({ task, enabled, onSaved }: { task: BfmrOrderTask; enabled: boolean; onSaved: () => void }) {
  const [candidates, setCandidates] = useState<Candidate[] | null>(null)
  const [quantities, setQuantities] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const base = `/buying-groups/${task.buying_group_id}/order-numbers/${task.id}`
  const completed = task.allocations.filter(a => a.status === 'submitted').reduce((sum, a) => sum + a.qty, 0)
  const remaining = task.snapshot.quantity - completed
  const uncertain = task.allocations.some(a => a.status === 'sending')
  const selected = Object.entries(quantities).filter(([, qty]) => Number(qty) > 0).map(([reserve_id, qty]) => ({ reserve_id, qty: Number(qty) }))
  const total = selected.reduce((sum, a) => sum + a.qty, 0)

  async function run(action: () => Promise<void>) {
    setBusy(true); setError('')
    try { await action() } catch (e) { setError(e instanceof Error ? e.message : 'Request failed') }
    finally { setBusy(false) }
  }

  return <article className="rounded-lg border border-brand-200 dark:border-gray-700 bg-white dark:bg-gray-800 p-4 space-y-3">
    <div className="flex flex-wrap justify-between gap-2">
      <div>
        <h2 className="font-medium">{task.snapshot.title || 'Imported product'}</h2>
        <p className="text-sm text-ink-muted">Order {task.snapshot.order_no} · {task.snapshot.retailer} · {task.snapshot.sku || 'Product ID unavailable'} · Quantity {task.snapshot.quantity}</p>
      </div>
      <span className="text-sm font-medium">{task.status.replace(/_/g, ' ')}</span>
    </div>
    {task.message && <p className="text-sm">{task.message}</p>}
    {task.next_attempt_at && <p className="text-xs text-ink-muted">Next attempt: {new Date(task.next_attempt_at).toLocaleString()}</p>}
    {task.allocations.length > 0 && <ul className="text-xs text-ink-muted space-y-1">
      {task.allocations.map((a, i) => <li key={i}>{a.qty} unit(s) · {a.status}{a.purchase_id ? ` · Purchase ${a.purchase_id}` : ''}</li>)}
    </ul>}
    {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
    {task.status !== 'submitted' && <div className="flex flex-wrap gap-3">
      <button type="button" disabled={busy} className="text-sm underline disabled:opacity-50"
        onClick={() => run(async () => { setCandidates(await api.get<Candidate[]>(`${base}/candidates`)); setQuantities({}) })}>Refresh commitments</button>
      <button type="button" disabled={busy || !enabled} className="text-sm underline disabled:opacity-50"
        onClick={() => run(async () => { await api.post(`${base}/retry`, {}); setCandidates(null); onSaved() })}>Retry reconciliation</button>
    </div>}
    {candidates && <div className="space-y-2">
      <p className="text-sm">Allocate {remaining} remaining unit(s). Selecting a commitment confirms that it is the intended product.</p>
      {uncertain && <p className="text-sm">Reconcile the previous submission before changing allocations.</p>}
      {candidates.length === 0 && <p className="text-sm text-ink-muted">No active commitments are available.</p>}
      {candidates.map(candidate => <label key={candidate.reserve_id} className="flex items-center gap-3 border-t border-brand-100 dark:border-gray-700 py-2">
        <input type="number" min={0} max={Math.min(candidate.qty, remaining)} step={1}
          aria-label={`Quantity for ${candidate.title} (${candidate.reserve_id})`}
          value={quantities[candidate.reserve_id] ?? ''} disabled={busy || !enabled || uncertain}
          onChange={e => setQuantities(prev => ({ ...prev, [candidate.reserve_id]: e.target.value }))}
          className="w-20 rounded border border-brand-200 dark:border-gray-600 px-2 py-1 dark:bg-gray-700" />
        <span className="text-sm">{candidate.title} · {candidate.qty} available{candidate.exact_match ? ' · Exact product match' : ''}
          <span className="block text-xs text-ink-muted">{candidate.retailer_links.map(link => `${link.retailer}: ${link.identifier_id}`).join(' · ')}</span>
          {candidate.order_deadline && <span className="block text-xs">Order deadline: {candidate.order_deadline}</span>}
        </span>
      </label>)}
      <button type="button" disabled={busy || !enabled || uncertain || selected.length === 0 || total !== remaining || selected.some(a => !Number.isInteger(a.qty))}
        className="rounded bg-brand-600 text-white px-3 py-1.5 text-sm disabled:opacity-50"
        onClick={() => run(async () => { await api.post(`${base}/allocations`, { allocations: selected }); setCandidates(null); onSaved() })}>
        Save allocations and queue submission ({total}/{remaining})
      </button>
    </div>}
  </article>
}

export default function BfmrOrderReview() {
  const [params, setParams] = useSearchParams()
  const groupId = Number(params.get('group')) || null
  const [groups, setGroups] = useState<BuyingGroup[]>([])
  const [tasks, setTasks] = useState<BfmrOrderTask[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const submissionsEnabled = useGroupSubmissionsEnabled()
  const requestVersion = useRef(0)
  const group = groups.find(g => g.id === groupId)
  const load = useCallback(async () => {
    const version = ++requestVersion.current
    if (!groupId) { setTasks([]); return }
    setBusy(true)
    try {
      const next = await api.get<BfmrOrderTask[]>(`/buying-groups/${groupId}/order-numbers`)
      if (version === requestVersion.current) { setTasks(next); setError('') }
    } catch (e) {
      if (version === requestVersion.current) setError(e instanceof Error ? e.message : 'Could not load order numbers')
    } finally { if (version === requestVersion.current) setBusy(false) }
  }, [groupId])

  useEffect(() => { api.get<BuyingGroup[]>('/buying-groups').then(all => setGroups(all.filter(g => g.api_framework === 'bfmr'))).catch(e => setError(String(e))) }, [])
  useEffect(() => { setTasks([]); void load() }, [load])
  useEffect(() => {
    const refresh = () => { if (document.visibilityState === 'visible') void load() }
    window.addEventListener('focus', refresh)
    return () => window.removeEventListener('focus', refresh)
  }, [load])

  async function scan() {
    if (!groupId) return
    setBusy(true)
    try { await api.post(`/buying-groups/${groupId}/order-numbers/scan`, {}); await load() }
    catch (e) { setError(e instanceof Error ? e.message : 'Scan failed') }
    finally { setBusy(false) }
  }

  return <div className="max-w-5xl mx-auto p-4 space-y-4">
    <h1 className="text-2xl font-semibold">BFMR order numbers</h1>
    <p className="text-sm text-ink-muted">Imported products with a unique commitment are registered automatically when enabled. Review uncertain matches here.</p>
    <Link to="/settings/buying-groups" className="text-sm underline">Buying group settings</Link>
    <div className="flex flex-wrap items-center gap-3">
      <select aria-label="BFMR group" value={groupId ?? ''} onChange={e => setParams({ group: e.target.value })}
        className="rounded border border-brand-200 dark:border-gray-600 p-2 dark:bg-gray-800">
        <option value="">Choose BFMR account</option>
        {groups.map(g => <option key={g.id} value={g.id}>{g.name}</option>)}
      </select>
      <button type="button" onClick={() => void load()} disabled={busy || !groupId} className="text-sm underline disabled:opacity-50">Refresh status</button>
      <button type="button" onClick={() => void scan()} disabled={busy || !group?.order_number_submit_enabled} className="text-sm underline disabled:opacity-50">Scan existing BFMR orders</button>
    </div>
    {!submissionsEnabled && <p className="text-sm">Group submissions are disabled in this environment. You can inspect commitments and submission status.</p>}
    {group && !group.order_number_submit_enabled && <p className="text-sm">Automatic order-number submission is off for this account.</p>}
    {error && <p role="alert" className="text-red-600">{error}</p>}
    {groupId && !busy && tasks.length === 0 && <p>No order-number tasks yet. Import an order with BFMR selected, or enable the setting and scan existing orders.</p>}
    {tasks.map(task => <TaskReview key={`${groupId}:${task.id}:${task.status}:${task.attempts}`} task={task}
      enabled={submissionsEnabled && !!group?.order_number_submit_enabled} onSaved={() => void load()} />)}
  </div>
}
