import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import type {
  BrowserImportLog,
  BrowserImportLogCategory,
  BrowserImportLogEvent,
  BrowserImportLogLevel,
} from '../api/types'

type LevelFilter = 'all' | BrowserImportLogLevel
type CategoryFilter = 'all' | BrowserImportLogCategory
type EventFilter = 'all' | BrowserImportLogEvent

function levelLabel(level: string): string {
  switch (level) {
    case 'updates':
      return 'Updates'
    case 'info':
      return 'Info'
    case 'error':
      return 'Error'
    default:
      return level
  }
}

function categoryLabel(category: string): string {
  switch (category) {
    case 'stores':
      return 'Stores'
    case 'groups':
      return 'Groups'
    default:
      return category
  }
}

function eventLabel(eventType: string): string {
  switch (eventType) {
    case 'order_imported':
      return 'Order imported'
    case 'tracking_updated':
      return 'Tracking updated'
    case 'order_checked':
      return 'Order checked'
    case 'order_marked_personal':
      return 'Marked personal'
    case 'order_skipped_ignored_zip':
      return 'Skipped (ignored zip)'
    case 'order_error':
      return 'Order error'
    case 'check_started':
      return 'Check started'
    case 'check_finished':
      return 'Check finished'
    case 'tracking_submit_started':
      return 'Tracking submit started'
    case 'tracking_submitted':
      return 'Tracking submitted'
    case 'tracking_submit_error':
      return 'Tracking submit error'
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
    case 'tracking_submit':
      return 'Tracking submit'
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

function levelBadgeClass(level: string): string {
  if (level === 'updates') {
    return 'bg-brand-100 text-brand-800 dark:bg-gray-700 dark:text-brand-400'
  }
  if (level === 'error') {
    return 'bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300'
  }
  return 'bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-200'
}

function parseCategoryParam(raw: string | null): CategoryFilter {
  if (raw === 'stores' || raw === 'groups') return raw
  return 'all'
}

export default function BrowserImportLogPage() {
  const [searchParams] = useSearchParams()
  const [rows, setRows] = useState<BrowserImportLog[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [categoryFilter, setCategoryFilter] = useState<CategoryFilter>(() =>
    parseCategoryParam(searchParams.get('category'))
  )
  const [levelFilter, setLevelFilter] = useState<LevelFilter>('all')
  const [eventFilter, setEventFilter] = useState<EventFilter>('all')
  const [scheduledOnly, setScheduledOnly] = useState(false)

  useEffect(() => {
    let cancelled = false
    let firstLoad = true

    const load = () => {
      if (firstLoad) {
        setLoading(true)
        setError(null)
      }
      const params = new URLSearchParams()
      params.set('limit', '300')
      if (categoryFilter !== 'all') params.set('category', categoryFilter)
      if (levelFilter !== 'all') params.set('level', levelFilter)
      if (eventFilter !== 'all') params.set('event_type', eventFilter)
      if (scheduledOnly) params.set('scheduled_only', 'true')
      return api
        .get<BrowserImportLog[]>(`/browser-profiles/import-logs?${params}`)
        .then((data) => {
          if (!cancelled) setRows(data)
        })
        .catch((err) => {
          console.error(err)
          if (!cancelled && firstLoad) {
            setError(err instanceof Error ? err.message : 'Failed to load import log')
          }
        })
        .finally(() => {
          if (!cancelled && firstLoad) {
            setLoading(false)
            firstLoad = false
          }
        })
    }

    void load()
    // Live imports commit per order; poll so new rows appear without a manual refresh.
    const intervalId = window.setInterval(() => {
      void load()
    }, 3000)

    return () => {
      cancelled = true
      window.clearInterval(intervalId)
    }
  }, [categoryFilter, levelFilter, eventFilter, scheduledOnly])

  return (
    <div className="w-full">
      <div className="flex flex-wrap items-start justify-between gap-4 mb-2">
        <h1 className="text-2xl font-semibold text-ink dark:text-gray-100">Import log</h1>
        <div className="flex flex-wrap gap-3 text-sm">
          <Link
            to="/browser-automation"
            className="text-brand-600 dark:text-brand-400 hover:underline"
          >
            Browser automation
          </Link>
          <Link
            to="/buying-groups"
            className="text-brand-600 dark:text-brand-400 hover:underline"
          >
            Buying groups
          </Link>
        </div>
      </div>
      <p className="text-sm text-ink-muted dark:text-gray-400 mb-6 max-w-2xl">
        Stores covers browser import checks. Groups covers buying-group tracking submissions.
        Levels: Updates (changes), Info (no-op / start-stop), Error (failures).
      </p>

      <div className="flex flex-wrap items-center gap-3 mb-4">
        <label className="text-sm text-ink dark:text-gray-200">
          <span className="sr-only">Category</span>
          <select
            className="rounded-lg border border-brand-200 dark:border-gray-600 dark:bg-gray-800 px-3 py-1.5 text-sm"
            value={categoryFilter}
            onChange={(e) => setCategoryFilter(e.target.value as CategoryFilter)}
          >
            <option value="all">All categories</option>
            <option value="stores">Stores</option>
            <option value="groups">Groups</option>
          </select>
        </label>
        <label className="text-sm text-ink dark:text-gray-200">
          <span className="sr-only">Level</span>
          <select
            className="rounded-lg border border-brand-200 dark:border-gray-600 dark:bg-gray-800 px-3 py-1.5 text-sm"
            value={levelFilter}
            onChange={(e) => setLevelFilter(e.target.value as LevelFilter)}
          >
            <option value="all">All levels</option>
            <option value="updates">Updates</option>
            <option value="info">Info</option>
            <option value="error">Error</option>
          </select>
        </label>
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
            <option value="order_checked">Order checked</option>
            <option value="order_marked_personal">Marked personal</option>
            <option value="order_skipped_ignored_zip">Skipped (ignored zip)</option>
            <option value="order_error">Order error</option>
            <option value="check_started">Check started</option>
            <option value="check_finished">Check finished</option>
            <option value="tracking_submit_started">Tracking submit started</option>
            <option value="tracking_submitted">Tracking submitted</option>
            <option value="tracking_submit_error">Tracking submit error</option>
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
                  <th className="text-left py-3 px-4 text-sm font-medium text-ink">Category</th>
                  <th className="text-left py-3 px-4 text-sm font-medium text-ink">Level</th>
                  <th className="text-left py-3 px-4 text-sm font-medium text-ink">Event</th>
                  <th className="text-left py-3 px-4 text-sm font-medium text-ink">Order</th>
                  <th className="text-left py-3 px-4 text-sm font-medium text-ink">Details</th>
                  <th className="text-left py-3 px-4 text-sm font-medium text-ink">Account</th>
                  <th className="text-left py-3 px-4 text-sm font-medium text-ink">Check</th>
                  <th className="text-left py-3 px-4 text-sm font-medium text-ink">Source</th>
                </tr>
              </thead>
              <tbody>
                {rows.length === 0 ? (
                  <tr>
                    <td colSpan={9} className="py-12 text-center text-ink-muted">
                      No activity logged yet.
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
                      <td className="py-3 px-4 text-sm text-ink dark:text-gray-200 whitespace-nowrap">
                        {categoryLabel(row.category || 'stores')}
                      </td>
                      <td className="py-3 px-4">
                        <span
                          className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${levelBadgeClass(row.level)}`}
                        >
                          {levelLabel(row.level)}
                        </span>
                      </td>
                      <td className="py-3 px-4 text-sm text-ink dark:text-gray-200">
                        {eventLabel(row.event_type)}
                      </td>
                      <td className="py-3 px-4 font-medium text-brand-700 dark:text-brand-400">
                        {row.store_order_number || '—'}
                      </td>
                      <td className="py-3 px-4 text-sm text-ink dark:text-gray-200">
                        {row.tracking_numbers.length > 0 ? (
                          <div className="space-y-1">
                            {row.message && (
                              <div
                                className={
                                  row.level === 'error'
                                    ? 'text-red-700 dark:text-red-300 break-words'
                                    : 'text-ink-muted'
                                }
                              >
                                {row.message}
                              </div>
                            )}
                            <ul className="space-y-0.5">
                              {row.tracking_numbers.map((t) => (
                                <li key={t} className="font-mono text-xs">
                                  {t}
                                </li>
                              ))}
                            </ul>
                          </div>
                        ) : row.message ? (
                          <span
                            className={
                              row.level === 'error'
                                ? 'text-red-700 dark:text-red-300 break-words'
                                : 'text-ink-muted'
                            }
                          >
                            {row.message}
                          </span>
                        ) : (
                          <span className="text-ink-muted">—</span>
                        )}
                      </td>
                      <td className="py-3 px-4 text-sm text-ink dark:text-gray-200">
                        <div>{row.store_name ?? (row.retailer || '—')}</div>
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
