import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'

interface ReceivingMatch {
  item_id: number
  order_id: number
  store_order_number: string | null
  description: string | null
  quantity: number
  tracking_number: string
  price_sold: string | null
  expected_amount: string | null
  receipt_id: string | null
  scanned_at: string | null
  received_item: string | null
  received_qty: number | null
  received_price: string | null
  received_ext_total: string | null
  received_status: string | null
  amount_discrepancy: boolean
  selected: boolean
}

interface ReceivingSession {
  buying_group_id: number
  buying_group_name: string
  created_at: string | null
  not_found_count: number
  void_skipped_count: number
  matches: ReceivingMatch[]
}

function parseMoney(v: string | null | undefined): number | null {
  if (v == null || !String(v).trim()) return null
  const n = Number(String(v).replace(/[^0-9.-]/g, ''))
  return Number.isFinite(n) ? n : null
}

function fmtMoney(v: string | number | null | undefined): string {
  if (v == null || v === '') return '—'
  const n = typeof v === 'number' ? v : parseMoney(String(v))
  if (n == null) return '—'
  return `$${n.toFixed(2)}`
}

function toYyyyMmDd(iso: string | null | undefined): string {
  if (!iso) {
    const d = new Date()
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  }
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso.trim())
  if (m) return `${m[1]}-${m[2]}-${m[3]}`
  try {
    const d = new Date(iso)
    if (!Number.isNaN(d.getTime())) {
      return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
    }
  } catch {
    // fall through
  }
  return toYyyyMmDd(null)
}

function dateInputToNoonUtcIso(dateStr: string): string {
  return `${dateStr.trim()}T12:00:00.000Z`
}

type RowEdit = {
  selected: boolean
  receipt_id: string
  scanned_date: string
  /** When set and different from expected, apply this as unit price_sold. */
  use_received_total: boolean
}

export default function ReceivingReview() {
  const [searchParams] = useSearchParams()
  const token = (searchParams.get('token') || '').trim()
  const [session, setSession] = useState<ReceivingSession | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [edits, setEdits] = useState<Record<number, RowEdit>>({})
  const [applying, setApplying] = useState(false)
  const [applyError, setApplyError] = useState<string | null>(null)
  const [appliedIds, setAppliedIds] = useState<Set<number>>(new Set())

  useEffect(() => {
    if (!token) {
      setLoading(false)
      setError('Missing review token.')
      return
    }
    setLoading(true)
    api
      .get<ReceivingSession>(`/buying-groups/receiving-sessions/${encodeURIComponent(token)}`)
      .then((data) => {
        setSession(data)
        const next: Record<number, RowEdit> = {}
        for (const m of data.matches) {
          next[m.item_id] = {
            selected: m.selected !== false,
            receipt_id: m.receipt_id ?? '',
            scanned_date: toYyyyMmDd(m.scanned_at),
            use_received_total: false,
          }
        }
        setEdits(next)
      })
      .catch((e) => {
        console.error(e)
        setError(e instanceof Error ? e.message : 'Failed to load receiving review')
      })
      .finally(() => setLoading(false))
  }, [token])

  const matches = session?.matches ?? []
  const selectedCount = useMemo(
    () => matches.filter((m) => edits[m.item_id]?.selected && !appliedIds.has(m.item_id)).length,
    [matches, edits, appliedIds]
  )
  const discrepancyCount = useMemo(
    () => matches.filter((m) => m.amount_discrepancy && !appliedIds.has(m.item_id)).length,
    [matches, appliedIds]
  )

  const setRow = (itemId: number, patch: Partial<RowEdit>) => {
    setEdits((prev) => ({
      ...prev,
      [itemId]: { ...prev[itemId], ...patch },
    }))
  }

  const selectAll = (selected: boolean) => {
    setEdits((prev) => {
      const next = { ...prev }
      for (const m of matches) {
        if (appliedIds.has(m.item_id)) continue
        next[m.item_id] = { ...next[m.item_id], selected }
      }
      return next
    })
  }

  const applySelected = async () => {
    if (!session || applying) return
    const updates: Array<{
      item_id: number
      status: 'scanned'
      receipt_id: string | null
      scanned_at: string
      price_sold?: string | null
    }> = []

    for (const m of matches) {
      const edit = edits[m.item_id]
      if (!edit?.selected || appliedIds.has(m.item_id)) continue
      const entry: (typeof updates)[number] = {
        item_id: m.item_id,
        status: 'scanned',
        receipt_id: edit.receipt_id.trim() || null,
        scanned_at: dateInputToNoonUtcIso(edit.scanned_date),
      }
      if (edit.use_received_total && m.received_ext_total != null && m.quantity > 0) {
        const total = parseMoney(m.received_ext_total)
        if (total != null) {
          entry.price_sold = (total / m.quantity).toFixed(2)
        }
      }
      updates.push(entry)
    }

    if (updates.length === 0) return

    setApplying(true)
    setApplyError(null)
    try {
      await api.post<{ items: unknown }>('/items/bulk-update', { updates })
      setAppliedIds((prev) => {
        const next = new Set(prev)
        for (const u of updates) next.add(u.item_id)
        return next
      })
    } catch (e) {
      console.error(e)
      setApplyError(e instanceof Error ? e.message : 'Failed to apply scanned updates')
    } finally {
      setApplying(false)
    }
  }

  if (!token) {
    return (
      <div className="max-w-2xl mx-auto">
        <h1 className="text-xl font-semibold text-ink dark:text-gray-100 mb-2">Receiving review</h1>
        <p className="text-ink-muted dark:text-gray-400 mb-4">
          No session was attached to this link. Run <span className="font-medium">Check receiving</span> from a
          Parsefile buying group.
        </p>
        <Link to="/buying-groups" className="text-brand-600 dark:text-brand-400 hover:underline text-sm">
          Back to Buying Groups
        </Link>
      </div>
    )
  }

  if (loading) {
    return <p className="text-ink-muted dark:text-gray-400">Loading receiving review…</p>
  }

  if (error || !session) {
    return (
      <div className="max-w-2xl mx-auto">
        <h1 className="text-xl font-semibold text-ink dark:text-gray-100 mb-2">Receiving review</h1>
        <p className="text-sm text-red-600 dark:text-red-400 mb-4">{error || 'Session not found.'}</p>
        <Link to="/buying-groups" className="text-brand-600 dark:text-brand-400 hover:underline text-sm">
          Back to Buying Groups
        </Link>
      </div>
    )
  }

  const allApplied = matches.length > 0 && matches.every((m) => appliedIds.has(m.item_id))

  return (
    <div>
      <div className="flex flex-wrap items-start justify-between gap-4 mb-6">
        <div>
          <h1 className="text-xl font-semibold text-ink dark:text-gray-100">
            Receiving review — {session.buying_group_name}
          </h1>
          <p className="text-sm text-ink-muted dark:text-gray-400 mt-1">
            {matches.length} item{matches.length !== 1 ? 's' : ''} found scanned at Parsefile
            {session.not_found_count > 0 ? ` · ${session.not_found_count} still unscanned there` : ''}
            {session.void_skipped_count > 0 ? ` · ${session.void_skipped_count} voided` : ''}
            {discrepancyCount > 0 ? (
              <span className="text-amber-700 dark:text-amber-400">
                {' '}
                · {discrepancyCount} payment amount discrepanc{discrepancyCount === 1 ? 'y' : 'ies'}
              </span>
            ) : null}
          </p>
        </div>
        <Link
          to="/buying-groups"
          className="text-sm text-brand-600 dark:text-brand-400 hover:underline whitespace-nowrap"
        >
          Back to Buying Groups
        </Link>
      </div>

      {applyError && (
        <p className="mb-3 text-sm text-red-600 dark:text-red-400">{applyError}</p>
      )}

      {allApplied ? (
        <p className="mb-4 text-sm text-emerald-700 dark:text-emerald-400 rounded-lg border border-emerald-200 dark:border-emerald-800 bg-emerald-50/60 dark:bg-emerald-900/20 px-3 py-2">
          All listed items were marked scanned.
        </p>
      ) : (
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => selectAll(true)}
            className="rounded-md border border-brand-200 dark:border-gray-600 px-2 py-1 text-xs text-ink hover:bg-brand-50 dark:hover:bg-gray-700"
          >
            Select all
          </button>
          <button
            type="button"
            onClick={() => selectAll(false)}
            className="rounded-md border border-brand-200 dark:border-gray-600 px-2 py-1 text-xs text-ink hover:bg-brand-50 dark:hover:bg-gray-700"
          >
            Select none
          </button>
          <button
            type="button"
            onClick={applySelected}
            disabled={applying || selectedCount === 0}
            className="rounded-md bg-brand-600 text-white px-3 py-1.5 text-sm hover:bg-brand-700 disabled:opacity-50"
          >
            {applying
              ? 'Applying…'
              : `Mark ${selectedCount} scanned`}
          </button>
        </div>
      )}

      <div className="overflow-x-auto rounded-xl border border-brand-200/80 dark:border-gray-700">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left border-b border-brand-200 dark:border-gray-600 bg-brand-50/40 dark:bg-gray-800/60">
              <th className="py-2 px-2 w-10" />
              <th className="py-2 px-2 font-medium text-ink-muted">Order</th>
              <th className="py-2 px-2 font-medium text-ink-muted">Item</th>
              <th className="py-2 px-2 font-medium text-ink-muted">Tracking</th>
              <th className="py-2 px-2 font-medium text-ink-muted text-right">Expected</th>
              <th className="py-2 px-2 font-medium text-ink-muted text-right">Received</th>
              <th className="py-2 px-2 font-medium text-ink-muted min-w-[8rem]">Receipt</th>
              <th className="py-2 px-2 font-medium text-ink-muted">Scan date</th>
              <th className="py-2 px-2 font-medium text-ink-muted">Fix payout</th>
            </tr>
          </thead>
          <tbody>
            {matches.map((m) => {
              const edit = edits[m.item_id]
              const applied = appliedIds.has(m.item_id)
              const rowClass = applied
                ? 'bg-emerald-50/50 dark:bg-emerald-900/15'
                : m.amount_discrepancy
                  ? 'bg-amber-50/80 dark:bg-amber-900/25'
                  : ''
              return (
                <tr
                  key={m.item_id}
                  className={`border-b border-brand-100 dark:border-gray-700 last:border-0 ${rowClass}`}
                >
                  <td className="py-2 px-2 align-top">
                    <input
                      type="checkbox"
                      checked={!!edit?.selected && !applied}
                      disabled={applied}
                      onChange={(e) => setRow(m.item_id, { selected: e.target.checked })}
                      aria-label={`Select item ${m.description || m.item_id}`}
                    />
                  </td>
                  <td className="py-2 px-2 align-top">
                    <Link
                      to={`/?q=${encodeURIComponent(m.store_order_number || String(m.order_id))}`}
                      className="text-brand-600 dark:text-brand-400 hover:underline font-mono text-xs"
                    >
                      {m.store_order_number || `Order ${m.order_id}`}
                    </Link>
                  </td>
                  <td className="py-2 px-2 align-top text-ink">
                    <div>
                      {m.quantity}× {m.description || '—'}
                    </div>
                    {m.received_item && m.received_item !== m.description && (
                      <div className="text-xs text-ink-muted mt-0.5">Parsefile: {m.received_item}</div>
                    )}
                    {applied && (
                      <div className="text-xs text-emerald-700 dark:text-emerald-400 mt-0.5">Applied</div>
                    )}
                  </td>
                  <td className="py-2 px-2 align-top font-mono text-xs text-ink">{m.tracking_number}</td>
                  <td
                    className={`py-2 px-2 align-top text-right tabular-nums ${
                      m.amount_discrepancy ? 'text-amber-800 dark:text-amber-300 font-medium' : 'text-ink'
                    }`}
                  >
                    {fmtMoney(m.expected_amount)}
                  </td>
                  <td
                    className={`py-2 px-2 align-top text-right tabular-nums ${
                      m.amount_discrepancy ? 'text-amber-800 dark:text-amber-300 font-medium' : 'text-ink'
                    }`}
                  >
                    {fmtMoney(m.received_ext_total)}
                    {m.amount_discrepancy && (
                      <div className="text-[11px] font-normal text-amber-700 dark:text-amber-400">
                        mismatch
                      </div>
                    )}
                  </td>
                  <td className="py-2 px-2 align-top">
                    <input
                      type="text"
                      value={edit?.receipt_id ?? ''}
                      disabled={applied}
                      onChange={(e) => setRow(m.item_id, { receipt_id: e.target.value })}
                      className="w-full min-w-[7rem] h-8 rounded border border-brand-200 dark:border-gray-600 px-2 py-1 text-sm bg-white dark:bg-gray-700 disabled:opacity-60"
                      aria-label="Receipt ID"
                    />
                  </td>
                  <td className="py-2 px-2 align-top">
                    <input
                      type="date"
                      value={edit?.scanned_date ?? ''}
                      disabled={applied}
                      onChange={(e) => setRow(m.item_id, { scanned_date: e.target.value })}
                      className="h-8 rounded border border-brand-200 dark:border-gray-600 px-2 py-1 text-sm bg-white dark:bg-gray-700 disabled:opacity-60"
                      aria-label="Scan date"
                    />
                  </td>
                  <td className="py-2 px-2 align-top">
                    {m.amount_discrepancy && m.received_ext_total != null ? (
                      <label className="inline-flex items-center gap-1.5 text-xs text-ink">
                        <input
                          type="checkbox"
                          checked={!!edit?.use_received_total}
                          disabled={applied}
                          onChange={(e) => setRow(m.item_id, { use_received_total: e.target.checked })}
                        />
                        <span>Use received</span>
                      </label>
                    ) : (
                      <span className="text-ink-muted">—</span>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
