import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'

interface ReceivingMatch {
  item_id: number
  order_id: number
  store_order_number: string | null
  description: string | null
  quantity: number
  tracking_number: string
  price_sold: string
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
  row_id?: string | null
  match_kind?: 'received' | 'not_received' | string
  original_quantity?: number | null
  split_keep_quantity?: number | null
  update_receipt_only?: boolean
}

interface ReceivingUnmatched {
  item_id: number
  order_id: number
  store_order_number: string | null
  description: string | null
  quantity: number
  status: string | null
  tracking_numbers: string[]
  expected_amount: string | null
  submitted_at: string | null
  void_matched?: boolean
}

interface ReceivingUnknownReceipt {
  receipt_id: string | null
  tracking_number: string
  bg_order_id: string | null
  description: string | null
  quantity: number | null
  received_price: string | null
  received_ext_total: string | null
  received_status: string | null
  note: string | null
  created_dt: string | null
  scanned_at: string | null
  delivery: string | null
}

interface ReceivingSession {
  buying_group_id: number
  buying_group_name: string
  created_at: string | null
  not_found_count: number
  void_skipped_count: number
  searched_trackings: string[]
  raw_csv: string
  csv_row_count: number
  unmatched: ReceivingUnmatched[]
  unknown_receipts?: ReceivingUnknownReceipt[]
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
  use_received_total: boolean
}

function rowKey(m: ReceivingMatch) {
  return m.row_id || `${m.item_id}:${m.match_kind || 'received'}`
}

function buildEdits(matches: ReceivingMatch[]): Record<string, RowEdit> {
  const next: Record<string, RowEdit> = {}
  for (const m of matches) {
    const key = rowKey(m)
    const isNotReceived = m.match_kind === 'not_received'
    next[key] = {
      selected: isNotReceived ? false : m.selected !== false,
      receipt_id: m.receipt_id ?? '',
      scanned_date: toYyyyMmDd(m.scanned_at),
      use_received_total: false,
    }
  }
  return next
}

function ReceivingGroupSection({
  session,
  highlighted,
  edits,
  setEdits,
  appliedIds,
  setAppliedIds,
}: {
  session: ReceivingSession
  highlighted: boolean
  edits: Record<string, RowEdit>
  setEdits: React.Dispatch<React.SetStateAction<Record<string, RowEdit>>>
  appliedIds: Set<string>
  setAppliedIds: React.Dispatch<React.SetStateAction<Set<string>>>
}) {
  const [applying, setApplying] = useState(false)
  const [applyError, setApplyError] = useState<string | null>(null)
  const [showExtra, setShowExtra] = useState(false)
  const [showCsv, setShowCsv] = useState(false)
  const [collapsed, setCollapsed] = useState(false)

  const matches = session.matches ?? []
  const receivableMatches = matches.filter((m) => m.match_kind !== 'not_received')
  const splitOffCount = matches.filter((m) => m.match_kind === 'not_received').length
  const remainingToApply = useMemo(
    () => receivableMatches.filter((m) => !appliedIds.has(rowKey(m))).length,
    [receivableMatches, appliedIds]
  )
  const appliedCount = receivableMatches.length - remainingToApply

  const selectedCount = useMemo(
    () =>
      matches.filter(
        (m) =>
          m.match_kind !== 'not_received' &&
          edits[rowKey(m)]?.selected &&
          !appliedIds.has(rowKey(m))
      ).length,
    [matches, edits, appliedIds]
  )

  const discrepancyCount = useMemo(
    () =>
      matches.filter(
        (m) => m.amount_discrepancy && m.match_kind !== 'not_received' && !appliedIds.has(rowKey(m))
      ).length,
    [matches, appliedIds]
  )

  const setRow = (key: string, patch: Partial<RowEdit>) => {
    setEdits((prev) => ({
      ...prev,
      [key]: { ...prev[key], ...patch },
    }))
  }

  const selectAll = (selected: boolean) => {
    setEdits((prev) => {
      const next = { ...prev }
      for (const m of matches) {
        if (m.match_kind === 'not_received') continue
        const key = rowKey(m)
        if (appliedIds.has(key)) continue
        next[key] = { ...next[key], selected }
      }
      return next
    })
  }

  const applySelected = async () => {
    if (applying) return
    const updates: Array<{
      item_id: number
      status?: 'scanned'
      receipt_id: string | null
      scanned_at?: string
      price_sold?: string | null
      warehouse_tracking?: string | null
    }> = []
    const appliedKeys: string[] = []
    const appliedItemIds: number[] = []

    setApplying(true)
    setApplyError(null)
    try {
      const liveItemId = new Map<number, number>()

      for (const m of matches) {
        if (m.match_kind === 'not_received') continue
        const key = rowKey(m)
        const edit = edits[key]
        if (!edit?.selected || appliedIds.has(key)) continue

        let itemId = liveItemId.get(m.item_id) ?? m.item_id

        if (m.split_keep_quantity != null && m.split_keep_quantity > 0) {
          const { split_off } = await api.post<{
            kept: { id: number }
            split_off: { id: number }
          }>(`/items/${itemId}/split`, {
            keep_quantity: m.split_keep_quantity,
            // Under-received remainder keeps the same tracking and needs review;
            // full receiving checks skip needs_review so they don't overwrite it.
            copy_tracking: true,
            split_off_status: 'needs_review',
          })
          liveItemId.set(m.item_id, split_off.id)
        }

        const entry: (typeof updates)[number] = {
          item_id: itemId,
          receipt_id: edit.receipt_id.trim() || null,
        }
        if (!m.update_receipt_only) {
          entry.status = 'scanned'
          entry.scanned_at = dateInputToNoonUtcIso(edit.scanned_date)
        } else if (edit.scanned_date) {
          entry.scanned_at = dateInputToNoonUtcIso(edit.scanned_date)
        }
        if (edit.use_received_total && m.received_ext_total != null && m.quantity > 0) {
          const total = parseMoney(m.received_ext_total)
          if (total != null) {
            entry.price_sold = (total / m.quantity).toFixed(2)
          }
        }
        // Persist D&R-scanned LPN / alternate TN on shipment notes when new.
        const scannedTn = (m.tracking_number || '').trim()
        if (scannedTn) {
          entry.warehouse_tracking = scannedTn
        }
        updates.push(entry)
        appliedKeys.push(key)
        appliedItemIds.push(m.item_id)
      }

      if (updates.length === 0) return

      await api.post<{ items: unknown }>('/items/bulk-update', { updates })
      // Prune backend pending snapshot; keep rows visible here as applied/inactive.
      await api.post<{ deleted: boolean }>('/buying-groups/receiving-review/prune', {
        buying_group_id: session.buying_group_id,
        item_ids: [...new Set(appliedItemIds)],
      })

      setAppliedIds((prev) => {
        const next = new Set(prev)
        for (const key of appliedKeys) next.add(key)
        for (const m of matches) {
          if (
            m.match_kind === 'not_received' &&
            appliedKeys.some((k) => k.startsWith(`${m.item_id}:`))
          ) {
            next.add(rowKey(m))
          }
        }
        return next
      })
    } catch (e) {
      console.error(e)
      setApplyError(e instanceof Error ? e.message : 'Failed to apply scanned updates')
    } finally {
      setApplying(false)
    }
  }

  const allApplied =
    receivableMatches.length > 0 && receivableMatches.every((m) => appliedIds.has(rowKey(m)))
  const unmatched = session.unmatched ?? []
  const unknownReceipts = session.unknown_receipts ?? []

  return (
    <section
      className={`mb-10 rounded-xl border transition-shadow ${
        highlighted
          ? 'border-brand-500 ring-2 ring-brand-400/60 dark:ring-brand-500/50'
          : 'border-brand-200/80 dark:border-gray-700'
      }`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3 px-4 py-3 border-b border-brand-100 dark:border-gray-700 bg-brand-50/30 dark:bg-gray-800/40">
        <div className="min-w-0">
          <button
            type="button"
            onClick={() => setCollapsed((v) => !v)}
            className="text-left font-semibold text-ink dark:text-gray-100 hover:text-brand-600 dark:hover:text-brand-400"
          >
            {session.buying_group_name}
            <span className="ml-2 text-sm font-normal text-ink-muted">
              ({remainingToApply} to apply
              {appliedCount > 0 ? ` · ${appliedCount} applied` : ''})
            </span>
          </button>
          <p className="text-sm text-ink-muted dark:text-gray-400 mt-1">
            {receivableMatches.length} matched received
            {splitOffCount > 0 ? ` · ${splitOffCount} under-received split-off` : ''}
            {session.not_found_count > 0 ? ` · ${session.not_found_count} local still unmatched` : ''}
            {discrepancyCount > 0 ? (
              <span className="text-amber-700 dark:text-amber-400">
                {' '}
                · {discrepancyCount} amount discrepanc{discrepancyCount === 1 ? 'y' : 'ies'}
              </span>
            ) : null}
          </p>
        </div>
        <button
          type="button"
          onClick={() => setCollapsed((v) => !v)}
          className="text-xs text-brand-600 dark:text-brand-400 hover:underline shrink-0"
        >
          {collapsed ? 'Expand' : 'Collapse'}
        </button>
      </div>

      {!collapsed && (
        <div className="p-4">
          {applyError && <p className="mb-3 text-sm text-red-600 dark:text-red-400">{applyError}</p>}

          {matches.length > 0 && (
            <>
              {allApplied ? (
                <p className="mb-4 text-sm text-emerald-700 dark:text-emerald-400 rounded-lg border border-emerald-200 dark:border-emerald-800 bg-emerald-50/60 dark:bg-emerald-900/20 px-3 py-2">
                  All listed rows were applied.
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
                    {applying ? 'Applying…' : `Apply ${selectedCount} selected`}
                  </button>
                </div>
              )}

              <div className="overflow-x-auto rounded-xl border border-brand-200/80 dark:border-gray-700 mb-4">
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
                      const key = rowKey(m)
                      const edit = edits[key]
                      const applied = appliedIds.has(key)
                      const isNotReceived = m.match_kind === 'not_received'
                      const rowClass = applied
                        ? 'bg-emerald-50/50 dark:bg-emerald-900/15 opacity-60'
                        : isNotReceived
                          ? 'bg-slate-50/80 dark:bg-gray-800/40'
                          : m.amount_discrepancy
                            ? 'bg-amber-50/80 dark:bg-amber-900/25'
                            : ''
                      return (
                        <tr
                          key={key}
                          className={`border-b border-brand-100 dark:border-gray-700 last:border-0 ${rowClass}`}
                        >
                          <td className="py-2 px-2 align-top">
                            {isNotReceived ? (
                              <span className="text-ink-muted text-xs">—</span>
                            ) : (
                              <input
                                type="checkbox"
                                checked={!!edit?.selected && !applied}
                                disabled={applied}
                                onChange={(e) => setRow(key, { selected: e.target.checked })}
                                aria-label={`Select item ${m.description || m.item_id}`}
                              />
                            )}
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
                            {isNotReceived ? (
                              <div className="text-xs text-ink-muted mt-0.5">
                                Not received — remainder keeps tracking and becomes needs review after apply
                              </div>
                            ) : (
                              <>
                                {m.update_receipt_only && (
                                  <div className="text-xs text-ink-muted mt-0.5">
                                    Already scanned — update receipt ID only
                                  </div>
                                )}
                                {applied && (
                                  <div className="text-xs text-emerald-700 dark:text-emerald-400 mt-0.5">
                                    Applied
                                  </div>
                                )}
                              </>
                            )}
                          </td>
                          <td className="py-2 px-2 align-top font-mono text-xs text-ink">{m.tracking_number}</td>
                          <td className="py-2 px-2 align-top text-right tabular-nums text-ink">
                            {fmtMoney(m.expected_amount)}
                          </td>
                          <td className="py-2 px-2 align-top text-right tabular-nums text-ink">
                            {isNotReceived ? '—' : fmtMoney(m.received_ext_total)}
                          </td>
                          <td className="py-2 px-2 align-top">
                            {isNotReceived ? (
                              <span className="text-ink-muted">—</span>
                            ) : (
                              <input
                                type="text"
                                value={edit?.receipt_id ?? ''}
                                disabled={applied}
                                onChange={(e) => setRow(key, { receipt_id: e.target.value })}
                                className="w-full min-w-[7rem] h-8 rounded border border-brand-200 dark:border-gray-600 px-2 py-1 text-sm bg-white dark:bg-gray-700 disabled:opacity-60"
                                aria-label="Receipt ID"
                              />
                            )}
                          </td>
                          <td className="py-2 px-2 align-top">
                            {isNotReceived ? (
                              <span className="text-ink-muted">—</span>
                            ) : (
                              <input
                                type="date"
                                value={edit?.scanned_date ?? ''}
                                disabled={applied}
                                onChange={(e) => setRow(key, { scanned_date: e.target.value })}
                                className="h-8 rounded border border-brand-200 dark:border-gray-600 px-2 py-1 text-sm bg-white dark:bg-gray-700 disabled:opacity-60"
                                aria-label="Scan date"
                              />
                            )}
                          </td>
                          <td className="py-2 px-2 align-top">
                            {!isNotReceived && m.amount_discrepancy && m.received_ext_total != null ? (
                              <label className="inline-flex items-center gap-1.5 text-xs text-ink">
                                <input
                                  type="checkbox"
                                  checked={!!edit?.use_received_total}
                                  disabled={applied}
                                  onChange={(e) => setRow(key, { use_received_total: e.target.checked })}
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
            </>
          )}

          {(unmatched.length > 0 || unknownReceipts.length > 0 || session.raw_csv) && (
            <div className="mb-2">
              <button
                type="button"
                onClick={() => setShowExtra((v) => !v)}
                className="text-xs text-brand-600 dark:text-brand-400 hover:underline"
              >
                {showExtra ? 'Hide' : 'Show'} unmatched, unknown receipts, and API raw response
              </button>
            </div>
          )}

          {showExtra && unknownReceipts.length > 0 && (
            <div className="mb-4 text-sm text-ink-muted">
              {unknownReceipts.length} warehouse receipt(s) not in Order Manager (informational).
            </div>
          )}

          {showExtra && unmatched.length > 0 && (
            <div className="mb-4 text-sm text-ink-muted">
              {unmatched.length} local item(s) not matched as received.
            </div>
          )}

          {showExtra && session.raw_csv && (
            <div className="mb-2">
              <button
                type="button"
                onClick={() => setShowCsv((v) => !v)}
                className="text-xs text-brand-600 dark:text-brand-400 hover:underline"
              >
                {showCsv ? 'Hide' : 'Show'} raw API response
              </button>
              {showCsv && (
                <pre className="mt-2 text-xs font-mono whitespace-pre-wrap break-all rounded-xl border border-brand-200/80 dark:border-gray-700 bg-brand-50/30 dark:bg-gray-900/40 p-3 max-h-[20rem] overflow-auto text-ink">
                  {session.raw_csv.trim() || '(empty response body)'}
                </pre>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  )
}

export default function ReceivingReview() {
  const [searchParams] = useSearchParams()
  const highlightRaw = (searchParams.get('highlight') || '').trim()
  const highlightGroupId = highlightRaw ? parseInt(highlightRaw, 10) : NaN
  const [sessions, setSessions] = useState<ReceivingSession[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [editsByGroup, setEditsByGroup] = useState<Record<number, Record<string, RowEdit>>>({})
  const [appliedByGroup, setAppliedByGroup] = useState<Record<number, Set<string>>>({})
  const [flashGroupId, setFlashGroupId] = useState<number | null>(null)
  const sectionRefs = useRef<Record<number, HTMLElement | null>>({})

  const loadSessions = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await api.get<ReceivingSession[]>('/buying-groups/receiving-review')
      setSessions(data)
      const edits: Record<number, Record<string, RowEdit>> = {}
      for (const s of data) {
        edits[s.buying_group_id] = buildEdits(s.matches)
      }
      setEditsByGroup(edits)
      setAppliedByGroup({})
    } catch (e) {
      console.error(e)
      setError(e instanceof Error ? e.message : 'Failed to load receiving review')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadSessions()
  }, [loadSessions, highlightRaw])

  useEffect(() => {
    if (!Number.isFinite(highlightGroupId) || loading) return
    setFlashGroupId(highlightGroupId)
    const el = sectionRefs.current[highlightGroupId]
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
    const t = window.setTimeout(() => setFlashGroupId(null), 4000)
    return () => window.clearTimeout(t)
  }, [highlightGroupId, loading, sessions])

  if (loading) {
    return <p className="text-ink-muted dark:text-gray-400">Loading receiving review…</p>
  }

  if (error) {
    return (
      <div className="max-w-2xl mx-auto">
        <h1 className="text-xl font-semibold text-ink dark:text-gray-100 mb-2">Receiving</h1>
        <p className="text-sm text-red-600 dark:text-red-400 mb-4">{error}</p>
        <Link to="/settings/buying-groups" className="text-brand-600 dark:text-brand-400 hover:underline text-sm">
          Back to Buying Groups
        </Link>
      </div>
    )
  }

  return (
    <div>
      <div className="flex flex-wrap items-start justify-between gap-4 mb-6">
        <div>
          <h1 className="text-xl font-semibold text-ink dark:text-gray-100">Receiving</h1>
          <p className="text-sm text-ink-muted dark:text-gray-400 mt-1">
            Warehouse matches grouped by buying group. Apply to mark items scanned with receipt IDs.
          </p>
        </div>
        <Link
          to="/settings/buying-groups"
          className="text-sm text-brand-600 dark:text-brand-400 hover:underline whitespace-nowrap"
        >
          Buying Groups
        </Link>
      </div>

      {sessions.length === 0 ? (
        <div className="rounded-xl border border-brand-200/80 dark:border-gray-700 px-4 py-8 text-center">
          <p className="text-ink-muted dark:text-gray-400 mb-3">No pending received updates.</p>
          <p className="text-sm text-ink-muted dark:text-gray-500 mb-4">
            Run <span className="font-medium">Check receiving</span> on a buying group or enable auto-check
            on a schedule.
          </p>
          <Link to="/settings/buying-groups" className="text-brand-600 dark:text-brand-400 hover:underline text-sm">
            Go to Buying Groups
          </Link>
        </div>
      ) : (
        sessions.map((session) => (
          <div
            key={session.buying_group_id}
            ref={(el) => {
              sectionRefs.current[session.buying_group_id] = el
            }}
          >
            <ReceivingGroupSection
              session={session}
              highlighted={flashGroupId === session.buying_group_id}
              edits={editsByGroup[session.buying_group_id] ?? {}}
              setEdits={(updater) => {
                setEditsByGroup((prev) => {
                  const current = prev[session.buying_group_id] ?? {}
                  const nextEdits =
                    typeof updater === 'function' ? updater(current) : updater
                  return { ...prev, [session.buying_group_id]: nextEdits }
                })
              }}
              appliedIds={appliedByGroup[session.buying_group_id] ?? new Set()}
              setAppliedIds={(updater) => {
                setAppliedByGroup((prev) => {
                  const current = prev[session.buying_group_id] ?? new Set<string>()
                  const nextApplied =
                    typeof updater === 'function' ? updater(current) : updater
                  return { ...prev, [session.buying_group_id]: nextApplied }
                })
              }}
            />
          </div>
        ))
      )}
    </div>
  )
}
