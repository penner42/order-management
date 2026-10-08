import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, getStoredToken } from '../api/client'
import type { BrowserJob, BrowserProfile, Store, StoreAccount } from '../api/types'

function statusLabel(status: string): string {
  switch (status) {
    case 'ready':
      return 'Ready'
    case 'logged_out':
      return 'Logged out'
    case 'login_required':
      return 'Login required'
    case 'login_in_progress':
      return 'Logging in…'
    case 'importing':
      return 'Importing…'
    case 'error':
      return 'Error'
    default:
      return status
  }
}

function statusClass(status: string): string {
  switch (status) {
    case 'ready':
      return 'bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-200'
    case 'login_required':
    case 'logged_out':
      return 'bg-amber-100 text-amber-900 dark:bg-amber-900/40 dark:text-amber-100'
    case 'login_in_progress':
    case 'importing':
      return 'bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-200'
    case 'error':
      return 'bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-200'
    default:
      return 'bg-brand-50 text-ink dark:bg-gray-800 dark:text-gray-200'
  }
}

function LiveBrowserView({
  profileId,
  onDone,
  onCancel,
}: {
  profileId: number
  onDone: () => void
  onCancel: () => void
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const viewportRef = useRef<HTMLDivElement>(null)
  const imgRef = useRef<HTMLImageElement | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const lastSizeRef = useRef({ width: 0, height: 0 })
  const resizeTimerRef = useRef<number | null>(null)
  const [url, setUrl] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [connected, setConnected] = useState(false)
  const [viewportSize, setViewportSize] = useState({ width: 0, height: 0 })

  const send = useCallback((payload: Record<string, unknown>) => {
    const ws = wsRef.current
    if (!ws || ws.readyState !== WebSocket.OPEN) return
    ws.send(JSON.stringify(payload))
  }, [])

  const sendResize = useCallback(
    (immediate = false) => {
      const apply = () => {
        const el = viewportRef.current
        if (!el) return
        const width = Math.max(320, Math.floor(el.clientWidth))
        const height = Math.max(240, Math.floor(el.clientHeight))
        if (width === lastSizeRef.current.width && height === lastSizeRef.current.height) return
        lastSizeRef.current = { width, height }
        setViewportSize({ width, height })
        send({ type: 'resize', width, height })
      }

      if (immediate) {
        if (resizeTimerRef.current != null) {
          window.clearTimeout(resizeTimerRef.current)
          resizeTimerRef.current = null
        }
        apply()
        return
      }
      if (resizeTimerRef.current != null) window.clearTimeout(resizeTimerRef.current)
      resizeTimerRef.current = window.setTimeout(apply, 120)
    },
    [send]
  )

  useEffect(() => {
    const token = getStoredToken()
    if (!token) {
      setError('Not authenticated.')
      return
    }
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(
      `${proto}://${window.location.host}/api/browser-profiles/${profileId}/live?token=${encodeURIComponent(token)}`
    )
    wsRef.current = ws

    ws.onopen = () => {
      setConnected(true)
      // Sync remote viewport to the windowed pane as soon as we connect.
      sendResize(true)
    }
    ws.onclose = () => setConnected(false)
    ws.onerror = () => setError('Live view connection failed.')
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data as string)
        if (msg.type === 'frame' && msg.data) {
          const canvas = canvasRef.current
          if (!canvas) return
          const img = imgRef.current || new Image()
          imgRef.current = img
          img.onload = () => {
            canvas.width = img.naturalWidth
            canvas.height = img.naturalHeight
            const ctx = canvas.getContext('2d')
            if (ctx) ctx.drawImage(img, 0, 0)
          }
          img.src = `data:image/jpeg;base64,${msg.data}`
          if (msg.url) setUrl(msg.url)
        } else if (msg.type === 'status') {
          if (msg.url) setUrl(msg.url)
        } else if (msg.type === 'error') {
          setError(msg.message || 'Live view error')
        }
      } catch {
        // ignore
      }
    }

    return () => {
      ws.close()
      wsRef.current = null
    }
  }, [profileId, sendResize])

  useEffect(() => {
    const el = viewportRef.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(() => sendResize())
    ro.observe(el)
    sendResize(true)
    return () => {
      ro.disconnect()
      if (resizeTimerRef.current != null) window.clearTimeout(resizeTimerRef.current)
    }
  }, [sendResize])

  const coords = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current
    if (!canvas) return { x: 0, y: 0 }
    const rect = canvas.getBoundingClientRect()
    const scaleX = canvas.width / rect.width
    const scaleY = canvas.height / rect.height
    return {
      x: (e.clientX - rect.left) * scaleX,
      y: (e.clientY - rect.top) * scaleY,
    }
  }

  const onMouseDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    e.preventDefault()
    canvasRef.current?.focus()
    const { x, y } = coords(e)
    send({ type: 'mouse', event: 'down', x, y, button: 'left', clickCount: 1 })
  }
  const onMouseUp = (e: React.MouseEvent<HTMLCanvasElement>) => {
    e.preventDefault()
    const { x, y } = coords(e)
    send({ type: 'mouse', event: 'up', x, y, button: 'left', clickCount: 1 })
  }
  const onWheel = (e: React.WheelEvent<HTMLCanvasElement>) => {
    e.preventDefault()
    const { x, y } = coords(e)
    send({ type: 'mouse', event: 'wheel', x, y, deltaX: e.deltaX, deltaY: e.deltaY })
  }
  const onKeyDown = (e: React.KeyboardEvent<HTMLCanvasElement>) => {
    e.preventDefault()
    const modifiers =
      (e.altKey ? 1 : 0) | (e.ctrlKey ? 2 : 0) | (e.metaKey ? 4 : 0) | (e.shiftKey ? 8 : 0)
    // keyDown must not include text when we also send char — that doubles every character.
    send({
      type: 'key',
      event: 'down',
      key: e.key,
      code: e.code,
      windowsVirtualKeyCode: e.keyCode,
      nativeVirtualKeyCode: e.keyCode,
      modifiers,
    })
    if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
      send({
        type: 'key',
        event: 'char',
        key: e.key,
        text: e.key,
        unmodifiedText: e.key,
        modifiers,
      })
    }
  }
  const onKeyUp = (e: React.KeyboardEvent<HTMLCanvasElement>) => {
    e.preventDefault()
    const modifiers =
      (e.altKey ? 1 : 0) | (e.ctrlKey ? 2 : 0) | (e.metaKey ? 4 : 0) | (e.shiftKey ? 8 : 0)
    send({
      type: 'key',
      event: 'up',
      key: e.key,
      code: e.code,
      windowsVirtualKeyCode: e.keyCode,
      nativeVirtualKeyCode: e.keyCode,
      modifiers,
    })
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-3 sm:p-6">
      <div className="bg-white dark:bg-gray-900 rounded-lg shadow-xl w-[min(1100px,96vw)] h-[min(820px,92vh)] flex flex-col overflow-hidden">
        <div className="flex items-center justify-between gap-3 px-4 py-2.5 border-b border-brand-200/80 dark:border-gray-700 shrink-0">
          <div className="min-w-0">
            <h2 className="text-sm font-medium text-ink dark:text-gray-100">Log in — live browser</h2>
            <p className="text-xs text-ink-muted dark:text-gray-400 truncate" title={url}>
              {connected ? url || 'Connecting…' : 'Disconnected'}
              {viewportSize.width > 0 ? ` · ${viewportSize.width}×${viewportSize.height}` : ''}
            </p>
          </div>
          <button
            type="button"
            onClick={onCancel}
            className="rounded-lg border border-brand-200 dark:border-gray-600 px-3 py-1.5 text-sm text-ink dark:text-gray-100 hover:bg-brand-50 dark:hover:bg-gray-800"
          >
            Cancel
          </button>
        </div>
        <div className="px-3 pt-2 shrink-0">
          {error && (
            <p className="text-sm text-red-600 dark:text-red-400 mb-1">{error}</p>
          )}
          <p className="text-xs text-ink-muted dark:text-gray-400 mb-2">
            Click the window to focus, then sign in (including MFA). When your orders page loads, click Done.
          </p>
        </div>
        <div
          ref={viewportRef}
          className="mx-3 mb-2 flex-1 min-h-0 rounded border border-brand-200 dark:border-gray-700 bg-gray-100 dark:bg-gray-800 overflow-hidden"
        >
          <canvas
            ref={canvasRef}
            tabIndex={0}
            className="block w-full h-full cursor-crosshair outline-none"
            onMouseDown={onMouseDown}
            onMouseUp={onMouseUp}
            onWheel={onWheel}
            onKeyDown={onKeyDown}
            onKeyUp={onKeyUp}
          />
        </div>
        <div className="flex justify-end gap-2 px-4 py-3 border-t border-brand-200/80 dark:border-gray-700 shrink-0">
          <button
            type="button"
            onClick={onCancel}
            className="rounded-lg border border-brand-200 dark:border-gray-600 px-4 py-2 text-sm text-ink dark:text-gray-100 hover:bg-brand-50 dark:hover:bg-gray-800"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onDone}
            className="rounded-lg bg-brand-600 text-white px-4 py-2 text-sm font-medium hover:bg-brand-700"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  )
}

export default function BrowserAutomation() {
  const [profiles, setProfiles] = useState<BrowserProfile[]>([])
  const [stores, setStores] = useState<Store[]>([])
  const [accounts, setAccounts] = useState<StoreAccount[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedAccountId, setSelectedAccountId] = useState<number | ''>('')
  const [selectedRetailer, setSelectedRetailer] = useState<'amazon' | 'walmart'>('walmart')
  const [loginProfileId, setLoginProfileId] = useState<number | null>(null)
  const [maxPagesByProfile, setMaxPagesByProfile] = useState<Record<number, number>>({})
  const [jobsByProfile, setJobsByProfile] = useState<Record<number, BrowserJob>>({})
  const pollRef = useRef<number | null>(null)

  const load = useCallback(() => {
    setError(null)
    Promise.all([
      api.get<BrowserProfile[]>('/browser-profiles'),
      api.get<Store[]>('/stores'),
      api.get<StoreAccount[]>('/store-accounts'),
    ])
      .then(([plist, slist, alist]) => {
        setProfiles(plist)
        setStores(slist)
        setAccounts(alist)
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    return () => {
      if (pollRef.current != null) window.clearInterval(pollRef.current)
    }
  }, [])

  const accountOptions = accounts.filter(
    (a) => !profiles.some((p) => p.store_account_id === a.id)
  )

  const createProfile = async (e: React.FormEvent) => {
    e.preventDefault()
    if (selectedAccountId === '') return
    try {
      await api.post<BrowserProfile>('/browser-profiles', {
        store_account_id: selectedAccountId,
        retailer: selectedRetailer,
      })
      setSelectedAccountId('')
      load()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  const startLogin = async (profileId: number) => {
    setError(null)
    try {
      await api.post(`/browser-profiles/${profileId}/login/start`, {})
      setLoginProfileId(profileId)
      load()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  const finishLogin = async () => {
    if (loginProfileId == null) return
    try {
      await api.post(`/browser-profiles/${loginProfileId}/login/done`, {})
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoginProfileId(null)
      load()
    }
  }

  const cancelLogin = async () => {
    if (loginProfileId == null) return
    try {
      await api.post(`/browser-profiles/${loginProfileId}/login/cancel`, {})
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoginProfileId(null)
      load()
    }
  }

  const pollJob = (profileId: number, jobId: string) => {
    if (pollRef.current != null) window.clearInterval(pollRef.current)
    pollRef.current = window.setInterval(async () => {
      try {
        const job = await api.get<BrowserJob>(`/browser-profiles/jobs/${jobId}`)
        setJobsByProfile((prev) => ({ ...prev, [profileId]: job }))
        if (job.status === 'succeeded' || job.status === 'failed' || job.status === 'cancelled') {
          if (pollRef.current != null) window.clearInterval(pollRef.current)
          pollRef.current = null
          load()
        }
      } catch {
        // keep polling briefly
      }
    }, 1000)
  }

  const startImport = async (profileId: number) => {
    setError(null)
    const maxPages = maxPagesByProfile[profileId] ?? 3
    try {
      const res = await api.post<{ job_id: string }>(`/browser-profiles/${profileId}/import`, {
        max_pages: maxPages,
      })
      setJobsByProfile((prev) => ({
        ...prev,
        [profileId]: {
          id: res.job_id,
          profile_id: profileId,
          kind: 'import',
          status: 'queued',
          message: 'Queued…',
        },
      }))
      pollJob(profileId, res.job_id)
      load()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  const deleteProfile = async (profileId: number) => {
    if (!confirm('Delete this browser profile? Stored cookies for this account will be removed from the server.')) {
      return
    }
    try {
      await api.delete(`/browser-profiles/${profileId}`)
      load()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  const storeNameForAccount = (accountId: number) => {
    const acc = accounts.find((a) => a.id === accountId)
    if (!acc) return ''
    return stores.find((s) => s.id === acc.store_id)?.name ?? ''
  }

  if (loading) {
    return <p className="text-ink-muted dark:text-gray-400">Loading…</p>
  }

  return (
    <div>
      <h1 className="text-2xl font-semibold text-ink dark:text-gray-100 mb-2">Browser automation</h1>
      <p className="text-sm text-ink-muted dark:text-gray-400 mb-6 max-w-2xl">
        Run a real Chromium session on the server for each store account. Log in once in the embedded
        view (MFA supported); then use Import now to capture Walmart or Amazon orders into Import
        Review. Sessions live on the machine hosting the API — use a trusted/home network IP when
        possible. If you see a “Robot or human?” / press-and-hold page, complete it in the live view
        (hold the button); rebuild so the browser runs headed under Xvfb rather than headless.
      </p>

      {error && (
        <p className="mb-4 text-sm text-red-600 dark:text-red-400">{error}</p>
      )}

      <section className="mb-8 max-w-xl">
        <h2 className="text-lg font-medium text-ink dark:text-gray-100 mb-2">Add browser profile</h2>
        <p className="text-xs text-ink-muted dark:text-gray-400 mb-3">
          Create a{' '}
          <Link to="/stores" className="text-brand-600 hover:underline dark:text-brand-400">
            Store Account
          </Link>{' '}
          first (for Amazon, name it with the login email for auto-matching), then attach a browser
          profile here.
        </p>
        <form onSubmit={createProfile} className="flex flex-wrap gap-2 items-end">
          <label className="min-w-[8rem]">
            <span className="block text-xs text-ink-muted dark:text-gray-400 mb-1">Retailer</span>
            <select
              className="w-full rounded-lg border border-brand-200 dark:border-gray-600 dark:bg-gray-800 px-3 py-2 text-sm text-ink dark:text-gray-100"
              value={selectedRetailer}
              onChange={(e) => setSelectedRetailer(e.target.value as 'amazon' | 'walmart')}
            >
              <option value="walmart">Walmart</option>
              <option value="amazon">Amazon</option>
            </select>
          </label>
          <label className="flex-1 min-w-[12rem]">
            <span className="block text-xs text-ink-muted dark:text-gray-400 mb-1">Store account</span>
            <select
              className="w-full rounded-lg border border-brand-200 dark:border-gray-600 dark:bg-gray-800 px-3 py-2 text-sm text-ink dark:text-gray-100"
              value={selectedAccountId}
              onChange={(e) =>
                setSelectedAccountId(e.target.value ? Number(e.target.value) : '')
              }
            >
              <option value="">Select account…</option>
              {accountOptions.map((a) => (
                <option key={a.id} value={a.id}>
                  {storeNameForAccount(a.id)} — {a.name}
                </option>
              ))}
            </select>
          </label>
          <button
            type="submit"
            disabled={selectedAccountId === ''}
            className="rounded-lg bg-brand-600 text-white px-3 py-2 text-sm font-medium hover:bg-brand-700 disabled:opacity-50"
          >
            Add profile
          </button>
        </form>
        {accountOptions.length === 0 && (
          <p className="text-xs text-ink-muted dark:text-gray-400 mt-2">
            All store accounts already have a browser profile, or none exist yet.
          </p>
        )}
      </section>

      <section>
        <h2 className="text-lg font-medium text-ink dark:text-gray-100 mb-3">Profiles</h2>
        {profiles.length === 0 ? (
          <p className="text-sm text-ink-muted dark:text-gray-400">No browser profiles yet.</p>
        ) : (
          <ul className="space-y-3">
            {profiles.map((p) => {
              const job = jobsByProfile[p.id]
              const maxPages = maxPagesByProfile[p.id] ?? 3
              return (
                <li
                  key={p.id}
                  className="rounded-lg border border-brand-200/80 dark:border-gray-700 p-4"
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <div className="font-medium text-ink dark:text-gray-100">
                        {p.store_name ?? 'Store'} — {p.store_account_name ?? 'Account'}
                      </div>
                      <div className="text-xs text-ink-muted dark:text-gray-400 mt-0.5">
                        {p.retailer === 'walmart' ? 'Walmart' : p.retailer === 'amazon' ? 'Amazon' : p.retailer}
                        {' · '}
                        {p.last_import_at
                          ? `Last import ${new Date(p.last_import_at).toLocaleString()}`
                          : 'Never imported'}
                      </div>
                      <span
                        className={`inline-block mt-2 rounded-full px-2 py-0.5 text-xs ${statusClass(p.status)}`}
                      >
                        {statusLabel(p.status)}
                      </span>
                      {p.last_error && (
                        <p className="text-xs text-red-600 dark:text-red-400 mt-1 max-w-lg">
                          {p.last_error}
                        </p>
                      )}
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      <button
                        type="button"
                        onClick={() => startLogin(p.id)}
                        disabled={p.status === 'importing' || p.status === 'login_in_progress'}
                        className="rounded-lg border border-brand-200 dark:border-gray-600 px-3 py-1.5 text-sm hover:bg-brand-50 dark:hover:bg-gray-800 disabled:opacity-50"
                      >
                        Log in
                      </button>
                      <label className="flex items-center gap-1 text-xs text-ink-muted dark:text-gray-400">
                        Pages
                        <input
                          type="number"
                          min={1}
                          max={50}
                          value={maxPages}
                          onChange={(e) =>
                            setMaxPagesByProfile((prev) => ({
                              ...prev,
                              [p.id]: Math.min(50, Math.max(1, Number(e.target.value) || 1)),
                            }))
                          }
                          className="w-14 rounded border border-brand-200 dark:border-gray-600 dark:bg-gray-800 px-1 py-1 text-sm text-ink dark:text-gray-100"
                        />
                      </label>
                      <button
                        type="button"
                        onClick={() => startImport(p.id)}
                        disabled={p.status === 'importing' || p.status === 'login_in_progress'}
                        className="rounded-lg bg-brand-600 text-white px-3 py-1.5 text-sm font-medium hover:bg-brand-700 disabled:opacity-50"
                      >
                        Import now
                      </button>
                      <button
                        type="button"
                        onClick={() => deleteProfile(p.id)}
                        className="rounded-lg px-2 py-1.5 text-sm text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20"
                        aria-label="Delete profile"
                      >
                        Delete
                      </button>
                    </div>
                  </div>
                  {job && (
                    <div className="mt-3 text-sm text-ink-muted dark:text-gray-400">
                      <div>
                        Job: {job.status}
                        {job.message ? ` — ${job.message}` : ''}
                        {job.order_count != null ? ` (${job.order_count} orders)` : ''}
                      </div>
                      {job.error && (
                        <div className="text-red-600 dark:text-red-400">{job.error}</div>
                      )}
                      {job.token && job.status === 'succeeded' && (
                        <Link
                          to={`/import-review/bulk?token=${encodeURIComponent(job.token)}`}
                          className="inline-block mt-1 text-brand-600 hover:underline dark:text-brand-400"
                        >
                          Open Import Review
                        </Link>
                      )}
                    </div>
                  )}
                </li>
              )
            })}
          </ul>
        )}
      </section>

      {loginProfileId != null && (
        <LiveBrowserView
          profileId={loginProfileId}
          onDone={() => {
            void finishLogin()
          }}
          onCancel={() => {
            void cancelLogin()
          }}
        />
      )}
    </div>
  )
}
