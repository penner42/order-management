import { useCallback, useEffect, useState } from 'react'
import { api, getStoredToken } from '../api/client'
import type { BrowserExtensionStatus } from '../api/types'

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatWhen(iso: string | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString()
}

async function downloadArtifact(browser: 'chrome' | 'firefox', filename: string) {
  const token = getStoredToken()
  const res = await fetch(`/api/browser-extension/download/${browser}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || 'Download failed')
  }
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

export default function BrowserExtension() {
  const [info, setInfo] = useState<BrowserExtensionStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [downloading, setDownloading] = useState<'chrome' | 'firefox' | null>(null)
  const [rebuilding, setRebuilding] = useState(false)

  const load = useCallback(async () => {
    try {
      const data = await api.get<BrowserExtensionStatus>('/browser-extension')
      setInfo(data)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load extension info')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    if (!info || info.status !== 'signing') return
    const timer = window.setInterval(load, 2000)
    return () => window.clearInterval(timer)
  }, [info, load])

  const onDownload = async (browser: 'chrome' | 'firefox') => {
    const artifact = browser === 'chrome' ? info?.meta?.chrome : info?.meta?.firefox
    if (!artifact) return
    setDownloading(browser)
    try {
      await downloadArtifact(browser, artifact.filename)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Download failed')
    } finally {
      setDownloading(null)
    }
  }

  const onRebuild = async () => {
    setRebuilding(true)
    setError(null)
    try {
      const data = await api.post<BrowserExtensionStatus>('/browser-extension/rebuild', {})
      setInfo(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Rebuild failed')
    } finally {
      setRebuilding(false)
    }
  }

  const statusLabel =
    info?.status === 'signing'
      ? 'Building signed packages…'
      : info?.status === 'ready'
        ? 'Ready to install'
        : info?.status === 'error'
          ? 'Build failed'
          : info?.status === 'unavailable'
            ? 'Extension source not available on this server'
            : 'Checking…'

  return (
    <div>
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-8">
        <div>
          <h1 className="text-2xl font-semibold text-ink dark:text-gray-100">Browser Extension</h1>
          <p className="text-ink-muted dark:text-gray-400 mt-1">
            Download signed builds for Chrome and Firefox (personal use).
          </p>
        </div>
        <button
          type="button"
          onClick={onRebuild}
          disabled={rebuilding || info?.status === 'signing' || !info?.available}
          className="px-4 py-2 bg-brand-600 text-white rounded-lg font-medium hover:bg-brand-700 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {rebuilding || info?.status === 'signing' ? 'Building…' : 'Rebuild now'}
        </button>
      </div>

      {loading && !info ? (
        <p className="text-ink-muted dark:text-gray-400">Loading…</p>
      ) : (
        <div className="space-y-6">
          <div className="rounded-xl border border-brand-200/80 dark:border-gray-700 bg-white dark:bg-gray-900 p-4 sm:p-5">
            <div className="text-sm text-ink-muted dark:text-gray-400">Status</div>
            <div className="mt-1 font-medium text-ink dark:text-gray-100">{statusLabel}</div>
            {info?.meta && (
              <dl className="mt-4 grid gap-2 sm:grid-cols-2 text-sm">
                <div>
                  <dt className="text-ink-muted dark:text-gray-400">Version</dt>
                  <dd className="text-ink dark:text-gray-100">{info.meta.version}</dd>
                </div>
                <div>
                  <dt className="text-ink-muted dark:text-gray-400">Last built</dt>
                  <dd className="text-ink dark:text-gray-100">{formatWhen(info.meta.built_at)}</dd>
                </div>
              </dl>
            )}
            {info?.error && (
              <p className="mt-3 text-sm text-red-600 dark:text-red-400">{info.error}</p>
            )}
            {error && (
              <p className="mt-3 text-sm text-red-600 dark:text-red-400">{error}</p>
            )}
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <section className="rounded-xl border border-brand-200/80 dark:border-gray-700 bg-white dark:bg-gray-900 p-4 sm:p-5">
              <h2 className="text-lg font-medium text-ink dark:text-gray-100">Chrome</h2>
              <p className="mt-2 text-sm text-ink-muted dark:text-gray-400">
                Install via <span className="font-mono">chrome://extensions</span> → Developer mode → drag the .crx file onto the page.
              </p>
              {info?.meta?.chrome ? (
                <div className="mt-4 text-sm text-ink-muted dark:text-gray-400">
                  {info.meta.chrome.filename} · {formatBytes(info.meta.chrome.size_bytes)}
                </div>
              ) : (
                <p className="mt-4 text-sm text-ink-muted dark:text-gray-400">No Chrome build available yet.</p>
              )}
              <button
                type="button"
                onClick={() => onDownload('chrome')}
                disabled={!info?.meta?.chrome || downloading !== null}
                className="mt-4 px-4 py-2 bg-brand-600 text-white rounded-lg font-medium hover:bg-brand-700 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {downloading === 'chrome' ? 'Downloading…' : 'Download for Chrome'}
              </button>
            </section>

            <section className="rounded-xl border border-brand-200/80 dark:border-gray-700 bg-white dark:bg-gray-900 p-4 sm:p-5">
              <h2 className="text-lg font-medium text-ink dark:text-gray-100">Firefox</h2>
              <p className="mt-2 text-sm text-ink-muted dark:text-gray-400">
                Install via <span className="font-mono">about:addons</span> → gear icon → Install Add-on From File…
              </p>
              {info?.meta?.firefox ? (
                <div className="mt-4 text-sm text-ink-muted dark:text-gray-400">
                  {info.meta.firefox.filename} · {formatBytes(info.meta.firefox.size_bytes)}
                </div>
              ) : (
                <p className="mt-4 text-sm text-ink-muted dark:text-gray-400">
                  No Firefox build yet. Set <span className="font-mono">WEB_EXT_API_KEY</span> and{' '}
                  <span className="font-mono">WEB_EXT_API_SECRET</span> on the server to enable Firefox signing.
                </p>
              )}
              <button
                type="button"
                onClick={() => onDownload('firefox')}
                disabled={!info?.meta?.firefox || downloading !== null}
                className="mt-4 px-4 py-2 bg-brand-600 text-white rounded-lg font-medium hover:bg-brand-700 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {downloading === 'firefox' ? 'Downloading…' : 'Download for Firefox'}
              </button>
            </section>
          </div>
        </div>
      )}
    </div>
  )
}
