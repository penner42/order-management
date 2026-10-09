import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { BrowserImportLog, BrowserImportLogEvent } from '../api/types'

type EventFilter = 'all' | BrowserImportLogEvent

function eventLabel(eventType: string): string {
  switch (eventType) {
    case 'order_imported':
      return 'Order imported'
    case 'tracking_updated':
      return 'Tracking updated'
    default:
      return eventType
  }
}

function modeLabel(mode: string): string {
  switch (mode) {
    case 'full':
      return 'Full check'
    case 'unshipped':
      return 'Unshipped check'
    default:
      return mode
  }
}

function formatWhen(iso: string | null): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return '—'
  }
}

export default function BrowserImportLogPage() {
  const [rows, setRows] = useState<BrowserImportLog[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [eventFilter, setEventFilter] = useState<EventFilter>('all')
  const [scheduledOnly, setScheduledOnly] = useState(false)

  useEffect(() => {
    setLoading(true)
    setError(null)
    const params = new URLSearchParams()
    params.set('limit', '300')
    if (eventFilter !== 'all') params.set('event_type', eventFilter)
    if (scheduledOnly) params.set('scheduled_only', 'true')
    api
      .get<BrowserImportLog[]>(`/browser-profiles/import-logs?${params}`)
      .then(setRows)
      .catch((err) => {
        console.error(err)
        setError(err instanceof Error ? err.message : 'Failed to load import log')
      })
      .finally(() => setLoading(false))
  }, [eventFilter, scheduledOnly])

  return (
    <div className="max-w-5xl">
      <div className="flex flex-wrap items-start justify-between gap-4 mb-2">
        <h1 className="text-2xl font-semibold text-ink dark:text-gray-100">Browser import log</h1>
        <Link
          to="/browser-automation"
          className="text-sm text-brand-600 dark:text-brand-400 hover:underline"
        >
          Back to browser automation
        </Link>
      </div>
      <p className="text-sm text-ink-muted dark:text-gray-400 mb-6 max-w-2xl">
        Orders created or updated with tracking numbers by scheduled checks and Run now auto-apply.
      </p>

      <div className="flex flex-wrap items-center gap-3 mb-4">
        <label className="text-sm text-ink dark:text-gray-200">
          <span className="sr-only">Event type</span>
          <select
            className="rounded-lg border border-brand-200 dark:border-gray-600 dark:bg-gray-800 px-3 py-1.5 text-sm"
            value={eventFilter}
            onChange={(e) => setEventFilter(e.target.value as EventFilter)}
          >
            <option value="all">All events</option>
            <option value="order_imported">Order imported</option>
            <option value="tracking_updated">Tracking updated</option>
          </select>
        </label>
        <label className="inline-flex items-center gap-2 text-sm text-ink dark:text-gray-200">
          <input
            type="checkbox"
            checked={scheduledOnly}
            onChange={(e) => setScheduledOnly(e.target.checked)}
            className="rounded border-brand-300"
          />
          Scheduled only
        </label>
      </div>

      {error && (
        <p className="mb-4 text-sm text-red-600 dark:text-red-400">{error}</p>
      )}

      {loading ? (
        <p className="text-ink-muted dark:text-gray-400">Loading…</p>
      ) : (
        <div className="w-full bg-white dark:bg-gray-800 rounded-xl border border-brand-200/80 dark:border-gray-700 shadow-sm overflow-hidden">
          <div className="w-full overflow-x-auto">
            <table className="min-w-full">
              <thead className="bg-brand-100/50 dark:bg-gray-700/50 border-b border-brand-200/80 dark:border-gray-700">
                <tr>
                  <th className="text-left py-3 px-4 text-sm font-medium text-ink">When</th>
                  <th className="text-left py-3 px-4 text-sm font-medium text-ink">Event</th>
                  <th className="text-left py-3 px-4 text-sm font-medium text-ink">Order</th>
                  <th className="text-left py-3 px-4 text-sm font-medium text-ink">Tracking</th>
                  <th className="text-left py-3 px-4 text-sm font-medium text-ink">Account</th>
                  <th className="text-left py-3 px-4 text-sm font-medium text-ink">Check</th>
                  <th className="text-left py-3 px-4 text-sm font-medium text-ink">Source</th>
                </tr>
              </thead>
              <tbody>
                {rows.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="py-12 text-center text-ink-muted">
                      No import activity logged yet.
                    </td>
                  </tr>
                ) : (
                  rows.map((row) => (
                    <tr
                      key={row.id}
                      className="border-b border-brand-100 last:border-0 hover:bg-brand-50/50 dark:hover:bg-gray-600"
                    >
                      <td className="py-3 px-4 text-sm text-ink-muted whitespace-nowrap">
                        {formatWhen(row.created_at)}
                      </td>
                      <td className="py-3 px-4 text-sm text-ink dark:text-gray-200">
                        {eventLabel(row.event_type)}
                      </td>
                      <td className="py-3 px-4 font-medium text-brand-700 dark:text-brand-400">
                        {row.store_order_number || '—'}
                      </td>
                      <td className="py-3 px-4 text-sm text-ink dark:text-gray-200">
                        {row.tracking_numbers.length > 0 ? (
                          <ul className="space-y-0.5">
                            {row.tracking_numbers.map((t) => (
                              <li key={t} className="font-mono text-xs">
                                {t}
                              </li>
                            ))}
                          </ul>
                        ) : (
                          <span className="text-ink-muted">—</span>
                        )}
                      </td>
                      <td className="py-3 px-4 text-sm text-ink dark:text-gray-200">
                        <div>{row.store_name ?? row.retailer}</div>
                        {row.store_account_name && (
                          <div className="text-xs text-ink-muted">{row.store_account_name}</div>
                        )}
                      </td>
                      <td className="py-3 px-4 text-sm text-ink-muted whitespace-nowrap">
                        {modeLabel(row.mode)}
                      </td>
                      <td className="py-3 px-4 text-sm text-ink-muted whitespace-nowrap">
                        {row.scheduled ? 'Scheduled' : 'Run now'}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
