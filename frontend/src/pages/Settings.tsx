import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { IgnoredZipCode } from '../api/types'
import { migrateLegacyIgnoredZipCodes, normalizeZipCode } from '../utils/ignoredZipCodes'

export default function Settings() {
  const [ignoredZipCodes, setIgnoredZipCodes] = useState<IgnoredZipCode[]>([])
  const [newZip, setNewZip] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        setError(null)
        await migrateLegacyIgnoredZipCodes()
        const list = await api.get<IgnoredZipCode[]>('/ignored-zip-codes')
        if (!cancelled) setIgnoredZipCodes(list)
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err))
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const addZip = async (e: React.FormEvent) => {
    e.preventDefault()
    const zip = newZip.trim()
    if (!zip || saving) return
    const existing = new Set(ignoredZipCodes.map((z) => normalizeZipCode(z.zip_code)))
    if (existing.has(normalizeZipCode(zip))) {
      setNewZip('')
      return
    }
    setSaving(true)
    setError(null)
    try {
      const created = await api.post<IgnoredZipCode>('/ignored-zip-codes', { zip_code: zip })
      setIgnoredZipCodes((prev) =>
        [...prev, created].sort((a, b) => a.zip_code.localeCompare(b.zip_code))
      )
      setNewZip('')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  const removeZip = async (row: IgnoredZipCode) => {
    if (saving) return
    setSaving(true)
    setError(null)
    try {
      await api.delete(`/ignored-zip-codes/${row.id}`)
      setIgnoredZipCodes((prev) => prev.filter((z) => z.id !== row.id))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div>
      <h1 className="text-2xl font-semibold text-ink dark:text-gray-100 mb-8">Settings</h1>

      <section className="max-w-xl">
        <h2 className="text-lg font-medium text-ink dark:text-gray-100">Ignored zip codes</h2>
        <p className="text-sm text-ink-muted dark:text-gray-400 mt-1 mb-4">
          Orders shipping to these zip codes are sorted to the bottom of bulk import review (above
          canceled orders) so they can be skipped. Automated imports skip creating new orders for
          these zip codes (existing orders still get tracking updates).
        </p>

        {error && (
          <p className="text-sm text-red-600 dark:text-red-400 mb-3" role="alert">
            {error}
          </p>
        )}

        <div className="flex flex-wrap items-center gap-1.5 min-h-[1.75rem]">
          {loading ? (
            <span className="text-xs text-ink-muted dark:text-gray-400">Loading…</span>
          ) : ignoredZipCodes.length === 0 ? (
            <span className="text-xs text-ink-muted dark:text-gray-400">No ignored zip codes</span>
          ) : (
            ignoredZipCodes.map((row) => (
              <span
                key={row.id}
                className="inline-flex items-center gap-1 rounded-full bg-brand-50 dark:bg-brand-900/30 px-2 py-0.5 text-xs text-ink dark:text-gray-100"
              >
                {row.zip_code}
                <button
                  type="button"
                  onClick={() => removeZip(row)}
                  disabled={saving}
                  className="text-ink-muted hover:text-red-600 dark:hover:text-red-400 disabled:opacity-50"
                  aria-label={`Remove zip code ${row.zip_code}`}
                >
                  ×
                </button>
              </span>
            ))
          )}
        </div>

        <form onSubmit={addZip} className="mt-3 flex gap-2 max-w-md">
          <input
            type="text"
            className="rounded-lg border border-brand-200 dark:border-gray-600 dark:bg-gray-800 px-3 py-2 flex-1 text-sm text-ink dark:text-gray-100 placeholder:text-ink-muted"
            placeholder="Add zip code"
            value={newZip}
            onChange={(e) => setNewZip(e.target.value)}
            autoComplete="postal-code"
            disabled={loading || saving}
          />
          <button
            type="submit"
            disabled={loading || saving || !newZip.trim()}
            className="rounded-lg bg-brand-600 text-white px-3 py-2 text-sm font-medium hover:bg-brand-700 disabled:opacity-50 transition"
          >
            Add
          </button>
        </form>
      </section>
    </div>
  )
}
