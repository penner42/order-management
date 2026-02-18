import { useState } from 'react'
import { useAuth } from '../contexts/AuthContext'
import { api } from '../api/client'
import type { Order } from '../api/types'

function escapeCsv(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return ''
  const s = String(value)
  if (/[",\n\r]/.test(s)) return `"${s.replace(/"/g, '""')}"`
  return s
}

function buildOrdersCsv(orders: Order[]): string {
  const headers = [
    'order_store_name',
    'order_store_account_name',
    'order_buying_group_name',
    'order_store_order_number',
    'order_purchase_date',
    'order_notes',
    'order_created_at',
    'order_updated_at',
    'order_payment_summary',
    'item_price_paid',
    'item_price_sold',
    'item_shipping',
    'item_sales_tax',
    'item_status',
    'item_quantity',
    'item_description',
    'item_submission_id',
    'item_receipt_id',
    'item_created_at',
    'item_updated_at',
    'item_purchased_at',
    'item_shipped_at',
    'item_submitted_at',
    'item_delivered_at',
    'item_scanned_at',
    'item_payment_requested_at',
    'item_payment_sent_at',
    'item_payment_received_at',
    'item_canceled_at',
    'item_needs_return_at',
    'item_return_started_at',
    'item_return_sent_at',
    'item_return_received_at',
    'item_return_refunded_at',
  ]
  const rows: string[][] = [headers]
  for (const order of orders) {
    const paymentSummary =
      order.order_payments
        ?.map(
          (opm) =>
            `${opm.payment_method?.label ?? 'Unknown'}` +
            (opm.amount != null ? `: ${opm.amount}` : '')
        )
        .join('; ') ?? ''
    const orderCells = [
      order.store?.name ?? '',
      order.store_account?.name ?? '',
      order.buying_group?.name ?? '',
      order.store_order_number ?? '',
      order.purchase_date ?? '',
      order.notes ?? '',
      order.created_at ?? '',
      order.updated_at ?? '',
      paymentSummary,
    ]
    for (const item of order.items ?? []) {
      rows.push([
        ...orderCells.map((c) => escapeCsv(c)),
        escapeCsv(item.price_paid),
        escapeCsv(item.price_sold),
        escapeCsv(item.shipping),
        escapeCsv(item.sales_tax),
        escapeCsv(item.status),
        escapeCsv(item.quantity),
        escapeCsv(item.description),
        escapeCsv(item.submission_id),
        escapeCsv(item.receipt_id),
        escapeCsv(item.created_at),
        escapeCsv(item.updated_at),
        escapeCsv(item.purchased_at),
        escapeCsv(item.shipped_at),
        escapeCsv(item.submitted_at),
        escapeCsv(item.delivered_at),
        escapeCsv(item.scanned_at),
        escapeCsv(item.payment_requested_at),
        escapeCsv(item.payment_sent_at),
        escapeCsv(item.payment_received_at),
        escapeCsv(item.canceled_at),
        escapeCsv(item.needs_return_at),
        escapeCsv(item.return_started_at),
        escapeCsv(item.return_sent_at),
        escapeCsv(item.return_received_at),
        escapeCsv(item.return_refunded_at),
      ])
    }
  }
  return rows.map((row) => row.join(',')).join('\n')
}

function downloadCsv(csv: string, filename: string) {
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

export default function Profile() {
  const { user } = useAuth()
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [downloadingCsv, setDownloadingCsv] = useState(false)
  const [csvError, setCsvError] = useState<string | null>(null)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setMessage(null)
    if (newPassword !== confirmPassword) {
      setMessage({ type: 'error', text: 'New password and confirmation do not match.' })
      return
    }
    if (newPassword.length < 6) {
      setMessage({ type: 'error', text: 'New password must be at least 6 characters.' })
      return
    }
    setSubmitting(true)
    try {
      await api.patch('/users/me/password', {
        current_password: currentPassword,
        new_password: newPassword,
      })
      setCurrentPassword('')
      setNewPassword('')
      setConfirmPassword('')
      setMessage({ type: 'success', text: 'Password changed successfully.' })
    } catch (err) {
      setMessage({
        type: 'error',
        text: err instanceof Error ? err.message : 'Failed to change password.',
      })
    } finally {
      setSubmitting(false)
    }
  }

  async function handleDownloadOrdersCsv() {
    setCsvError(null)
    setDownloadingCsv(true)
    try {
      const orders = await api.get<Order[]>('/users/me/orders')
      const csv = buildOrdersCsv(orders)
      const filename = `orders-${new Date().toISOString().slice(0, 10)}.csv`
      downloadCsv(csv, filename)
    } catch (err) {
      setCsvError(err instanceof Error ? err.message : 'Failed to download orders.')
    } finally {
      setDownloadingCsv(false)
    }
  }

  return (
    <div>
      <h1 className="text-2xl font-semibold text-ink mb-8">Profile</h1>
      <div className="max-w-md space-y-6">
        <div className="bg-white dark:bg-gray-800 rounded-xl border border-brand-200/80 dark:border-gray-700 p-6">
          <h2 className="text-lg font-medium text-ink dark:text-gray-100 mb-4">Change password</h2>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label htmlFor="current-password" className="block text-sm font-medium text-ink dark:text-gray-300 mb-1">
                Current password
              </label>
              <input
                id="current-password"
                type="password"
                autoComplete="current-password"
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
                className="w-full px-3 py-2 border border-brand-200 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-ink dark:text-gray-100"
                required
              />
            </div>
            <div>
              <label htmlFor="new-password" className="block text-sm font-medium text-ink dark:text-gray-300 mb-1">
                New password
              </label>
              <input
                id="new-password"
                type="password"
                autoComplete="new-password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                className="w-full px-3 py-2 border border-brand-200 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-ink dark:text-gray-100"
                required
              />
            </div>
            <div>
              <label htmlFor="confirm-password" className="block text-sm font-medium text-ink dark:text-gray-300 mb-1">
                Confirm new password
              </label>
              <input
                id="confirm-password"
                type="password"
                autoComplete="new-password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                className="w-full px-3 py-2 border border-brand-200 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-ink dark:text-gray-100"
                required
              />
            </div>
            {message && (
              <p
                className={`text-sm ${message.type === 'success' ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'}`}
                role="alert"
              >
                {message.text}
              </p>
            )}
            <button
              type="submit"
              disabled={submitting}
              className="px-4 py-2 bg-brand-600 text-white rounded-lg font-medium hover:bg-brand-700 disabled:opacity-50 transition"
            >
              {submitting ? 'Changing…' : 'Change password'}
            </button>
          </form>
        </div>
        <div className="bg-white dark:bg-gray-800 rounded-xl border border-brand-200/80 dark:border-gray-700 p-6">
          <h2 className="text-lg font-medium text-ink dark:text-gray-100 mb-4">Export orders</h2>
          <p className="text-sm text-ink-muted dark:text-gray-400 mb-4">
            Download a CSV of all your orders with line items and related data (store, account, payments, item status dates).
          </p>
          {csvError && (
            <p className="text-sm text-red-600 dark:text-red-400 mb-4" role="alert">
              {csvError}
            </p>
          )}
          <button
            type="button"
            onClick={handleDownloadOrdersCsv}
            disabled={downloadingCsv}
            className="px-4 py-2 bg-brand-600 text-white rounded-lg font-medium hover:bg-brand-700 disabled:opacity-50 transition"
          >
            {downloadingCsv ? 'Preparing…' : 'Download orders CSV'}
          </button>
        </div>
        <div className="text-sm text-ink-muted dark:text-gray-400">
          Logged in as <span className="font-medium text-ink dark:text-gray-200">{user?.username}</span>
          {user?.role && (
            <span className="ml-1">
              ({user.role})
            </span>
          )}
        </div>
      </div>
    </div>
  )
}
