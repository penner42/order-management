import { useMemo } from 'react'
import { Link } from 'react-router-dom'

/** Order details payload sent from the browser extension (e.g. Walmart). */
export interface ExtensionOrderDetails {
  orderNumber: string
  id?: string
  orderDate?: string
  account?: string
  address?: string
  paymentMethod?: string
  deliveryAddresses?: { fullName?: string; addressString?: string }[]
  items: {
    name: string
    quantity: number
    price: string
    trackingNumber?: string
  }[]
}

/** Parse price string (e.g. "$1.23" or "1.23") to number for calculations. Totals are display-only, not saved. */
function parsePrice(s: string | null | undefined): number {
  if (s == null || String(s).trim() === '') return 0
  const n = parseFloat(String(s).replace(/[^0-9.-]/g, ''))
  return Number.isNaN(n) ? 0 : n
}

function decodeHashPayload(): ExtensionOrderDetails | null {
  const hash = window.location.hash.slice(1)
  if (!hash) return null
  try {
    const json = decodeURIComponent(escape(atob(hash)))
    const data = JSON.parse(json) as ExtensionOrderDetails
    if (!data || !Array.isArray(data.items)) return null
    return data
  } catch {
    return null
  }
}

export default function ImportPreview() {
  const order = useMemo(() => decodeHashPayload(), [])

  if (!order) {
    return (
      <div className="max-w-2xl mx-auto">
        <h1 className="text-xl font-semibold text-ink dark:text-gray-100 mb-2">
          Import preview
        </h1>
        <p className="text-ink-muted dark:text-gray-400 mb-4">
          No order data in this link. Use the Order Manager browser extension on
          a store order page: get order details, then click “Send to Order
          Manager”.
        </p>
        <Link
          to="/"
          className="text-brand-600 dark:text-brand-400 hover:underline text-sm"
        >
          Back to Orders
        </Link>
      </div>
    )
  }

  const { orderNumber, orderDate, account, address, paymentMethod, items } =
    order

  return (
    <div className="max-w-2xl mx-auto">
      <div className="flex items-center justify-between gap-4 mb-6">
        <h1 className="text-xl font-semibold text-ink dark:text-gray-100">
          Import preview — Order {orderNumber}
        </h1>
        <Link
          to="/"
          className="text-sm text-brand-600 dark:text-brand-400 hover:underline"
        >
          Back to Orders
        </Link>
      </div>

      <p className="text-sm text-ink-muted dark:text-gray-400 mb-6">
        Order data from the browser extension. Nothing has been saved to the
        database.
      </p>

      <div className="bg-white dark:bg-gray-800 rounded-lg border border-brand-200/80 dark:border-gray-700 overflow-hidden">
        <div className="p-4 sm:p-6 space-y-4">
          {orderDate && (
            <div>
              <span className="text-xs font-medium text-ink-muted dark:text-gray-400 uppercase tracking-wide">
                Order date
              </span>
              <p className="text-ink dark:text-gray-200 mt-0.5">{orderDate}</p>
            </div>
          )}
          {account && (
            <div>
              <span className="text-xs font-medium text-ink-muted dark:text-gray-400 uppercase tracking-wide">
                Account
              </span>
              <p className="text-ink dark:text-gray-200 mt-0.5">{account}</p>
            </div>
          )}
          {address && (
            <div>
              <span className="text-xs font-medium text-ink-muted dark:text-gray-400 uppercase tracking-wide">
                Delivery address
              </span>
              <pre className="text-ink dark:text-gray-200 mt-0.5 whitespace-pre-wrap font-sans text-sm">
                {address}
              </pre>
            </div>
          )}
          {paymentMethod && (
            <div>
              <span className="text-xs font-medium text-ink-muted dark:text-gray-400 uppercase tracking-wide">
                Payment
              </span>
              <p className="text-ink dark:text-gray-200 mt-0.5">
                {paymentMethod}
              </p>
            </div>
          )}

          <div>
            <span className="text-xs font-medium text-ink-muted dark:text-gray-400 uppercase tracking-wide">
              Items ({items.length})
            </span>
            <ul className="mt-2 divide-y divide-brand-200/80 dark:divide-gray-700">
              {items.map((item, idx) => {
                const qty = Math.max(0, item.quantity ?? 1)
                const unitCost = parsePrice(item.price)
                const lineTotal = unitCost * qty
                return (
                  <li
                    key={idx}
                    className="py-3 first:pt-0 last:pb-0 flex flex-col gap-0.5"
                  >
                    <span className="font-medium text-ink dark:text-gray-200">
                      {item.name || '—'}
                    </span>
                    <span className="text-sm text-ink-muted dark:text-gray-400">
                      Unit {item.price} × {qty} = ${lineTotal.toFixed(2)}
                      {item.trackingNumber
                        ? ` · ${item.trackingNumber}`
                        : ''}
                    </span>
                  </li>
                )
              })}
            </ul>
            {items.length > 0 && (() => {
              const orderTotal = items.reduce(
                (sum, item) =>
                  sum +
                  parsePrice(item.price) * Math.max(0, item.quantity ?? 1),
                0
              )
              return (
                <div className="mt-3 pt-3 border-t border-brand-200/80 dark:border-gray-700">
                  <span className="text-sm font-medium text-ink dark:text-gray-200">
                    Order total (calculated): ${orderTotal.toFixed(2)}
                  </span>
                </div>
              )
            })()}
          </div>
        </div>
      </div>

      <div className="mt-8">
        <span className="text-xs font-medium text-ink-muted dark:text-gray-400 uppercase tracking-wide">
          Full JSON
        </span>
        <pre className="mt-2 p-4 bg-gray-100 dark:bg-gray-900 rounded-lg border border-brand-200/80 dark:border-gray-700 text-xs text-ink dark:text-gray-300 font-mono overflow-x-auto max-h-[24rem] overflow-y-auto">
          {JSON.stringify(order, null, 2)}
        </pre>
      </div>
    </div>
  )
}
