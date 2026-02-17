import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { Shipment } from '../api/types'

export default function Shipments() {
  const [shipments, setShipments] = useState<Shipment[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.get<Shipment[]>('/shipments').then(setShipments).catch(console.error).finally(() => setLoading(false))
  }, [])

  if (loading) return <div className="text-ink-muted">Loading shipments…</div>

  return (
    <div>
      <h1 className="text-2xl font-semibold text-ink mb-8">Shipments</h1>
<div className="bg-white dark:bg-gray-800 rounded-xl border border-brand-200/80 dark:border-gray-700 shadow-sm overflow-hidden">
      <table className="w-full">
          <thead className="bg-brand-100/50 dark:bg-gray-700/50 border-b border-brand-200/80 dark:border-gray-700">
            <tr>
              <th className="text-left py-3 px-4 text-sm font-medium text-ink">Shipment</th>
              <th className="text-left py-3 px-4 text-sm font-medium text-ink">Tracking</th>
              <th className="text-left py-3 px-4 text-sm font-medium text-ink">Shipped</th>
              <th className="text-left py-3 px-4 text-sm font-medium text-ink">Items</th>
            </tr>
          </thead>
          <tbody>
            {shipments.length === 0 ? (
              <tr>
                <td colSpan={4} className="py-12 text-center text-ink-muted">
                  No shipments yet. Create shipments from order items when you ship.
                </td>
              </tr>
            ) : (
              shipments.map((s) => (
                <tr key={s.id} className="border-b border-brand-100 last:border-0 hover:bg-brand-50/50">
                  <td className="py-3 px-4 font-medium text-brand-700">#{s.id}</td>
                  <td className="py-3 px-4 text-sm">{s.tracking_number ?? '—'}</td>
                  <td className="py-3 px-4 text-sm text-ink-muted">
                    {s.shipped_at ? new Date(s.shipped_at).toLocaleDateString() : '—'}
                  </td>
                  <td className="py-3 px-4 text-sm">{s.shipment_items?.length ?? 0} items</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
