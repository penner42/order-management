import { useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../api/client'

export default function BestBuyImport() {
  const { orderId } = useParams()
  const navigate = useNavigate()
  const [url, setUrl] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (loading || (!orderId && !url.trim())) return
    setError(null)
    setLoading(true)
    try {
      const base = '/integrations/stores/orders'
      const { token } = orderId
        ? await api.post<{ token: string }>(`${base}/${orderId}/best-buy-tracking-preview`, {})
        : await api.post<{ token: string }>(`${base}/best-buy-preview`, { url: url.trim() })
      navigate(`/import-review/bulk?token=${encodeURIComponent(token)}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not read the Best Buy order.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="mx-auto max-w-xl">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-semibold text-ink dark:text-gray-100">
          {orderId ? 'Check Best Buy tracking' : 'Import Best Buy order'}
        </h1>
        <Link to="/" className="text-sm text-brand-600 hover:underline dark:text-brand-400">
          Back to Orders
        </Link>
      </div>
      <form onSubmit={handleSubmit} className="space-y-4 rounded-lg border border-brand-200 bg-white p-4 dark:border-gray-700 dark:bg-gray-800">
        {orderId ? (
          <p className="text-sm text-ink-muted">Open the saved order link to review any new tracking numbers.</p>
        ) : (
          <div className="space-y-1.5">
            <label htmlFor="best-buy-url" className="block text-sm font-medium text-ink dark:text-gray-200">
              Best Buy order URL
            </label>
            <input
              id="best-buy-url"
              type="url"
              value={url}
              onChange={event => setUrl(event.target.value)}
              autoComplete="off"
              spellCheck={false}
              maxLength={4096}
              placeholder="https://click.emailinfo2.bestbuy.com/…"
              disabled={loading}
              required
              className="w-full rounded-md border border-brand-200 bg-transparent px-3 py-2 text-sm text-ink dark:border-gray-600 dark:text-gray-200"
            />
            <p className="text-sm text-ink-muted">Paste the order link from your Best Buy email. The link is saved when you approve the import.</p>
          </div>
        )}
        {error && <p role="alert" className="text-sm text-red-600 dark:text-red-400">{error}</p>}
        <button
          type="submit"
          disabled={loading || (!orderId && !url.trim())}
          className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-60"
        >
          {loading ? 'Reading Best Buy order…' : orderId ? 'Check tracking' : 'Review order'}
        </button>
      </form>
    </div>
  )
}
