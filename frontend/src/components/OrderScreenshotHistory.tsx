import { useEffect, useRef, useState } from 'react'
import { api, getStoredToken } from '../api/client'

type Capture = { id: number; captured_at: string; summary: string }

export function OrderScreenshotHistory({ orderId, orderNumber, onClose }: {
  orderId: number; orderNumber: string; onClose: () => void
}) {
  const [captures, setCaptures] = useState<Capture[]>([])
  const [selected, setSelected] = useState<Capture | null>(null)
  const [imageUrl, setImageUrl] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [imageLoading, setImageLoading] = useState(false)
  const [error, setError] = useState('')
  const dialog = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let active = true
    api.get<Capture[]>(`/orders/${orderId}/screenshots`).then(rows => {
      if (active) { setCaptures(rows); setSelected(rows[0] ?? null) }
    }).catch(err => { if (active) setError(String(err.message || err)) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [orderId])

  useEffect(() => {
    if (!selected) return
    const controller = new AbortController()
    let objectUrl: string | null = null
    setImageUrl(null)
    setError('')
    setImageLoading(true)
    const token = getStoredToken()
    fetch(`/api/orders/${orderId}/screenshots/${selected.id}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {}, signal: controller.signal,
    }).then(async response => {
      if (!response.ok) throw new Error('Could not load screenshot')
      const blob = await response.blob()
      if (controller.signal.aborted) return
      objectUrl = URL.createObjectURL(blob)
      setImageUrl(objectUrl)
    }).catch(err => { if (!controller.signal.aborted) setError(String(err.message || err)) })
      .finally(() => { if (!controller.signal.aborted) setImageLoading(false) })
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [orderId, selected])

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    dialog.current?.focus()
    return () => previous?.focus()
  }, [])

  useEffect(() => {
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
      if (event.key !== 'Tab') return
      const focusable = dialog.current?.querySelectorAll<HTMLElement>('button, select, a[href]')
      if (!focusable?.length) return
      const first = focusable[0], last = focusable[focusable.length - 1]
      const outside = !dialog.current?.contains(document.activeElement)
      if (event.shiftKey && (outside || document.activeElement === first || document.activeElement === dialog.current)) {
        event.preventDefault(); last.focus()
      } else if (!event.shiftKey && (outside || document.activeElement === last)) {
        event.preventDefault(); first.focus()
      }
    }
    document.addEventListener('keydown', handleKey)
    return () => document.removeEventListener('keydown', handleKey)
  }, [onClose])

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div ref={dialog} role="dialog" aria-modal="true" aria-labelledby="screenshot-history-title" tabIndex={-1}
        className="bg-white dark:bg-gray-800 rounded-xl shadow-xl p-5 w-full max-w-6xl max-h-[95vh] overflow-auto"
        onClick={event => event.stopPropagation()}>
        <div className="flex items-center justify-between gap-4 mb-4">
          <h2 id="screenshot-history-title" className="text-lg font-semibold text-ink">Screenshot history · {orderNumber}</h2>
          <button type="button" onClick={onClose} className="px-3 py-1.5 rounded border">Close</button>
        </div>
        {loading && <p role="status">Loading screenshot history…</p>}
        {!loading && !captures.length && !error && <p>No screenshots yet. The next browser check will try to capture this order.</p>}
        {!!captures.length && <div className="flex flex-wrap items-center gap-3 mb-3">
          <label>Version <select aria-label="Screenshot version" value={selected?.id ?? ''}
            className="border rounded p-2 bg-white dark:bg-gray-900 ml-2"
            onChange={event => setSelected(captures.find(row => row.id === Number(event.target.value)) ?? null)}>
            {captures.map(row => <option key={row.id} value={row.id}>{new Date(row.captured_at).toLocaleString()} · {row.summary}</option>)}
          </select></label>
          {imageUrl && <a href={imageUrl} download={`order-${orderNumber}-${selected?.id}.png`}
            className="text-brand-600 underline">Download PNG</a>}
        </div>}
        {error && <p role="alert" className="text-red-600">{error}</p>}
        {imageLoading && <p role="status">Loading screenshot…</p>}
        {imageUrl && <img src={imageUrl} alt={`Order ${orderNumber}, captured ${selected ? new Date(selected.captured_at).toLocaleString() : ''}`}
          className="w-full h-auto border rounded" />}
      </div>
    </div>
  )
}
