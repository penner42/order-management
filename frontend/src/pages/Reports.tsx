import { useEffect, useMemo, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { BuyingGroup, EffectiveItemStatus, Order, OrderListPage, Store, StoreAccount } from '../api/types'

const STATUS_FILTER_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'purchased', label: 'Purchased' },
  { value: 'shipped', label: 'Shipped' },
  { value: 'submitted', label: 'Submitted' },
  { value: 'scanned', label: 'Scanned' },
  { value: 'payment_requested', label: 'Payment requested' },
  { value: 'payment_sent', label: 'Payment sent' },
  { value: 'payment_received', label: 'Paid' },
  { value: 'canceled', label: 'Canceled' },
  { value: 'needs_return', label: 'Needs return' },
  { value: 'return_started', label: 'Return started' },
  { value: 'return_sent', label: 'Return sent' },
  { value: 'return_received', label: 'Return received' },
  { value: 'return_refunded', label: 'Refunded' },
]

const DEFAULT_STATUSES: EffectiveItemStatus[] = [
  'purchased',
  'shipped',
  'submitted',
  'scanned',
  'payment_requested',
  'payment_sent',
  'payment_received',
  'needs_return',
  'return_started',
  'return_sent',
  'return_received',
  'return_refunded',
]

const OPEN_ITEM_STATUSES = new Set<EffectiveItemStatus>([
  'purchased',
  'shipped',
  'submitted',
  'scanned',
  'payment_requested',
  'payment_sent',
  'needs_return',
  'return_started',
  'return_sent',
  'return_received',
])

function parseDecimal(value: string | null | undefined): number {
  if (value == null || String(value).trim() === '') return 0
  const parsed = Number.parseFloat(String(value))
  return Number.isNaN(parsed) ? 0 : parsed
}

function getEffectiveItemStatus(item: Order['items'][number]): EffectiveItemStatus {
  if (item.payment_received_at) return 'payment_received'
  if (item.payment_sent_at) return 'payment_sent'
  if (item.payment_requested_at) return 'payment_requested'
  return item.status
}

function toYyyyMmDdLocal(date: Date): string {
  const y = date.getFullYear()
  const m = String(date.getMonth() + 1).padStart(2, '0')
  const d = String(date.getDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}

function buildOrdersPath(opts: {
  filterStatuses: Set<string>
  filterBuyingGroups: Set<number>
  filterStores: Set<number>
  filterStoreAccounts: Set<number>
  filterDateFrom: string
  filterDateTo: string
  searchText: string
  perPage: number
}): string {
  const params = new URLSearchParams()
  const statuses = opts.filterStatuses.size > 0 ? [...opts.filterStatuses] : DEFAULT_STATUSES
  statuses.forEach((status) => params.append('status', status))
  opts.filterBuyingGroups.forEach((id) => params.append('buying_group_id', String(id)))
  opts.filterStores.forEach((id) => params.append('store_id', String(id)))
  opts.filterStoreAccounts.forEach((id) => params.append('store_account_id', String(id)))

  if (opts.filterDateFrom) params.set('date_from', opts.filterDateFrom)
  if (opts.filterDateTo) params.set('date_to', opts.filterDateTo)

  if (opts.filterDateFrom) {
    const start = new Date(`${opts.filterDateFrom}T00:00:00`)
    params.set('date_from_utc', start.toISOString())
  }
  if (opts.filterDateTo) {
    const end = new Date(`${opts.filterDateTo}T23:59:59.999`)
    params.set('date_to_utc', end.toISOString())
  }

  if (opts.searchText.trim()) params.set('q', opts.searchText.trim())
  params.set('page', '1')
  params.set('per_page', String(opts.perPage))
  return `/orders/paged?${params.toString()}`
}

function getMultiSelectValues(event: React.ChangeEvent<HTMLSelectElement>): string[] {
  return Array.from(event.target.selectedOptions).map((opt) => opt.value)
}

export default function Reports() {
  const location = useLocation()
  const navigate = useNavigate()

  const [orders, setOrders] = useState<Order[]>([])
  const [groups, setGroups] = useState<BuyingGroup[]>([])
  const [stores, setStores] = useState<Store[]>([])
  const [accountsByStore, setAccountsByStore] = useState<Record<number, StoreAccount[]>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [totalFilteredOrders, setTotalFilteredOrders] = useState(0)

  const [searchText, setSearchText] = useState('')
  const [filterStatuses, setFilterStatuses] = useState<Set<string>>(new Set())
  const [filterBuyingGroups, setFilterBuyingGroups] = useState<Set<number>>(new Set())
  const [filterStores, setFilterStores] = useState<Set<number>>(new Set())
  const [filterStoreAccounts, setFilterStoreAccounts] = useState<Set<number>>(new Set())
  const [filterDateFrom, setFilterDateFrom] = useState('')
  const [filterDateTo, setFilterDateTo] = useState('')

  useEffect(() => {
    const params = new URLSearchParams(location.search)
    const dateFromParam = params.get('date_from')
    const dateToParam = params.get('date_to')
    if (!dateFromParam && !dateToParam) {
      const now = new Date()
      const start = new Date(now)
      start.setDate(now.getDate() - 29)
      setFilterDateFrom(toYyyyMmDdLocal(start))
      setFilterDateTo(toYyyyMmDdLocal(now))
    } else {
      setFilterDateFrom(dateFromParam ?? '')
      setFilterDateTo(dateToParam ?? '')
    }
    setSearchText(params.get('q') ?? '')
    setFilterStatuses(new Set(params.getAll('status')))
    setFilterBuyingGroups(new Set(params.getAll('buying_group_id').map((v) => Number.parseInt(v, 10)).filter(Number.isFinite)))
    setFilterStores(new Set(params.getAll('store_id').map((v) => Number.parseInt(v, 10)).filter(Number.isFinite)))
    setFilterStoreAccounts(new Set(params.getAll('store_account_id').map((v) => Number.parseInt(v, 10)).filter(Number.isFinite)))
  }, [location.search])

  useEffect(() => {
    const params = new URLSearchParams()
    if (searchText.trim()) params.set('q', searchText.trim())
    if (filterDateFrom) params.set('date_from', filterDateFrom)
    if (filterDateTo) params.set('date_to', filterDateTo)
    filterStatuses.forEach((status) => params.append('status', status))
    filterBuyingGroups.forEach((id) => params.append('buying_group_id', String(id)))
    filterStores.forEach((id) => params.append('store_id', String(id)))
    filterStoreAccounts.forEach((id) => params.append('store_account_id', String(id)))
    navigate({ search: params.toString() ? `?${params.toString()}` : '' }, { replace: true })
  }, [
    searchText,
    filterDateFrom,
    filterDateTo,
    filterStatuses,
    filterBuyingGroups,
    filterStores,
    filterStoreAccounts,
    navigate,
  ])

  useEffect(() => {
    Promise.all([api.get<BuyingGroup[]>('/buying-groups'), api.get<Store[]>('/stores')])
      .then(async ([groupsData, storesData]) => {
        setGroups(groupsData)
        setStores(storesData)
        const byStore: Record<number, StoreAccount[]> = {}
        await Promise.all(
          storesData.map((store) =>
            api.get<StoreAccount[]>(`/stores/${store.id}/accounts`).then((accounts) => {
              byStore[store.id] = accounts
            })
          )
        )
        setAccountsByStore(byStore)
      })
      .catch((err: unknown) => {
        console.error(err)
        setError('Unable to load report filters.')
      })
  }, [])

  const ordersPath = useMemo(
    () =>
      buildOrdersPath({
        filterStatuses,
        filterBuyingGroups,
        filterStores,
        filterStoreAccounts,
        filterDateFrom,
        filterDateTo,
        searchText,
        perPage: 0,
      }),
    [filterStatuses, filterBuyingGroups, filterStores, filterStoreAccounts, filterDateFrom, filterDateTo, searchText]
  )

  useEffect(() => {
    setLoading(true)
    setError(null)
    api
      .get<OrderListPage>(ordersPath)
      .then((data) => {
        setOrders(data.items)
        setTotalFilteredOrders(data.total)
      })
      .catch((err: unknown) => {
        console.error(err)
        setError('Unable to load report data.')
      })
      .finally(() => setLoading(false))
  }, [ordersPath])

  const visibleOrders = useMemo(() => orders.filter((order) => (order.items ?? []).length > 0), [orders])

  const metrics = useMemo(() => {
    let revenue = 0
    let totalCost = 0
    let openOrders = 0

    for (const order of visibleOrders) {
      let hasOpenItems = false
      const discount = Math.max(0, parseDecimal(order.order_discount))
      for (const item of order.items ?? []) {
        const qty = Math.max(0, item.quantity ?? 1)
        const itemRevenue = parseDecimal(item.price_sold) * qty
        const itemCost = (parseDecimal(item.price_paid) + parseDecimal(item.shipping) + parseDecimal(item.sales_tax)) * qty
        revenue += itemRevenue
        totalCost += itemCost
        if (OPEN_ITEM_STATUSES.has(getEffectiveItemStatus(item))) hasOpenItems = true
      }
      totalCost = Math.max(0, totalCost - discount)
      if (hasOpenItems) openOrders += 1
    }

    const orderCount = visibleOrders.length
    const avgOrderValue = orderCount === 0 ? 0 : revenue / orderCount

    return { revenue, orderCount, avgOrderValue, openOrders, totalCost }
  }, [visibleOrders])

  const applyDatePreset = (preset: 'today' | 'week' | 'past7' | 'past30' | 'month' | 'year' | 'lastYear') => {
    const now = new Date()
    const today = toYyyyMmDdLocal(now)
    switch (preset) {
      case 'today':
        setFilterDateFrom(today)
        setFilterDateTo(today)
        break
      case 'week': {
        const start = new Date(now)
        start.setDate(now.getDate() - now.getDay())
        setFilterDateFrom(toYyyyMmDdLocal(start))
        setFilterDateTo(today)
        break
      }
      case 'past7': {
        const d = new Date(now)
        d.setDate(now.getDate() - 6)
        setFilterDateFrom(toYyyyMmDdLocal(d))
        setFilterDateTo(today)
        break
      }
      case 'past30': {
        const d = new Date(now)
        d.setDate(now.getDate() - 29)
        setFilterDateFrom(toYyyyMmDdLocal(d))
        setFilterDateTo(today)
        break
      }
      case 'month':
        setFilterDateFrom(toYyyyMmDdLocal(new Date(now.getFullYear(), now.getMonth(), 1)))
        setFilterDateTo(today)
        break
      case 'year':
        setFilterDateFrom(`${now.getFullYear()}-01-01`)
        setFilterDateTo(today)
        break
      case 'lastYear': {
        const previousYear = now.getFullYear() - 1
        setFilterDateFrom(`${previousYear}-01-01`)
        setFilterDateTo(`${previousYear}-12-31`)
        break
      }
    }
  }

  const clearFilters = () => {
    setSearchText('')
    setFilterStatuses(new Set())
    setFilterBuyingGroups(new Set())
    setFilterStores(new Set())
    setFilterStoreAccounts(new Set())
    setFilterDateFrom('')
    setFilterDateTo('')
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <h1 className="text-xl font-semibold text-ink dark:text-gray-100">Reports</h1>
        <button
          type="button"
          className="px-3 py-2 rounded-md border border-brand-200 dark:border-gray-600 text-sm text-ink-muted dark:text-gray-200"
          onClick={clearFilters}
        >
          Clear filters
        </button>
      </div>

      <section className="bg-white dark:bg-gray-800 rounded-xl border border-brand-200/80 dark:border-gray-700 p-4 md:p-5">
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
          <label className="text-sm text-ink-muted dark:text-gray-300">
            Search
            <input
              type="text"
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
              placeholder="Order number, notes, item description"
              className="mt-1 w-full rounded-md border border-brand-200 dark:border-gray-600 bg-white dark:bg-gray-900 px-3 py-2 text-sm"
            />
          </label>

          <label className="text-sm text-ink-muted dark:text-gray-300">
            Date from
            <input
              type="date"
              value={filterDateFrom}
              onChange={(e) => setFilterDateFrom(e.target.value)}
              className="mt-1 w-full rounded-md border border-brand-200 dark:border-gray-600 bg-white dark:bg-gray-900 px-3 py-2 text-sm"
            />
          </label>

          <label className="text-sm text-ink-muted dark:text-gray-300">
            Date to
            <input
              type="date"
              value={filterDateTo}
              onChange={(e) => setFilterDateTo(e.target.value)}
              className="mt-1 w-full rounded-md border border-brand-200 dark:border-gray-600 bg-white dark:bg-gray-900 px-3 py-2 text-sm"
            />
          </label>

          <label className="text-sm text-ink-muted dark:text-gray-300">
            Status (multi-select)
            <select
              multiple
              value={[...filterStatuses]}
              onChange={(e) => setFilterStatuses(new Set(getMultiSelectValues(e)))}
              className="mt-1 w-full min-h-[116px] rounded-md border border-brand-200 dark:border-gray-600 bg-white dark:bg-gray-900 px-2 py-2 text-sm"
            >
              {STATUS_FILTER_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>

          <label className="text-sm text-ink-muted dark:text-gray-300">
            Buying groups (multi-select)
            <select
              multiple
              value={[...filterBuyingGroups].map(String)}
              onChange={(e) =>
                setFilterBuyingGroups(
                  new Set(getMultiSelectValues(e).map((value) => Number.parseInt(value, 10)).filter(Number.isFinite))
                )
              }
              className="mt-1 w-full min-h-[116px] rounded-md border border-brand-200 dark:border-gray-600 bg-white dark:bg-gray-900 px-2 py-2 text-sm"
            >
              {groups.map((group) => (
                <option key={group.id} value={group.id}>
                  {group.name}
                </option>
              ))}
            </select>
          </label>

          <label className="text-sm text-ink-muted dark:text-gray-300">
            Stores (multi-select)
            <select
              multiple
              value={[...filterStores].map(String)}
              onChange={(e) =>
                setFilterStores(
                  new Set(getMultiSelectValues(e).map((value) => Number.parseInt(value, 10)).filter(Number.isFinite))
                )
              }
              className="mt-1 w-full min-h-[116px] rounded-md border border-brand-200 dark:border-gray-600 bg-white dark:bg-gray-900 px-2 py-2 text-sm"
            >
              {stores.map((store) => (
                <option key={store.id} value={store.id}>
                  {store.name}
                </option>
              ))}
            </select>
          </label>

          <label className="text-sm text-ink-muted dark:text-gray-300">
            Store accounts (multi-select)
            <select
              multiple
              value={[...filterStoreAccounts].map(String)}
              onChange={(e) =>
                setFilterStoreAccounts(
                  new Set(getMultiSelectValues(e).map((value) => Number.parseInt(value, 10)).filter(Number.isFinite))
                )
              }
              className="mt-1 w-full min-h-[116px] rounded-md border border-brand-200 dark:border-gray-600 bg-white dark:bg-gray-900 px-2 py-2 text-sm"
            >
              {stores.flatMap((store) =>
                (accountsByStore[store.id] ?? []).map((account) => (
                  <option key={account.id} value={account.id}>
                    {store.name} ({account.name})
                  </option>
                ))
              )}
            </select>
          </label>

          <div className="text-sm text-ink-muted dark:text-gray-300">
            Date presets
            <div className="mt-1 flex flex-wrap gap-2">
              <button type="button" className="px-2 py-1 rounded-md border border-brand-200 dark:border-gray-600" onClick={() => applyDatePreset('today')}>Today</button>
              <button type="button" className="px-2 py-1 rounded-md border border-brand-200 dark:border-gray-600" onClick={() => applyDatePreset('week')}>Week</button>
              <button type="button" className="px-2 py-1 rounded-md border border-brand-200 dark:border-gray-600" onClick={() => applyDatePreset('past7')}>Past 7</button>
              <button type="button" className="px-2 py-1 rounded-md border border-brand-200 dark:border-gray-600" onClick={() => applyDatePreset('past30')}>Past 30</button>
              <button type="button" className="px-2 py-1 rounded-md border border-brand-200 dark:border-gray-600" onClick={() => applyDatePreset('month')}>Month</button>
              <button type="button" className="px-2 py-1 rounded-md border border-brand-200 dark:border-gray-600" onClick={() => applyDatePreset('year')}>Year</button>
              <button type="button" className="px-2 py-1 rounded-md border border-brand-200 dark:border-gray-600" onClick={() => applyDatePreset('lastYear')}>Last year</button>
            </div>
          </div>
        </div>
      </section>

      <section className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
        <div className="bg-white dark:bg-gray-800 rounded-xl border border-brand-200/80 dark:border-gray-700 p-4">
          <div className="text-xs uppercase tracking-wide text-ink-muted">Revenue</div>
          <div className="mt-2 text-2xl font-semibold text-ink dark:text-gray-100">${metrics.revenue.toFixed(2)}</div>
        </div>
        <div className="bg-white dark:bg-gray-800 rounded-xl border border-brand-200/80 dark:border-gray-700 p-4">
          <div className="text-xs uppercase tracking-wide text-ink-muted">Orders</div>
          <div className="mt-2 text-2xl font-semibold text-ink dark:text-gray-100">{metrics.orderCount}</div>
        </div>
        <div className="bg-white dark:bg-gray-800 rounded-xl border border-brand-200/80 dark:border-gray-700 p-4">
          <div className="text-xs uppercase tracking-wide text-ink-muted">Average order value</div>
          <div className="mt-2 text-2xl font-semibold text-ink dark:text-gray-100">${metrics.avgOrderValue.toFixed(2)}</div>
        </div>
        <div className="bg-white dark:bg-gray-800 rounded-xl border border-brand-200/80 dark:border-gray-700 p-4">
          <div className="text-xs uppercase tracking-wide text-ink-muted">Open/Pending orders</div>
          <div className="mt-2 text-2xl font-semibold text-ink dark:text-gray-100">{metrics.openOrders}</div>
        </div>
      </section>

      <section className="bg-white dark:bg-gray-800 rounded-xl border border-brand-200/80 dark:border-gray-700 p-4">
        <div className="flex items-center justify-between gap-2 mb-3">
          <h2 className="text-lg font-semibold text-ink dark:text-gray-100">Filtered orders</h2>
          <div className="text-sm text-ink-muted dark:text-gray-300">Results: {totalFilteredOrders}</div>
        </div>

        {loading && <div className="text-sm text-ink-muted dark:text-gray-300">Loading report data...</div>}
        {error && <div className="text-sm text-red-600 dark:text-red-400">{error}</div>}

        {!loading && !error && (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="text-left border-b border-brand-100 dark:border-gray-700">
                  <th className="px-2 py-2 font-medium text-ink-muted">Order #</th>
                  <th className="px-2 py-2 font-medium text-ink-muted">Purchase date</th>
                  <th className="px-2 py-2 font-medium text-ink-muted">Store</th>
                  <th className="px-2 py-2 font-medium text-ink-muted">Buying group</th>
                  <th className="px-2 py-2 font-medium text-ink-muted">Status</th>
                  <th className="px-2 py-2 font-medium text-ink-muted">Items</th>
                  <th className="px-2 py-2 font-medium text-ink-muted">Revenue</th>
                  <th className="px-2 py-2 font-medium text-ink-muted">Total cost</th>
                </tr>
              </thead>
              <tbody>
                {visibleOrders.length === 0 && (
                  <tr>
                    <td className="px-2 py-4 text-ink-muted" colSpan={8}>
                      No matching orders.
                    </td>
                  </tr>
                )}
                {visibleOrders.map((order) => {
                  const orderRevenue = (order.items ?? []).reduce((sum, item) => {
                    const qty = Math.max(0, item.quantity ?? 1)
                    return sum + parseDecimal(item.price_sold) * qty
                  }, 0)
                  const orderCostRaw = (order.items ?? []).reduce((sum, item) => {
                    const qty = Math.max(0, item.quantity ?? 1)
                    return sum + (parseDecimal(item.price_paid) + parseDecimal(item.shipping) + parseDecimal(item.sales_tax)) * qty
                  }, 0)
                  const orderCost = Math.max(0, orderCostRaw - Math.max(0, parseDecimal(order.order_discount)))
                  const itemCount = (order.items ?? []).reduce((sum, item) => sum + Math.max(0, item.quantity ?? 1), 0)

                  return (
                    <tr key={order.id} className="border-b border-brand-50 dark:border-gray-700/60">
                      <td className="px-2 py-2">{order.store_order_number || '—'}</td>
                      <td className="px-2 py-2">{order.purchase_date ? order.purchase_date.slice(0, 10) : '—'}</td>
                      <td className="px-2 py-2">{order.store?.name ?? '—'}</td>
                      <td className="px-2 py-2">{order.buying_group?.name ?? '—'}</td>
                      <td className="px-2 py-2 capitalize">{order.status}</td>
                      <td className="px-2 py-2">{itemCount}</td>
                      <td className="px-2 py-2">${orderRevenue.toFixed(2)}</td>
                      <td className="px-2 py-2">${orderCost.toFixed(2)}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}
