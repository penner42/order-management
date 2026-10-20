import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, getStoredToken } from '../api/client'
import type {
  BrowserJob,
  BrowserProfile,
  BrowserProfileScheduleUpdate,
  Store,
  StoreAccount,
} from '../api/types'

type ScheduleDraft = {
  full_check_enabled: boolean
  full_check_max_pages: number
  unshipped_check_enabled: boolean
  unshipped_check_interval_minutes: number
}

function scheduleDraftFromProfile(p: BrowserProfile): ScheduleDraft {
  return {
    full_check_enabled: !!p.full_check_enabled,
    full_check_max_pages: p.full_check_max_pages ?? 3,
    unshipped_check_enabled: !!p.unshipped_check_enabled,
    unshipped_check_interval_minutes: p.unshipped_check_interval_minutes ?? 360,
  }
}

function formatLastRun(iso: string | null | undefined): string {
  if (!iso) return 'Never'
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return 'Never'
  }
}

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

/** Snap + hysteresis so ResizeObserver ↔ canvas ↔ backend can't oscillate by 1px. */
const RESIZE_SNAP = 16
const RESIZE_THRESHOLD = 16
/** Never drive the remote browser below a normal desktop pane — tiny viewports get blocked. */
const MIN_VIEWPORT_WIDTH = 1024
const MIN_VIEWPORT_HEIGHT = 720
const DEFAULT_VIEWPORT = { width: 1920, height: 1080 }
/** Must stay in sync with backend SCREENCAST_MAX. */
const MAX_VIEWPORT_WIDTH = 2560
const MAX_VIEWPORT_HEIGHT = 1440
/** ~40 Hz pointer moves — responsive without flooding the WS. */
const MOUSE_MOVE_MIN_MS = 24

export function LiveBrowserView({
  profileId,
  suggestedUrl,
  onDone,
  onCancel,
}: {
  profileId: number
  suggestedUrl: string
  onDone: () => void
  onCancel: () => void
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const viewportRef = useRef<HTMLDivElement>(null)
  const imgRef = useRef<HTMLImageElement | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const lastSizeRef = useRef({ width: 0, height: 0 })
  const resizeTimerRef = useRef<number | null>(null)
  const openedRef = useRef(false)
  const frameBusyRef = useRef(false)
  const pendingFrameRef = useRef<string | null>(null)
  /**
   * Screencast JPEG size → Playwright CSS px. Kept in sync from frame metadata when
   * the encoder scales the bitmap; otherwise equals the bitmap size (1:1).
   */
  const remoteViewportRef = useRef({ width: 0, height: 0 })
  const [url, setUrl] = useState('')
  const [address, setAddress] = useState(suggestedUrl)
  const [error, setError] = useState<string | null>(null)
  const [connected, setConnected] = useState(false)
  const [viewportSize, setViewportSize] = useState({ width: 0, height: 0 })

  /** Size/position the canvas so its CSS box matches the drawn frame (no object-fit guesswork). */
  const layoutCanvas = useCallback(() => {
    const pane = viewportRef.current
    const canvas = canvasRef.current
    if (!pane || !canvas) return
    const bw = canvas.width
    const bh = canvas.height
    if (!bw || !bh) return
    const paneW = pane.clientWidth
    const paneH = pane.clientHeight
    if (paneW <= 0 || paneH <= 0) return
    const scale = Math.min(paneW / bw, paneH / bh)
    const w = Math.max(1, Math.round(bw * scale))
    const h = Math.max(1, Math.round(bh * scale))
    canvas.style.width = `${w}px`
    canvas.style.height = `${h}px`
    canvas.style.left = `${Math.round((paneW - w) / 2)}px`
    canvas.style.top = `${Math.round((paneH - h) / 2)}px`
  }, [])

  const paintFrame = useCallback(
    (b64: string) => {
      pendingFrameRef.current = b64
      if (frameBusyRef.current) return
      frameBusyRef.current = true

      const pump = () => {
        const next = pendingFrameRef.current
        pendingFrameRef.current = null
        if (!next) {
          frameBusyRef.current = false
          return
        }
        const canvas = canvasRef.current
        if (!canvas) {
          frameBusyRef.current = false
          return
        }
        const img = imgRef.current || new Image()
        imgRef.current = img
        img.onload = () => {
          if (canvas.width !== img.naturalWidth) canvas.width = img.naturalWidth
          if (canvas.height !== img.naturalHeight) canvas.height = img.naturalHeight
          const ctx = canvas.getContext('2d')
          if (ctx) ctx.drawImage(img, 0, 0)
          // Default page space to bitmap until metadata says otherwise.
          if (!remoteViewportRef.current.width || !remoteViewportRef.current.height) {
            remoteViewportRef.current = { width: img.naturalWidth, height: img.naturalHeight }
          }
          layoutCanvas()
          if (pendingFrameRef.current) {
            requestAnimationFrame(pump)
          } else {
            frameBusyRef.current = false
          }
        }
        img.onerror = () => {
          if (pendingFrameRef.current) {
            requestAnimationFrame(pump)
          } else {
            frameBusyRef.current = false
          }
        }
        img.src = `data:image/jpeg;base64,${next}`
      }
      requestAnimationFrame(pump)
    },
    [layoutCanvas]
  )

  const send = useCallback((payload: Record<string, unknown>) => {
    const ws = wsRef.current
    if (!ws || ws.readyState !== WebSocket.OPEN) return
    ws.send(JSON.stringify(payload))
  }, [])

  const measureViewport = useCallback(() => {
    const el = viewportRef.current
    if (!el) return null
    // Wait until flex layout has given the pane a real size.
    if (el.clientWidth < 400 || el.clientHeight < 400) return null
    const rawW = Math.floor(el.clientWidth)
    const rawH = Math.floor(el.clientHeight)
    const width = Math.min(
      MAX_VIEWPORT_WIDTH,
      Math.max(MIN_VIEWPORT_WIDTH, Math.floor(rawW / RESIZE_SNAP) * RESIZE_SNAP)
    )
    const height = Math.min(
      MAX_VIEWPORT_HEIGHT,
      Math.max(MIN_VIEWPORT_HEIGHT, Math.floor(rawH / RESIZE_SNAP) * RESIZE_SNAP)
    )
    return { width, height }
  }, [])

  const sendResize = useCallback(
    (immediate = false) => {
      const apply = () => {
        const measured = measureViewport()
        // Fall back once so we never stick on a 240px-tall bot-looking viewport.
        const next = measured ?? (lastSizeRef.current.width > 0 ? null : DEFAULT_VIEWPORT)
        if (!next) return
        const prev = lastSizeRef.current
        if (prev.width > 0 && prev.height > 0) {
          if (
            Math.abs(next.width - prev.width) < RESIZE_THRESHOLD &&
            Math.abs(next.height - prev.height) < RESIZE_THRESHOLD
          ) {
            return
          }
        }
        if (next.width === prev.width && next.height === prev.height) return
        lastSizeRef.current = next
        setViewportSize(next)
        send({ type: 'resize', width: next.width, height: next.height })
      }

      if (immediate) {
        if (resizeTimerRef.current != null) {
          window.clearTimeout(resizeTimerRef.current)
          resizeTimerRef.current = null
        }
        // Double rAF: let the modal finish flex layout before measuring.
        requestAnimationFrame(() => requestAnimationFrame(apply))
        return
      }
      if (resizeTimerRef.current != null) window.clearTimeout(resizeTimerRef.current)
      resizeTimerRef.current = window.setTimeout(apply, 250)
    },
    [measureViewport, send]
  )

  useEffect(() => {
    const token = getStoredToken()
    if (!token) {
      setError('Not authenticated.')
      return
    }
    openedRef.current = false
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(
      `${proto}://${window.location.host}/api/browser-profiles/${profileId}/live?token=${encodeURIComponent(token)}`
    )
    wsRef.current = ws

    ws.onopen = () => {
      if (wsRef.current !== ws) return
      openedRef.current = true
      setConnected(true)
      setError(null)
      sendResize(true)
    }
    ws.onclose = (ev) => {
      if (wsRef.current !== ws) return
      setConnected(false)
      if (!openedRef.current) {
        setError(
          `Live view connection failed (WebSocket close ${ev.code || 0}). ` +
            'Behind a reverse proxy, /api must upgrade WebSockets (HTTP/1.1, Upgrade + Connection headers). ' +
            'Backend logs show a plain GET 404/426 when the upgrade is missing.'
        )
      } else {
        setError(
          'Live browser connection closed. The server may have restarted. ' +
            'Cancel this view and start login again; saved browser cookies are preserved.'
        )
      }
    }
    // onerror always fires before onclose; don't surface it alone (false positives).
    ws.onerror = () => {}
    ws.onmessage = (ev) => {
      if (wsRef.current !== ws) return
      try {
        const msg = JSON.parse(ev.data as string)
        if (msg.type === 'frame' && msg.data) {
          const meta = msg.metadata as { viewportWidth?: number; viewportHeight?: number } | undefined
          const vpW = Number(meta?.viewportWidth) || 0
          const vpH = Number(meta?.viewportHeight) || 0
          // Only adopt metadata when it looks like a real page viewport (not 0 / garbage).
          if (vpW >= 200 && vpH >= 200) {
            remoteViewportRef.current = { width: vpW, height: vpH }
          }
          paintFrame(msg.data as string)
          if (msg.url) {
            setUrl(msg.url)
            setAddress(msg.url)
          }
        } else if (msg.type === 'status') {
          if (msg.url) {
            setUrl(msg.url)
            setAddress(msg.url)
          }
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
      pendingFrameRef.current = null
      frameBusyRef.current = false
    }
  }, [profileId, sendResize, paintFrame])

  useEffect(() => {
    const el = viewportRef.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(() => {
      layoutCanvas()
      sendResize()
    })
    ro.observe(el)
    sendResize(true)
    layoutCanvas()
    return () => {
      ro.disconnect()
      if (resizeTimerRef.current != null) window.clearTimeout(resizeTimerRef.current)
    }
  }, [sendResize, layoutCanvas])

  /**
   * Map pointer on the laid-out canvas → Playwright page CSS px.
   * Use the frame's viewport size (not JPEG bitmap size): screencast may scale
   * the JPEG down to fit `size` while mouse coords stay in viewport space.
   */
  const coords = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current
    if (!canvas) return { x: 0, y: 0 }
    const rect = canvas.getBoundingClientRect()
    if (rect.width <= 0 || rect.height <= 0) return { x: 0, y: 0 }

    const localX = e.clientX - rect.left
    const localY = e.clientY - rect.top
    if (localX < 0 || localY < 0 || localX > rect.width || localY > rect.height) {
      return { x: -1, y: -1 }
    }

    const vpW = remoteViewportRef.current.width || canvas.width
    const vpH = remoteViewportRef.current.height || canvas.height
    if (!vpW || !vpH) return { x: 0, y: 0 }
    return {
      x: Math.max(0, Math.min(vpW - 1e-3, (localX / rect.width) * vpW)),
      y: Math.max(0, Math.min(vpH - 1e-3, (localY / rect.height) * vpH)),
    }
  }

  const lastMoveSentRef = useRef(0)
  const onMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const now = performance.now()
    if (now - lastMoveSentRef.current < MOUSE_MOVE_MIN_MS) return
    lastMoveSentRef.current = now
    const { x, y } = coords(e)
    if (x < 0 || y < 0) return
    send({ type: 'mouse', event: 'move', x, y })
  }
  const onMouseDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    e.preventDefault()
    canvasRef.current?.focus()
    const { x, y } = coords(e)
    if (x < 0 || y < 0) return
    send({ type: 'mouse', event: 'move', x, y })
    send({ type: 'mouse', event: 'down', x, y, button: 'left', clickCount: 1 })
  }
  const onMouseUp = (e: React.MouseEvent<HTMLCanvasElement>) => {
    e.preventDefault()
    const { x, y } = coords(e)
    // Always release the button (even in letterbox gutters) so focus isn't stuck.
    send({
      type: 'mouse',
      event: 'up',
      x: x < 0 ? 0 : x,
      y: y < 0 ? 0 : y,
      button: 'left',
      clickCount: 1,
    })
  }
  const onWheel = (e: React.WheelEvent<HTMLCanvasElement>) => {
    e.preventDefault()
    const { x, y } = coords(e)
    if (x < 0 || y < 0) return
    send({ type: 'mouse', event: 'wheel', x, y, deltaX: e.deltaX, deltaY: e.deltaY })
  }
  const pasteText = (text: string) => {
    if (text) send({ type: 'paste', text })
  }
  const onPaste = (e: React.ClipboardEvent<HTMLCanvasElement>) => {
    e.preventDefault()
    pasteText(e.clipboardData?.getData('text/plain') ?? '')
  }
  const onKeyDown = (e: React.KeyboardEvent<HTMLCanvasElement>) => {
    // Ctrl/Cmd+V: send local clipboard text. Remote Ctrl+V has no access to your clipboard.
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'v') {
      e.preventDefault()
      if (navigator.clipboard?.readText) {
        void navigator.clipboard.readText().then(pasteText).catch(() => {})
      }
      return
    }
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
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'v') {
      return
    }
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

  const navigateTo = (raw: string) => {
    let next = raw.trim()
    if (!next) return
    if (!/^https?:\/\//i.test(next)) next = `https://${next}`
    setAddress(next)
    send({ type: 'navigate', url: next })
  }

  const blocked = /\/blocked/i.test(url)

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-1 sm:p-2">
      <div className="bg-[#dee1e6] dark:bg-gray-900 rounded-xl shadow-2xl w-[min(2560px,100vw-0.5rem)] h-[min(1440px,100vh-0.5rem)] flex flex-col overflow-hidden border border-black/10 dark:border-gray-700">
        {/* Window chrome */}
        <div className="flex items-center gap-2 px-3 py-1.5 bg-[#e8eaed] dark:bg-gray-800 border-b border-black/10 dark:border-gray-700 shrink-0">
          <div className="flex items-center gap-1.5 shrink-0" aria-hidden>
            <span className="h-3 w-3 rounded-full bg-[#ff5f57]" />
            <span className="h-3 w-3 rounded-full bg-[#febc2e]" />
            <span className="h-3 w-3 rounded-full bg-[#28c840]" />
          </div>
          <form
            className="min-w-0 flex-1 flex items-center gap-1.5"
            onSubmit={(e) => {
              e.preventDefault()
              navigateTo(address)
            }}
          >
            <input
              type="text"
              value={address}
              onChange={(e) => setAddress(e.target.value)}
              placeholder={connected ? 'Enter URL…' : 'Connecting…'}
              disabled={!connected}
              className="min-w-0 flex-1 rounded-full bg-white dark:bg-gray-900 border border-black/10 dark:border-gray-600 px-3 py-1 text-xs text-ink dark:text-gray-200 outline-none focus:ring-1 focus:ring-brand-500"
              aria-label="Address bar"
            />
            <button
              type="submit"
              disabled={!connected}
              className="rounded-md border border-black/10 dark:border-gray-600 bg-white dark:bg-gray-900 px-2.5 py-1 text-xs text-ink dark:text-gray-100 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-50"
            >
              Go
            </button>
            {suggestedUrl && (
              <button
                type="button"
                disabled={!connected}
                onClick={() => navigateTo(suggestedUrl)}
                className="rounded-md border border-black/10 dark:border-gray-600 bg-white dark:bg-gray-900 px-2.5 py-1 text-xs text-ink dark:text-gray-100 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-50"
              >
                Home
              </button>
            )}
          </form>
          {viewportSize.width > 0 && (
            <span className="shrink-0 text-[10px] text-ink-muted dark:text-gray-500">
              {viewportSize.width}×{viewportSize.height}
            </span>
          )}
          <button
            type="button"
            onClick={onCancel}
            className="rounded-md border border-black/10 dark:border-gray-600 bg-white dark:bg-gray-900 px-2.5 py-1 text-xs text-ink dark:text-gray-100 hover:bg-gray-50 dark:hover:bg-gray-800"
          >
            Cancel
          </button>
        </div>
        <div className="px-3 py-1.5 shrink-0 bg-[#f1f3f4] dark:bg-gray-900 border-b border-black/5 dark:border-gray-800">
          {error ? (
            <p className="text-xs text-red-600 dark:text-red-400">{error}</p>
          ) : blocked ? (
            <p className="text-xs text-amber-800 dark:text-amber-200">
              Site blocked this session. Cancel, delete the profile, create a new one after Camoufox is deployed, then open the store homepage yourself via the address bar.
            </p>
          ) : (
            <p className="text-xs text-ink-muted dark:text-gray-400">
              Click the page to focus. Ctrl/Cmd+V pastes into the remote browser. Use Home or the address bar to open the store, sign in, then Done.
            </p>
          )}
        </div>
        <div
          ref={viewportRef}
          className="relative flex-1 min-h-0 bg-neutral-200 dark:bg-gray-950 overflow-hidden"
        >
          <canvas
            ref={canvasRef}
            tabIndex={0}
            className="absolute cursor-default outline-none bg-white dark:bg-gray-950"
            onMouseMove={onMouseMove}
            onMouseDown={onMouseDown}
            onMouseUp={onMouseUp}
            onWheel={onWheel}
            onPaste={onPaste}
            onKeyDown={onKeyDown}
            onKeyUp={onKeyUp}
          />
        </div>
        <div className="flex justify-end gap-2 px-4 py-2.5 bg-[#e8eaed] dark:bg-gray-800 border-t border-black/10 dark:border-gray-700 shrink-0">
          <button
            type="button"
            onClick={onCancel}
            className="rounded-lg border border-black/10 dark:border-gray-600 bg-white dark:bg-gray-900 px-4 py-2 text-sm text-ink dark:text-gray-100 hover:bg-gray-50 dark:hover:bg-gray-800"
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

export default function BrowserProfileControls({ storeId, accountId, profiles, stores, accounts, onChange }: {
  storeId: number; accountId: number | null; profiles: BrowserProfile[]; stores: Store[];
  accounts: StoreAccount[]; onChange: () => Promise<void>
}) {
  const [error, setError] = useState<string | null>(null)
  const [selectedRetailer, setSelectedRetailer] = useState<'' | 'amazon' | 'walmart' | 'costco'>('')
  const [loginProfileId, setLoginProfileId] = useState<number | null>(null)
  const [loginSuggestedUrl, setLoginSuggestedUrl] = useState('https://www.walmart.com/')
  const [maxPagesByProfile, setMaxPagesByProfile] = useState<Record<number, number>>({})
  const [scheduleByProfile, setScheduleByProfile] = useState<Record<number, ScheduleDraft>>({})
  const [savingScheduleId, setSavingScheduleId] = useState<number | null>(null)
  const [editingAccountProfileId, setEditingAccountProfileId] = useState<number | null>(null)
  const [savingAccountProfileId, setSavingAccountProfileId] = useState<number | null>(null)
  const [jobsByProfile, setJobsByProfile] = useState<Record<number, BrowserJob>>({})
  const load = useCallback(() => {
    setError(null)
    return onChange()
  }, [onChange])

  const ownerProfiles = profiles.filter(p => p.store_id === storeId && p.store_account_id === accountId)
  const ownerProfileId = ownerProfiles[0]?.id
  const ownerStatus = ownerProfiles[0]?.status
  useEffect(() => {
    if (ownerProfileId == null) return
    let cancelled = false
    const refreshJob = async () => {
      try {
        const job = await api.get<BrowserJob | null>(`/browser-profiles/${ownerProfileId}/job`)
        if (!cancelled && job) {
          setJobsByProfile(prev => ({ ...prev, [ownerProfileId]: job }))
          if (ownerStatus === 'importing' && ['succeeded', 'failed', 'cancelled'].includes(job.status)) load()
        }
      } catch { /* Existing profile errors are shown by load(). */ }
    }
    void refreshJob()
    const interval = ownerStatus === 'importing' ? window.setInterval(refreshJob, 2000) : null
    return () => { cancelled = true; if (interval != null) window.clearInterval(interval) }
  }, [ownerProfileId, ownerStatus, load])
  const ownerOptions = [
    ...stores.map(store => ({ key: `store:${store.id}`, storeId: store.id, accountId: null as number | null, label: `${store.name} — Store profile` })),
    ...accounts.map(account => ({ key: `account:${account.id}`, storeId: account.store_id, accountId: account.id as number | null, label: `${stores.find(s => s.id === account.store_id)?.name ?? 'Store'} — ${account.name}` })),
  ]
  const createProfile = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!selectedRetailer) return
    try {
      await api.post<BrowserProfile>('/browser-profiles', {
        store_id: storeId,
        store_account_id: accountId,
        retailer: selectedRetailer,
      })
      load()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  const startLogin = async (profileId: number) => {
    setError(null)
    try {
      const res = await api.post<{ login_url?: string }>(
        `/browser-profiles/${profileId}/login/start`,
        {}
      )
      setLoginSuggestedUrl(res.login_url || 'https://www.walmart.com/')
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

  const startImport = async (
    profileId: number,
    opts?: { mode?: 'full' | 'unshipped'; autoApply?: boolean; maxPages?: number }
  ) => {
    setError(null)
    const mode = opts?.mode ?? 'full'
    const schedule = scheduleByProfile[profileId]
    const maxPages =
      opts?.maxPages ??
      (mode === 'full'
        ? (schedule?.full_check_max_pages ?? maxPagesByProfile[profileId] ?? profiles.find(p => p.id === profileId)?.full_check_max_pages ?? 3)
        : 1)
    const autoApply = opts?.autoApply ?? mode === 'unshipped'
    try {
      const res = await api.post<{ job_id: string }>(`/browser-profiles/${profileId}/import`, {
        mode,
        max_pages: maxPages,
        auto_apply: autoApply,
      })
      setJobsByProfile((prev) => ({
        ...prev,
        [profileId]: {
          id: res.job_id,
          profile_id: profileId,
          kind: mode === 'unshipped' ? 'unshipped' : 'import',
          status: 'queued',
          message: 'Queued…',
        },
      }))
      load()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  const updateScheduleDraft = (profileId: number, patch: Partial<ScheduleDraft>) => {
    setScheduleByProfile((prev) => ({
      ...prev,
      [profileId]: {
        ...(prev[profileId] ??
          scheduleDraftFromProfile(
            profiles.find((p) => p.id === profileId) ??
              ({
                full_check_enabled: false,
                full_check_max_pages: 3,
                unshipped_check_enabled: false,
                unshipped_check_interval_minutes: 360,
              } as BrowserProfile)
          )),
        ...patch,
      },
    }))
  }

  const saveSchedule = async (profileId: number) => {
    const draft = scheduleByProfile[profileId]
    if (!draft) return
    setSavingScheduleId(profileId)
    setError(null)
    const body: BrowserProfileScheduleUpdate = {
      full_check_enabled: draft.full_check_enabled,
      full_check_max_pages: Math.min(50, Math.max(1, draft.full_check_max_pages || 1)),
      unshipped_check_enabled: draft.unshipped_check_enabled,
      unshipped_check_interval_minutes: draft.unshipped_check_interval_minutes,
    }
    try {
      const updated = await api.patch<BrowserProfile>(`/browser-profiles/${profileId}`, body)
      await load()
      setScheduleByProfile((prev) => ({ ...prev, [profileId]: scheduleDraftFromProfile(updated) }))
      setMaxPagesByProfile((prev) => ({
        ...prev,
        [profileId]: updated.full_check_max_pages ?? 3,
      }))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSavingScheduleId(null)
    }
  }

  const changeProfileAccount = async (profileId: number, ownerKey: string) => {
    const owner = ownerOptions.find(option => option.key === ownerKey)
    if (!owner) return
    const storeAccountId = owner.accountId
    const profile = profiles.find((p) => p.id === profileId)
    if (!profile || (profile.store_id === owner.storeId && profile.store_account_id === storeAccountId)) {
      setEditingAccountProfileId(null)
      return
    }
    setSavingAccountProfileId(profileId)
    setError(null)
    try {
      await api.patch<BrowserProfile>(`/browser-profiles/${profileId}`, {
        store_id: owner.storeId,
        store_account_id: storeAccountId,
      })
      await load()
      setEditingAccountProfileId(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSavingAccountProfileId(null)
    }
  }

  const unlockProfile = async (profileId: number) => {
    setError(null)
    try {
      await api.post<BrowserProfile>(`/browser-profiles/${profileId}/unlock`, {})
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  const deleteProfile = async (profileId: number) => {
    if (!confirm('Delete this browser profile? Stored cookies for this store or account will be removed from the server.')) {
      return
    }
    try {
      await api.delete(`/browser-profiles/${profileId}`)
      load()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
        <h3 className="font-medium text-ink dark:text-gray-100">Browser profile</h3>
        <Link to="/browser-import-log?category=stores" className="text-sm text-brand-600 dark:text-brand-400 hover:underline">View log</Link>
      </div>
      {error && (
        <p role="alert" className="mb-4 text-sm text-red-600 dark:text-red-400">{error}</p>
      )}

      {ownerProfiles.length === 0 && <section className="mb-4 max-w-xl">
        <p className="text-xs text-ink-muted dark:text-gray-400 mb-3">Add a browser profile to log in, import orders, and schedule checks for this {accountId == null ? 'store' : 'account'}.</p>
        <form onSubmit={createProfile} className="flex flex-wrap gap-2 items-end">
          <label className="min-w-[8rem]">
            <span className="block text-xs text-ink-muted dark:text-gray-400 mb-1">Retailer</span>
            <select
              className="w-full rounded-lg border border-brand-200 dark:border-gray-600 dark:bg-gray-800 px-3 py-2 text-sm text-ink dark:text-gray-100"
              value={selectedRetailer}
              onChange={(e) =>
                setSelectedRetailer(e.target.value as '' | 'amazon' | 'walmart' | 'costco')
              }
            >
              <option value="">Select retailer…</option>
              <option value="walmart">Walmart</option>
              <option value="amazon">Amazon</option>
              <option value="costco">Costco</option>
            </select>
          </label>
          <button
            type="submit"
            disabled={!selectedRetailer}
            className="rounded-lg bg-brand-600 text-white px-3 py-2 text-sm font-medium hover:bg-brand-700 disabled:opacity-50"
          >
            Add profile
          </button>
        </form>
      </section>}

      <section>
          <ul className="space-y-3">
            {ownerProfiles.map((p) => {
              const job = jobsByProfile[p.id]
              const maxPages = maxPagesByProfile[p.id] ?? p.full_check_max_pages ?? 3
              const schedule = scheduleByProfile[p.id] ?? scheduleDraftFromProfile(p)
              const busy = p.status === 'importing' || p.status === 'login_in_progress'
              const scheduleDirty =
                schedule.full_check_enabled !== !!p.full_check_enabled ||
                schedule.full_check_max_pages !== (p.full_check_max_pages ?? 3) ||
                schedule.unshipped_check_enabled !== !!p.unshipped_check_enabled ||
                schedule.unshipped_check_interval_minutes !== (p.unshipped_check_interval_minutes ?? 360)
              return (
                <li
                  key={p.id}
                  className="rounded-lg border border-brand-200/80 dark:border-gray-700 p-4"
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      {editingAccountProfileId === p.id ? (
                        <div className="flex flex-wrap items-center gap-2">
                          <select
                            autoFocus
                            disabled={savingAccountProfileId === p.id}
                            className="rounded-lg border border-brand-200 dark:border-gray-600 dark:bg-gray-800 px-2 py-1 text-sm font-medium text-ink dark:text-gray-100"
                            value={p.store_account_id == null ? `store:${p.store_id}` : `account:${p.store_account_id}`}
                            onChange={(e) =>
                              void changeProfileAccount(p.id, e.target.value)
                            }
                            onKeyDown={(e) => {
                              if (e.key === 'Escape') setEditingAccountProfileId(null)
                            }}
                            aria-label="Profile owner"
                          >
                            {ownerOptions.filter(owner => !profiles.some(other => other.id !== p.id && other.store_id === owner.storeId && other.store_account_id === owner.accountId)).map(owner => (
                              <option key={owner.key} value={owner.key}>{owner.label}</option>
                            ))}
                          </select>
                          <button
                            type="button"
                            onClick={() => setEditingAccountProfileId(null)}
                            disabled={savingAccountProfileId === p.id}
                            className="text-xs text-ink-muted hover:underline disabled:opacity-50"
                          >
                            Cancel
                          </button>
                        </div>
                      ) : (
                        <div className="font-medium text-ink dark:text-gray-100 flex items-center gap-1">
                          <span>
                            {p.store_name ?? 'Store'}{p.store_account_name ? ` — ${p.store_account_name}` : ' — Store profile'}
                          </span>
                          <button
                            type="button"
                            onClick={() => setEditingAccountProfileId(p.id)}
                            disabled={busy}
                            className="p-1 rounded text-ink-muted hover:text-brand-600 hover:bg-brand-50 dark:hover:bg-brand-900/20 transition disabled:opacity-50"
                            title="Move profile"
                            aria-label="Move profile"
                          >
                            <svg
                              className="w-3.5 h-3.5"
                              fill="none"
                              stroke="currentColor"
                              viewBox="0 0 24 24"
                              aria-hidden
                            >
                              <path
                                strokeLinecap="round"
                                strokeLinejoin="round"
                                strokeWidth={2}
                                d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z"
                              />
                            </svg>
                          </button>
                        </div>
                      )}
                      <div className="text-xs text-ink-muted dark:text-gray-400 mt-0.5">
                        {p.retailer === 'walmart'
                          ? 'Walmart'
                          : p.retailer === 'amazon'
                            ? 'Amazon'
                            : p.retailer === 'costco'
                              ? 'Costco'
                              : p.retailer}
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
                      {p.status === 'login_in_progress' && <button type="button" onClick={() => {
                        setLoginSuggestedUrl(p.retailer === 'amazon' ? 'https://www.amazon.com/' : p.retailer === 'costco' ? 'https://www.costco.com/' : 'https://www.walmart.com/')
                        setLoginProfileId(p.id)
                      }} className="rounded-lg bg-brand-600 text-white px-3 py-1.5 text-sm">Resume login</button>}
                      <button
                        type="button"
                        onClick={() => startLogin(p.id)}
                        disabled={busy}
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
                        onClick={() =>
                          startImport(p.id, {
                            mode: 'full',
                            autoApply: false,
                            maxPages,
                          })
                        }
                        disabled={busy}
                        className="rounded-lg bg-brand-600 text-white px-3 py-1.5 text-sm font-medium hover:bg-brand-700 disabled:opacity-50"
                      >
                        Import now
                      </button>
                      <button
                        type="button"
                        onClick={() => unlockProfile(p.id)}
                        disabled={busy}
                        title="Clear stale browser lock files if this profile fails to launch (cookies kept)"
                        className="rounded-lg border border-brand-200 dark:border-gray-600 px-3 py-1.5 text-sm hover:bg-brand-50 dark:hover:bg-gray-800 disabled:opacity-50"
                      >
                        Unlock
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

                  <div className="mt-4 rounded-md border border-brand-100 dark:border-gray-700/80 bg-brand-50/40 dark:bg-gray-900/40 p-3 space-y-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <h3 className="text-sm font-medium text-ink dark:text-gray-100">Schedule</h3>
                      <button
                        type="button"
                        onClick={() => saveSchedule(p.id)}
                        disabled={savingScheduleId === p.id || !scheduleDirty}
                        className="rounded-md border border-brand-200 dark:border-gray-600 bg-white dark:bg-gray-900 px-2.5 py-1 text-xs font-medium text-ink dark:text-gray-100 hover:bg-brand-50 dark:hover:bg-gray-800 disabled:opacity-50"
                      >
                        {savingScheduleId === p.id ? 'Saving…' : 'Save schedule'}
                      </button>
                    </div>

                    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-sm text-ink dark:text-gray-200">
                      <label className="inline-flex items-center gap-1.5">
                        <input
                          type="checkbox"
                          checked={schedule.full_check_enabled}
                          onChange={(e) =>
                            updateScheduleDraft(p.id, { full_check_enabled: e.target.checked })
                          }
                          className="rounded border-brand-300 dark:border-gray-600"
                        />
                        <span>Full check</span>
                      </label>
                      <span className="text-xs text-ink-muted">Once daily</span>
                      <label className="inline-flex items-center gap-1 text-xs text-ink-muted dark:text-gray-400">
                        pages
                        <input
                          type="number"
                          min={1}
                          max={50}
                          value={schedule.full_check_max_pages}
                          onChange={(e) =>
                            updateScheduleDraft(p.id, {
                              full_check_max_pages: Math.min(
                                50,
                                Math.max(1, Number(e.target.value) || 1)
                              ),
                            })
                          }
                          className="w-14 rounded border border-brand-200 dark:border-gray-600 dark:bg-gray-800 px-1 py-1 text-sm text-ink dark:text-gray-100"
                        />
                      </label>
                      <span className="text-xs text-ink-muted dark:text-gray-500">
                        Last: {formatLastRun(p.full_check_last_run_at)} · Next: {p.full_check_next_run_at ? formatLastRun(p.full_check_next_run_at) : "Off"}
                      </span>
                      <button
                        type="button"
                        onClick={() =>
                          startImport(p.id, {
                            mode: 'full',
                            autoApply: true,
                            maxPages: schedule.full_check_max_pages,
                          })
                        }
                        disabled={busy}
                        className="rounded-md border border-brand-200 dark:border-gray-600 px-2 py-1 text-xs hover:bg-white dark:hover:bg-gray-800 disabled:opacity-50"
                      >
                        Run full check
                      </button>
                    </div>

                    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-sm text-ink dark:text-gray-200">
                      <label className="inline-flex items-center gap-1.5">
                        <input
                          type="checkbox"
                          checked={schedule.unshipped_check_enabled}
                          onChange={(e) =>
                            updateScheduleDraft(p.id, {
                              unshipped_check_enabled: e.target.checked,
                            })
                          }
                          className="rounded border-brand-300 dark:border-gray-600"
                        />
                        <span>Check unshipped</span>
                      </label>
                      <label className="inline-flex items-center gap-1 text-xs text-ink-muted dark:text-gray-400">
                        Every (minutes)
                        <input
                          type="number"
                          min={1}
                          max={525600}
                          value={schedule.unshipped_check_interval_minutes}
                          onChange={(e) => updateScheduleDraft(p.id, {
                            unshipped_check_interval_minutes: Math.min(525600, Math.max(1, Number(e.target.value) || 1)),
                          })}
                          className="w-24 rounded border border-brand-200 dark:border-gray-600 dark:bg-gray-800 px-1.5 py-1 text-sm text-ink dark:text-gray-100"
                        />
                      </label>
                      <span className="text-xs text-ink-muted dark:text-gray-500">
                        Last: {formatLastRun(p.unshipped_check_last_run_at)} · Next: {p.unshipped_check_next_run_at ? formatLastRun(p.unshipped_check_next_run_at) : "Off"}
                      </span>
                      <button
                        type="button"
                        onClick={() => startImport(p.id, { mode: 'unshipped', autoApply: true })}
                        disabled={busy}
                        className="rounded-md border border-brand-200 dark:border-gray-600 px-2 py-1 text-xs hover:bg-white dark:hover:bg-gray-800 disabled:opacity-50"
                      >
                        Run unshipped check
                      </button>
                    </div>
                    <p className="text-[11px] text-ink-muted dark:text-gray-500">
                      Checks are automatically staggered across store profiles. Saving schedule changes rebuilds
                      the next run times for all profiles. Full checks repeat daily; unshipped checks repeat at
                      their selected interval. Scheduled runs and Run buttons apply updates directly. Import now still opens Import Review
                      for manual review.
                    </p>
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
      </section>

      {loginProfileId != null && (
        <LiveBrowserView
          profileId={loginProfileId}
          suggestedUrl={loginSuggestedUrl}
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
