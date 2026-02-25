import React, { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { Shipment, Item } from '../api/types'

const STATUS_LABELS: Record<string, string> = {
  purchased: 'Purchased',
  shipped: 'Shipped',
  submitted: 'Submitted',
  scanned: 'Scanned',
  payment_requested: 'Payment requested',
  payment_sent: 'Payment sent',
  payment_received: 'Paid',
  canceled: 'Canceled',
  needs_return: 'Needs return',
  return_started: 'Return started',
  return_sent: 'Return sent',
  return_received: 'Return received',
  return_refunded: 'Refunded',
}

function canMarkScanned(shipment: Shipment): boolean {
  return shipment.delivered_at != null
}

function MarkScannedModal({
  item,
  onApply,
  onClose,
}: {
  item: Item
  onApply: (receiptId: string) => Promise<void>
  onClose: () => void
}) {
  const [receiptId, setReceiptId] = useState(item.receipt_id ?? '')
  const [applying, setApplying] = useState(false)
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-white dark:bg-gray-800 rounded-xl shadow-xl p-6 max-w-md w-full mx-4 border border-brand-200/80 dark:border-gray-700"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-lg font-medium text-ink mb-2">Mark as Scanned</h3>
        <p className="text-sm text-ink-muted mb-2">{item.description || 'Item'}</p>
        <label className="block text-sm font-medium text-ink mb-2">Receipt ID (optional)</label>
        <input
          type="text"
          value={receiptId}
          onChange={(e) => setReceiptId(e.target.value)}
          placeholder="Receipt ID"
          className="w-full h-10 rounded-lg border border-brand-200 dark:border-gray-600 px-3 py-2 text-sm bg-white dark:bg-gray-700 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500 mb-6"
        />
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className="px-3 py-1.5 border border-brand-300 dark:border-gray-600 rounded-lg text-sm text-ink hover:bg-brand-50 dark:hover:bg-gray-700">
            Cancel
          </button>
          <button
            type="button"
            onClick={async () => {
              setApplying(true)
              try {
                await onApply(receiptId)
              } finally {
                setApplying(false)
              }
            }}
            disabled={applying}
            className="px-3 py-1.5 bg-brand-600 text-white rounded-lg text-sm hover:bg-brand-700 disabled:opacity-50"
          >
            {applying ? 'Applying…' : 'Apply'}
          </button>
        </div>
      </div>
    </div>
  )
}

export default function Shipments() {
  const [shipments, setShipments] = useState<Shipment[]>([])
  const [loading, setLoading] = useState(true)
  const [scanItemModal, setScanItemModal] = useState<Item | null>(null)

  const fetchShipments = () => {
    api.get<Shipment[]>('/shipments').then(setShipments).catch(console.error).finally(() => setLoading(false))
  }

  useEffect(() => {
    setLoading(true)
    api.get<Shipment[]>('/shipments').then(setShipments).catch(console.error).finally(() => setLoading(false))
  }, [])

  const markItemScanned = async (item: Item, receiptId: string) => {
    const now = new Date().toISOString().slice(0, 19)
    await api.patch<Item>(`/items/${item.id}`, {
      status: 'scanned',
      scanned_at: now,
      receipt_id: receiptId.trim() || null,
    })
    setScanItemModal(null)
    fetchShipments()
  }

  if (loading) return <div className="text-ink-muted">Loading shipments…</div>

  return (
    <div>
      <h1 className="text-2xl font-semibold text-ink mb-8">Shipments</h1>
      <div className="w-fit min-w-[600px] max-w-full bg-white dark:bg-gray-800 rounded-xl border border-brand-200/80 dark:border-gray-700 shadow-sm overflow-hidden">
        <table className="min-w-0">
          <thead className="bg-brand-100/50 dark:bg-gray-700/50 border-b border-brand-200/80 dark:border-gray-700">
            <tr>
              <th className="text-left py-3 px-4 text-sm font-medium text-ink">Shipment</th>
              <th className="text-left py-3 px-4 text-sm font-medium text-ink">Tracking</th>
              <th className="text-left py-3 px-4 text-sm font-medium text-ink">Shipped</th>
              <th className="text-left py-3 px-4 text-sm font-medium text-ink">Delivered</th>
              <th className="text-left py-3 px-4 text-sm font-medium text-ink">Items</th>
            </tr>
          </thead>
          <tbody>
            {shipments.length === 0 ? (
              <tr>
                <td colSpan={5} className="py-12 text-center text-ink-muted">
                  No shipments yet. Create shipments from order items when you ship.
                </td>
              </tr>
            ) : (
              shipments.map((s) => (
                <React.Fragment key={s.id}>
                  <tr className="border-b border-brand-100 last:border-0 hover:bg-brand-50/50">
                    <td className="py-3 px-4 font-medium text-brand-700">#{s.id}</td>
                    <td className="py-3 px-4 text-sm">{s.tracking_number ?? '—'}</td>
                    <td className="py-3 px-4 text-sm text-ink-muted">
                      {s.shipped_at ? new Date(s.shipped_at).toLocaleDateString() : '—'}
                    </td>
                    <td className="py-3 px-4 text-sm text-ink-muted">
                      {s.delivered_at ? new Date(s.delivered_at).toLocaleDateString() : '—'}
                    </td>
                    <td className="py-3 px-4 text-sm">{s.shipment_items?.length ?? 0} items</td>
                  </tr>
                  {s.shipment_items?.length ? (
                    <tr key={`${s.id}-items`}>
                      <td colSpan={5} className="py-0 px-4 pb-3 bg-brand-50/30 dark:bg-gray-800/50">
                        <table className="w-full text-sm">
                          <thead>
                            <tr className="text-left border-b border-brand-200 dark:border-gray-600">
                              <th className="py-2 px-2 font-medium text-ink-muted w-12">Qty</th>
                              <th className="py-2 px-2 font-medium text-ink-muted">Description</th>
                              <th className="py-2 px-2 font-medium text-ink-muted">Status</th>
                              <th className="py-2 px-2 font-medium text-ink-muted w-24"> </th>
                            </tr>
                          </thead>
                          <tbody>
                            {s.shipment_items.map((si) => {
                              const item = si.item
                              if (!item) return null
                              return (
                                <tr key={si.id} className="border-b border-brand-100 last:border-0">
                                  <td className="py-2 px-2">{item.quantity ?? 1}</td>
                                  <td className="py-2 px-2 text-ink">{item.description || '—'}</td>
                                  <td className="py-2 px-2 text-ink-muted">{STATUS_LABELS[item.status] ?? item.status}</td>
                                  <td className="py-2 px-2">
                                    {canMarkScanned(s) && (
                                      <button
                                        type="button"
                                        onClick={() => setScanItemModal(item)}
                                        className="text-xs font-medium text-brand-600 hover:text-brand-700 dark:text-brand-400"
                                      >
                                        Mark scanned
                                      </button>
                                    )}
                                  </td>
                                </tr>
                              )
                            })}
                          </tbody>
                        </table>
                      </td>
                    </tr>
                  ) : null}
                </React.Fragment>
              ))
            )}
          </tbody>
        </table>
      </div>
      {scanItemModal && (
        <MarkScannedModal
          item={scanItemModal}
          onApply={(receiptId) => markItemScanned(scanItemModal, receiptId)}
          onClose={() => setScanItemModal(null)}
        />
      )}
    </div>
  )
}
