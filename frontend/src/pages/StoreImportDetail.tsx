import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { StoreOrderImport } from '../api/types'

export default function StoreImportDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [record, setRecord] = useState<StoreOrderImport | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [applying, setApplying] = useState(false)
  const [discarding, setDiscarding] = useState(false)

  useEffect(() => {
    if (!id) return
    setLoading(true)
    setError(null)
    api
      .get<StoreOrderImport>(`/integrations/stores/imports/${id}`)
      .then(setRecord)
      .catch((err: unknown) => {
        console.error(err)
        setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => setLoading(false))
  }, [id])

  async function handleApply() {
    if (!record || applying || discarding) return
    setApplying(true)
    setError(null)
    try {
      await api.post(`/integrations/stores/imports/${record.id}/apply`, {})
      navigate('/store-imports')
    } catch (err) {
      console.error(err)
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setApplying(false)
    }
  }

  async function handleDiscard() {
    if (!record || applying || discarding) return
    setDiscarding(true)
    setError(null)
    try {
      await api.post(`/integrations/stores/imports/${record.id}/discard`, {})
      navigate('/store-imports')
    } catch (err) {
      console.error(err)
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setDiscarding(false)
    }
  }

  if (loading) {
    return (
      <div className="max-w-4xl">
        <p className="text-ink-muted dark:text-gray-400">Loading…</p>
      </div>
    )
  }

  if (!record) {
    return (
      <div className="max-w-4xl">
        <h1 className="text-xl font-semibold text-ink dark:text-gray-100 mb-2">
          Store import
        </h1>
        <p className="text-ink-muted dark:text-gray-400 mb-4">
          Import not found.
        </p>
        <Link
          to="/store-imports"
          className="text-sm text-brand-600 dark:text-brand-400 hover:underline"
        >
          Back to Store Imports
        </Link>
      </div>
    )
  }

  const normalized = record.normalized_payload_json as unknown

  return (
    <div className="max-w-4xl">
      <div className="flex items-center justify-between gap-4 mb-6">
        <div>
          <h1 className="text-xl font-semibold text-ink dark:text-gray-100">
            Store import — {record.store} {record.external_order_id}
          </h1>
          <p className="text-sm text-ink-muted dark:text-gray-400 mt-1">
            Review this store order snapshot before applying it into your orders.
          </p>
        </div>
        <Link
          to="/store-imports"
          className="text-sm text-brand-600 dark:text-brand-400 hover:underline"
        >
          Back to Store Imports
        </Link>
      </div>

      {error && (
        <p className="mb-3 text-sm text-red-600 dark:text-red-400">
          {error}
        </p>
      )}

      <div className="flex flex-col gap-4 mb-6">
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-brand-200/80 dark:border-gray-700 p-4 space-y-2">
          <div>
            <span className="text-xs font-medium text-ink-muted dark:text-gray-400 uppercase tracking-wide">
              Store
            </span>
            <p className="text-ink dark:text-gray-200 mt-0.5">{record.store}</p>
          </div>
          <div>
            <span className="text-xs font-medium text-ink-muted dark:text-gray-400 uppercase tracking-wide">
              Store order id
            </span>
            <p className="text-ink dark:text-gray-200 mt-0.5">
              {record.external_order_id}
            </p>
          </div>
          {record.external_order_url && (
            <div>
              <span className="text-xs font-medium text-ink-muted dark:text-gray-400 uppercase tracking-wide">
                Store order link
              </span>
              <p className="mt-0.5">
                <a
                  href={record.external_order_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-sm text-brand-600 dark:text-brand-400 hover:underline break-all"
                >
                  Open in store site
                </a>
              </p>
            </div>
          )}
          <div>
            <span className="text-xs font-medium text-ink-muted dark:text-gray-400 uppercase tracking-wide">
              Status
            </span>
            <p className="text-xs font-medium uppercase tracking-wide text-ink-muted dark:text-gray-400 mt-0.5">
              {record.status}
            </p>
          </div>
          <div className="flex gap-3 pt-2">
            <button
              type="button"
              onClick={handleApply}
              disabled={applying || discarding || record.status !== 'pending'}
              className="inline-flex items-center justify-center px-3 py-2 text-sm font-medium rounded-md bg-brand-600 text-white hover:bg-brand-700 disabled:opacity-60 disabled:cursor-not-allowed"
            >
              {applying ? 'Applying…' : 'Apply store updates'}
            </button>
            <button
              type="button"
              onClick={handleDiscard}
              disabled={applying || discarding || record.status !== 'pending'}
              className="inline-flex items-center justify-center px-3 py-2 text-sm font-medium rounded-md border border-brand-200/80 dark:border-gray-700 text-ink-muted hover:text-ink hover:bg-brand-100/60 dark:text-gray-300 dark:hover:text-gray-100 dark:hover:bg-gray-700 disabled:opacity-60 disabled:cursor-not-allowed"
            >
              {discarding ? 'Discarding…' : 'Discard'}
            </button>
          </div>
        </div>

        <div>
          <span className="text-xs font-medium text-ink-muted dark:text-gray-400 uppercase tracking-wide">
            Normalized payload
          </span>
          <pre className="mt-2 p-4 bg-gray-100 dark:bg-gray-900 rounded-lg border border-brand-200/80 dark:border-gray-700 text-xs text-ink dark:text-gray-300 font-mono overflow-x-auto max-h-[28rem] overflow-y-auto">
            {JSON.stringify(normalized, null, 2)}
          </pre>
        </div>
      </div>
    </div>
  )
}

