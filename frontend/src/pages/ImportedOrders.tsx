import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { Order } from '../api/types'

export default function ImportedOrders() {
  const [orders, setOrders] = useState<Order[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    api
      .get<Order[]>('/orders?order_status=imported')
      .then(setOrders)
      .catch(console.error)
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
      <div className="bg-white dark:bg-gray-800 rounded-xl border border-brand-200/80 dark:border-gray-700 shadow-sm overflow-hidden">
        <table className="w-full">
          <thead className="bg-brand-100/50 dark:bg-gray-700/50 border-b border-brand-200/80 dark:border-gray-700">
            <tr>
              <th className="text-left py-3 px-4 text-sm font-medium text-ink">Order</th>
              <th className="text-left py-3 px-4 text-sm font-medium text-ink">Store</th>
              <th className="text-left py-3 px-4 text-sm font-medium text-ink">Items</th>
              <th className="text-left py-3 px-4 text-sm font-medium text-ink">Created</th>
            </tr>
          </thead>
          <tbody>
            {orders.length === 0 ? (
              <tr>
                <td colSpan={4} className="py-12 text-center text-ink-muted">
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
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
