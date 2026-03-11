import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { StoreOrderImport, StoreOrderImportListResponse } from '../api/types'

export default function StoreImports() {
  const [imports, setImports] = useState<StoreOrderImport[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    setError(null)
    api
      .get<StoreOrderImportListResponse>('/integrations/stores/imports')
      .then((res) => setImports(res.imports))
      .catch((err: unknown) => {
        console.error(err)
        setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => setLoading(false))
  }, [])

  if (loading) {
    return (
      <div className="max-w-4xl">
        <p className="text-ink-muted dark:text-gray-400">Loading…</p>
      </div>
    )
  }

  return (
    <div className="max-w-4xl">
      <div className="flex items-center justify-between gap-4 mb-6">
        <h1 className="text-xl font-semibold text-ink dark:text-gray-100">
          Store Imports
        </h1>
        <Link
          to="/"
          className="text-sm text-brand-600 dark:text-brand-400 hover:underline"
        >
          Back to Orders
        </Link>
      </div>

      <p className="text-sm text-ink-muted dark:text-gray-400 mb-4">
        Pending store order imports captured from the browser extension (e.g. Walmart).
        Review and apply updates into your orders.
      </p>

      {error && (
        <p className="mb-3 text-sm text-red-600 dark:text-red-400">
          {error}
        </p>
      )}

      <div className="w-fit min-w-[720px] max-w-full bg-white dark:bg-gray-800 rounded-xl border border-brand-200/80 dark:border-gray-700 shadow-sm overflow-hidden">
        <table className="min-w-0">
          <thead className="bg-brand-100/50 dark:bg-gray-700/50 border-b border-brand-200/80 dark:border-gray-700">
            <tr>
              <th className="text-left py-3 px-4 text-sm font-medium text-ink">Store</th>
              <th className="text-left py-3 px-4 text-sm font-medium text-ink">
                Store order id
              </th>
              <th className="text-left py-3 px-4 text-sm font-medium text-ink">
                Status
              </th>
              <th className="text-left py-3 px-4 text-sm font-medium text-ink">
                Captured
              </th>
              <th className="text-left py-3 px-4 text-sm font-medium text-ink">
                Actions
              </th>
            </tr>
          </thead>
          <tbody>
            {imports.length === 0 ? (
              <tr>
                <td colSpan={5} className="py-12 text-center text-ink-muted">
                  No pending store imports.
                </td>
              </tr>
            ) : (
              imports.map((imp) => (
                <tr
                  key={imp.id}
                  className="border-b border-brand-100 last:border-0 hover:bg-brand-50/50 dark:hover:bg-gray-600"
                >
                  <td className="py-3 px-4 text-sm text-ink dark:text-gray-200">
                    {imp.store || '—'}
                  </td>
                  <td className="py-3 px-4 text-sm text-ink dark:text-gray-200">
                    {imp.external_order_id || '—'}
                  </td>
                  <td className="py-3 px-4 text-xs font-medium uppercase tracking-wide text-ink-muted dark:text-gray-400">
                    {imp.status}
                  </td>
                  <td className="py-3 px-4 text-sm text-ink-muted">
                    {imp.created_at ? new Date(imp.created_at).toLocaleString() : '—'}
                  </td>
                  <td className="py-3 px-4 text-right">
                    <Link
                      to={`/store-imports/${imp.id}`}
                      aria-label="Review import"
                      className="inline-flex items-center justify-center p-1.5 rounded-md text-ink-muted hover:text-ink hover:bg-brand-100/70 dark:text-gray-400 dark:hover:text-gray-100 dark:hover:bg-gray-700 transition"
                    >
                      <svg
                        className="w-4 h-4"
                        viewBox="0 0 20 20"
                        fill="none"
                        xmlns="http://www.w3.org/2000/svg"
                        aria-hidden
                      >
                        <path
                          d="M10 3C5 3 2 10 2 10s3 7 8 7 8-7 8-7-3-7-8-7Zm0 11a4 4 0 1 1 0-8 4 4 0 0 1 0 8Zm0-2.5a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3Z"
                          fill="currentColor"
                        />
                      </svg>
                    </Link>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

