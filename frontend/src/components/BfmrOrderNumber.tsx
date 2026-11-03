import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { api } from '../api/client'
import type { BfmrOrderTask } from '../api/types'

type Candidate = { reserve_id: string; title: string; qty: number; exact_match: boolean; eligible: boolean; reason: string | null }
type Purchase = { purchase_id: string; title: string; order_no: string; qty: number }
type Product = { product_key: string; snapshot: BfmrOrderTask['snapshot']; task: BfmrOrderTask | null; existing_match?: boolean; purchases?: Purchase[]; candidates: Candidate[] }
type Preview = { order_no: string | null; products: Product[] }

export function BfmrOrderNumber({ orderId, groupId, enabled, submitted, onSaved }: {
  orderId: number; groupId: number; enabled: boolean; submitted?: boolean; onSaved: () => void
}) {
  const [open, setOpen] = useState(false)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [selected, setSelected] = useState<Record<string, boolean>>({})
  const [quantities, setQuantities] = useState<Record<string, Record<string, string>>>({})
  const [purchaseIds, setPurchaseIds] = useState<Record<string, string[]>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState('')
  const closeButton = useRef<HTMLButtonElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const base = `/buying-groups/${groupId}/order-numbers/orders/${orderId}`

  useEffect(() => {
    if (!open) return
    closeButton.current?.focus()
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !busy) { setOpen(false); trigger.current?.focus() }
    }
    window.addEventListener('keydown', escape)
    return () => window.removeEventListener('keydown', escape)
  }, [open, busy])

  async function load(reconcile = true) {
    const next = await api.get<Preview>(`${base}/preview`)
    const matches = next.products.filter(p => p.existing_match && p.task?.status !== 'submitted')
    if (reconcile && matches.length) {
      try {
        const tasks = await api.post<BfmrOrderTask[]>(`${base}/reconcile`, {
          products: matches.map(p => ({ product_key: p.product_key, snapshot: p.snapshot })),
        })
        for (const product of next.products) {
          const task = tasks.find(t => t.product_key === product.product_key)
          if (task) product.task = task
        }
        const linked = tasks.filter(t => t.status === 'submitted').length
        setResult(`${linked}/${tasks.length} products automatically linked to existing BFMR submissions. ${tasks.filter(t => t.status !== 'submitted').map(t => t.message).join(' ')}`)
        onSaved()
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Could not automatically link the existing BFMR submission. Refresh to retry.')
      }
    }
    setPreview(next)
    setSelected({})
    setPurchaseIds({})
    const defaults: Record<string, Record<string, string>> = {}
    for (const product of next.products) {
      const remaining = product.snapshot.quantity - (product.task?.allocations ?? []).filter(a => a.status === 'submitted').reduce((sum, a) => sum + a.qty, 0)
      const exact = product.candidates.filter(c => c.eligible && c.exact_match)
      defaults[product.product_key] = exact.length === 1 && exact[0].qty >= remaining && remaining > 0
        ? { [exact[0].reserve_id]: String(remaining) } : {}
    }
    setQuantities(defaults)
  }

  async function refresh() {
    setBusy(true); setError('')
    try { await load() } catch (e) { setError(e instanceof Error ? e.message : 'Could not load commitments') }
    finally { setBusy(false) }
  }

  function allocations(product: Product) {
    return Object.entries(quantities[product.product_key] ?? {}).filter(([, value]) => Number(value) > 0)
      .map(([reserve_id, value]) => ({ reserve_id, qty: Number(value) }))
  }
  const chosen = preview?.products.filter(p => selected[p.product_key]) ?? []
  const invalidLink = chosen.some(p => {
    const ids = purchaseIds[p.product_key] ?? []
    return !ids.length || (p.purchases ?? []).filter(a => ids.includes(a.purchase_id)).reduce((sum, a) => sum + a.qty, 0) !== p.snapshot.quantity
  })
  const invalid = chosen.some(p => {
    if (p.task?.allocations.some(a => a.status === 'sending')) return false
    const completed = (p.task?.allocations ?? []).filter(a => a.status === 'submitted').reduce((sum, a) => sum + a.qty, 0)
    const values = allocations(p)
    return values.some(a => !Number.isInteger(a.qty)) || values.reduce((sum, a) => sum + a.qty, completed) !== p.snapshot.quantity
  })

  async function submit() {
    setBusy(true); setError(''); setResult('')
    try {
      const tasks = await api.post<BfmrOrderTask[]>(`${base}/submit`, { products: chosen.map(p => ({
        product_key: p.product_key, snapshot: p.snapshot,
        allocations: p.task?.allocations.some(a => a.status === 'sending') ? [] : allocations(p),
      })) })
      const succeeded = tasks.filter(t => t.status === 'submitted').length
      setResult(`${succeeded}/${tasks.length} products registered. ${tasks.filter(t => t.status !== 'submitted').map(t => t.message).join(' ')}`)
      onSaved()
      await load(false)
    } catch (e) { setError(e instanceof Error ? e.message : 'Submission failed'); onSaved() }
    finally { setBusy(false) }
  }

  async function linkSelected() {
    setBusy(true); setError(''); setResult('')
    try {
      const tasks = await api.post<BfmrOrderTask[]>(`${base}/reconcile`, {
        products: chosen.map(p => ({ product_key: p.product_key, snapshot: p.snapshot, purchase_ids: purchaseIds[p.product_key] })),
      })
      setResult(`${tasks.filter(t => t.status === 'submitted').length}/${tasks.length} products linked. ${tasks.filter(t => t.status !== 'submitted').map(t => t.message).join(' ')}`)
      onSaved()
      await load(false)
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not link commitment'); onSaved() }
    finally { setBusy(false) }
  }

  return <>
    {submitted
      ? <span className="shrink-0 whitespace-nowrap text-xs text-ink-muted">BFMR order number submitted</span>
      : <button ref={trigger} type="button" className="shrink-0 whitespace-nowrap text-xs underline" onClick={() => { setOpen(true); setPreview(null); setResult(''); void refresh() }}>BFMR order number</button>}
    {open && createPortal(<div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4">
      <div role="dialog" aria-modal="true" aria-label="BFMR order number" className="bg-white dark:bg-gray-800 rounded-xl p-5 w-full max-w-3xl max-h-[90vh] overflow-y-auto space-y-4 text-ink"
        onKeyDown={event => {
          if (event.key !== 'Tab') return
          const elements = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled)'))
          const first = elements[0], last = elements[elements.length - 1]
          if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
          else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
        }}>
        <div className="flex justify-between gap-4"><h2 className="font-semibold">BFMR order number {preview?.order_no}</h2>
          <button ref={closeButton} type="button" disabled={busy} onClick={() => { setOpen(false); trigger.current?.focus() }}>Close</button></div>
        <p className="text-sm">Register the saved order number before tracking is available. Select products and allocate their quantities to commitments.</p>
        <p className="text-sm">Existing BFMR submissions are linked automatically when the order number, product, and quantity match. Otherwise, select an existing purchase to link, or allocate an unassigned commitment to register the order number.</p>
        {!enabled && <p className="text-sm">Preview only: new group submissions are disabled in this environment. Existing submissions can still be recognized automatically.</p>}
        {error && <p role="alert" className="text-red-600">{error}</p>}
        {result && <p role="status">{result}</p>}
        {busy && <p role="status">Loading BFMR…</p>}
        {preview?.products.length === 0 && <p>No eligible products with a saved order number.</p>}
        {preview?.products.map(product => {
          const uncertain = product.task?.allocations.some(a => a.status === 'sending')
          const complete = product.task?.status === 'submitted'
          const completed = (product.task?.allocations ?? []).filter(a => a.status === 'submitted').reduce((sum, a) => sum + a.qty, 0)
          const exact = product.candidates.filter(c => c.eligible && c.exact_match)
          return <fieldset key={product.product_key} disabled={busy} className="border rounded p-3 space-y-2">
            <label className="flex gap-2"><input type="checkbox" disabled={complete} checked={!!selected[product.product_key]}
              onChange={e => setSelected(prev => ({ ...prev, [product.product_key]: e.target.checked }))} />
              <span>{product.snapshot.title} · Quantity {product.snapshot.quantity} · {product.snapshot.sku || 'Product ID unavailable'}</span></label>
            <p className="text-sm">{product.task?.status.replace(/_/g, ' ') || 'Not registered'}{completed > 0 && ` · ${completed} units registered`}</p>
            {product.task?.message && <p className="text-sm">{product.task.message}</p>}
            {uncertain && <p className="text-sm">Select this product to reconcile its previous submission before sending again.</p>}
            {!complete && <>
              {!!product.purchases?.length && <div className="space-y-2">
                <p className="text-sm font-medium">Link an existing BFMR purchase</p>
                <p className="text-xs">Select the commitment that belongs to this product, even if its BFMR order number differs. Linking saves its status here without changing BFMR.</p>
                <select multiple aria-label={`Existing BFMR purchases for ${product.snapshot.title}`} className="w-full border rounded p-2 dark:bg-gray-700"
                  value={purchaseIds[product.product_key] ?? []}
                  onChange={e => setPurchaseIds(prev => ({ ...prev, [product.product_key]: Array.from(e.target.selectedOptions, option => option.value) }))}>
                  {product.purchases.map(purchase => <option key={purchase.purchase_id} value={purchase.purchase_id}>
                    {purchase.title} · Order {purchase.order_no} · Quantity {purchase.qty} · {purchase.purchase_id}
                  </option>)}
                </select>
              </div>}
              {exact.length === 0 && <p className="text-sm">No active exact product match. Review the commitments before selecting quantities.</p>}
              {exact.length > 1 && <p className="text-sm">Multiple exact matches. Choose the intended commitments and quantities.</p>}
              {exact.length === 1 && exact[0].qty < product.snapshot.quantity - completed && <p className="text-sm">The exact commitment has insufficient quantity. Allocate across commitments or refresh availability.</p>}
              {product.candidates.map(candidate => <label key={candidate.reserve_id} className="flex items-center gap-3 text-sm">
                <input type="number" min={0} max={Math.min(candidate.qty, product.snapshot.quantity - completed)} step={1}
                  className="w-20 border rounded p-1 dark:bg-gray-700" aria-label={`Quantity for ${product.snapshot.title}: ${candidate.title} (${candidate.reserve_id})`}
                  disabled={!candidate.eligible || !!uncertain} value={quantities[product.product_key]?.[candidate.reserve_id] ?? ''}
                  onChange={e => setQuantities(prev => ({ ...prev, [product.product_key]: { ...prev[product.product_key], [candidate.reserve_id]: e.target.value } }))} />
                <span>{candidate.title} · {candidate.qty} available{candidate.exact_match ? ' · Exact product match' : ''}{candidate.reason && ` · ${candidate.reason}`}</span>
              </label>)}
              <p className="text-xs">Allocate {product.snapshot.quantity - completed} remaining units. Choosing a commitment confirms it is the intended product.</p>
            </>}
          </fieldset>
        })}
        <div className="flex gap-4">
          <button type="button" disabled={busy} className="underline text-sm" onClick={() => void refresh()}>Refresh commitments</button>
          <button type="button" disabled={busy || !chosen.length || invalidLink} className="border rounded px-3 py-2 disabled:opacity-50" onClick={() => void linkSelected()}>Link selected commitment</button>
          {enabled && <button type="button" disabled={busy || !chosen.length || invalid} className="bg-brand-600 text-white rounded px-3 py-2 disabled:opacity-50" onClick={() => void submit()}>Submit order number</button>}
        </div>
      </div>
    </div>, document.body)}
  </>
}
