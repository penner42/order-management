import { useState } from 'react'
import { getIgnoredZipCodes, setIgnoredZipCodes } from '../utils/ignoredZipCodes'

export default function Settings() {
  const [ignoredZipCodes, setIgnoredZipCodesState] = useState<string[]>(() => getIgnoredZipCodes())
  const [newZip, setNewZip] = useState('')

  const persist = (next: string[]) => {
    setIgnoredZipCodes(next)
    setIgnoredZipCodesState(next)
  }

  const addZip = (e: React.FormEvent) => {
    e.preventDefault()
    const zip = newZip.trim()
    if (!zip) return
    const existing = new Set(ignoredZipCodes.map((z) => z.toLowerCase()))
    if (existing.has(zip.toLowerCase())) {
      setNewZip('')
      return
    }
    persist([...ignoredZipCodes, zip])
    setNewZip('')
  }

  const removeZip = (zip: string) => {
    persist(ignoredZipCodes.filter((z) => z !== zip))
  }

  return (
    <div>
      <h1 className="text-2xl font-semibold text-ink dark:text-gray-100 mb-8">Settings</h1>

      <section className="max-w-xl">
        <h2 className="text-lg font-medium text-ink dark:text-gray-100">Ignored zip codes</h2>
        <p className="text-sm text-ink-muted dark:text-gray-400 mt-1 mb-4">
          Orders shipping to these zip codes are sorted to the bottom of bulk import review (above
          canceled orders) so they can be skipped.
        </p>

        <div className="flex flex-wrap items-center gap-1.5 min-h-[1.75rem]">
          {ignoredZipCodes.length === 0 ? (
            <span className="text-xs text-ink-muted dark:text-gray-400">No ignored zip codes</span>
          ) : (
            ignoredZipCodes.map((zip) => (
              <span
                key={zip}
                className="inline-flex items-center gap-1 rounded-full bg-brand-50 dark:bg-brand-900/30 px-2 py-0.5 text-xs text-ink dark:text-gray-100"
              >
                {zip}
                <button
                  type="button"
                  onClick={() => removeZip(zip)}
                  className="text-ink-muted hover:text-red-600 dark:hover:text-red-400"
                  aria-label={`Remove zip code ${zip}`}
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
          />
          <button
            type="submit"
            disabled={!newZip.trim()}
            className="rounded-lg bg-brand-600 text-white px-3 py-2 text-sm font-medium hover:bg-brand-700 disabled:opacity-50 transition"
          >
            Add
          </button>
        </form>
      </section>
    </div>
  )
}
