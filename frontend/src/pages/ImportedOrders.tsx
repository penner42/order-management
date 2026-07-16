import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, getStoredToken } from '../api/client'
import type { Order } from '../api/types'

async function downloadInvoice(order: Order) {
  const token = getStoredToken()
  const res = await fetch(`/api/orders/${order.id}/invoice`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || 'Failed to download invoice')
  }
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `invoice-${order.store_order_number || order.id}.pdf`
  a.click()
  URL.revokeObjectURL(url)
}

export default function ImportedOrders() {
  const [orders, setOrders] = useState<Order[]>([])
  const [loading, setLoading] = useState(true)
  const [downloadingId, setDownloadingId] = useState<number | null>(null)

  useEffect(() => {
    setLoading(true)
    api
      .get<Order[]>('/orders?order_status=imported')
      .then(setOrders)
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [])

  async function handleDownloadInvoice(order: Order) {
    if (downloadingId != null) return
    setDownloadingId(order.id)
    try {
      await downloadInvoice(order)
    } catch (err) {
      console.error(err)
    } finally {
      setDownloadingId(null)
    }
  }

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
        <h1 className="text-xl font-semibold text-ink dark:text-gray-100">Imported Orders</h1>
        <Link
          to="/"
          className="text-sm text-brand-600 dark:text-brand-400 hover:underline"
        >
          Back to Orders
        </Link>
      </div>
      <p className="text-sm text-ink-muted dark:text-gray-400 mb-4">
        Orders brought in via import (e.g. browser extension). They are hidden from the main Orders list.
      </p>
      <div className="w-full bg-white dark:bg-gray-800 rounded-xl border border-brand-200/80 dark:border-gray-700 shadow-sm overflow-hidden">
        <div className="w-full overflow-x-auto">
        <table className="min-w-full">
          <thead className="bg-brand-100/50 dark:bg-gray-700/50 border-b border-brand-200/80 dark:border-gray-700">
            <tr>
              <th className="text-left py-3 px-4 text-sm font-medium text-ink">Order</th>
              <th className="text-left py-3 px-4 text-sm font-medium text-ink">Store</th>
              <th className="text-left py-3 px-4 text-sm font-medium text-ink">Items</th>
              <th className="text-left py-3 px-4 text-sm font-medium text-ink">Created</th>
              <th className="text-left py-3 px-4 text-sm font-medium text-ink">Invoice</th>
            </tr>
          </thead>
          <tbody>
            {orders.length === 0 ? (
              <tr>
                <td colSpan={5} className="py-12 text-center text-ink-muted">
                  No imported orders yet.
                </td>
              </tr>
            ) : (
              orders.map((o) => (
                <tr
                  key={o.id}
                  className="border-b border-brand-100 last:border-0 hover:bg-brand-50/50 dark:hover:bg-gray-600"
                >
                  <td className="py-3 px-4 font-medium text-brand-700 dark:text-brand-400">
                    {o.store_order_number ?? '—'}
                  </td>
                  <td className="py-3 px-4 text-sm text-ink dark:text-gray-200">
                    {o.store?.name ?? '—'}
                  </td>
                  <td className="py-3 px-4 text-ink-muted">
                    {o.items?.reduce((sum, i) => sum + (i.quantity ?? 1), 0) ?? 0} items
                  </td>
                  <td className="py-3 px-4 text-sm text-ink-muted">
                    {new Date(o.created_at).toLocaleString()}
                  </td>
                  <td className="py-3 px-4">
                    {o.has_invoice ? (
                      <button
                        type="button"
                        onClick={() => handleDownloadInvoice(o)}
                        disabled={downloadingId === o.id}
                        className="p-1.5 rounded text-ink-muted hover:text-brand-600 hover:bg-brand-100 dark:hover:bg-gray-600 transition disabled:opacity-50"
                        title="Download invoice"
                        aria-label="Download invoice"
                      >
                        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                        </svg>
                      </button>
                    ) : (
                      <span className="text-ink-muted">—</span>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
        </div>
      </div>
    </div>
  )
}
