/**
 * Orders page — horizontal strip per order.
 * Left: vertical order box (order #, store, account, group, date, payment).
 * Right: full line-items table (one row per item).
 */
import React, { useEffect, useMemo, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { api } from '../api/client'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { SearchableCombobox } from '../components/SearchableCombobox'
import type {
  Order,
  BuyingGroup,
  Shipment,
  Store,
  StoreAccount,
  PaymentMethod,
  PaymentMethodNested,
  Item,
  ItemStatus,
  EffectiveItemStatus,
} from '../api/types'
import { getTrackingInfoBulk } from '../utils/tracking'
import type { TrackingInfo } from '../utils/tracking'

/** Format UTC ISO date string for datetime-local input in the user's local timezone. */
function toLocalDatetimeLocal(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  const h = String(d.getHours()).padStart(2, '0')
  const min = String(d.getMinutes()).padStart(2, '0')
  return `${y}-${m}-${day}T${h}:${min}`
}

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

const STATUS_PROGRESSION: EffectiveItemStatus[] = [
  'shipped',
  'submitted',
  'scanned',
  'payment_requested',
  'payment_sent',
  'payment_received',
]

// shipped_at / delivered_at live on Shipment; payment dates live on Payment
const STATUS_TO_DATE_FIELD: Record<string, keyof Item> = {
  submitted: 'submitted_at',
  scanned: 'scanned_at',
}

function getEffectiveItemStatus(item: Item): EffectiveItemStatus {
  if (item.payment_received_at) return 'payment_received'
  if (item.payment_sent_at) return 'payment_sent'
  if (item.payment_requested_at) return 'payment_requested'
  return item.status
}

// Row background by status for at-a-glance color coding (light + dark)
function getStatusRowClass(status: string): string {
  switch (status) {
    case 'purchased':
      return 'bg-gray-100/80 dark:bg-gray-700/50'
    case 'shipped':
    case 'submitted':
      return 'bg-yellow-100/80 dark:bg-yellow-600/30'
    case 'scanned':
    case 'payment_requested':
    case 'payment_sent':
      return 'bg-indigo-100/80 dark:bg-indigo-900/35'
    case 'payment_received':
      return 'bg-emerald-100/80 dark:bg-emerald-900/30'
    case 'canceled':
    case 'needs_return':
    case 'return_started':
    case 'return_sent':
    case 'return_received':
    case 'return_refunded':
      return 'bg-slate-100/70 dark:bg-slate-800/40'
    default:
      return 'bg-gray-100/80 dark:bg-gray-700/50'
  }
}

// Input/select background to match row status color (light: slightly more opaque for readability; dark: same as row)
function getStatusInputClass(status: string): string {
  switch (status) {
    case 'purchased':
      return 'bg-gray-50/90 dark:bg-gray-700/50'
    case 'shipped':
    case 'submitted':
      return 'bg-yellow-100/90 dark:bg-yellow-600/30'
    case 'scanned':
    case 'payment_requested':
    case 'payment_sent':
      return 'bg-indigo-50/90 dark:bg-indigo-900/35'
    case 'payment_received':
      return 'bg-emerald-50/90 dark:bg-emerald-900/30'
    case 'canceled':
    case 'needs_return':
    case 'return_started':
    case 'return_sent':
    case 'return_received':
    case 'return_refunded':
      return 'bg-slate-50/90 dark:bg-slate-800/40'
    default:
      return 'bg-gray-50/90 dark:bg-gray-700/50'
  }
}

function getNextStatus(current: EffectiveItemStatus): EffectiveItemStatus | null {
  const idx = STATUS_PROGRESSION.indexOf(current)
  if (idx < 0 || idx >= STATUS_PROGRESSION.length - 1) return null
  return STATUS_PROGRESSION[idx + 1]
}

function copyToClipboard(text: string): void {
  if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
    void navigator.clipboard.writeText(text).catch(console.error)
    return
  }
  const el = document.createElement('textarea')
  el.value = text
  el.setAttribute('readonly', '')
  el.style.position = 'absolute'
  el.style.left = '-9999px'
  document.body.appendChild(el)
  el.select()
  try {
    document.execCommand('copy')
  } finally {
    document.body.removeChild(el)
  }
}

const DEFAULT_STATUSES: EffectiveItemStatus[] = [
  'purchased', 'shipped', 'submitted', 'scanned',
  'payment_requested', 'payment_sent', 'payment_received',
  'needs_return', 'return_started', 'return_sent', 'return_received', 'return_refunded',
]

/** Item-only statuses (for status dropdown; payment status is on Payment). */
const ITEM_STATUSES_FOR_EDIT: ItemStatus[] = [
  'purchased', 'shipped', 'submitted', 'scanned', 'canceled',
  'needs_return', 'return_started', 'return_sent', 'return_received', 'return_refunded',
]

const STATUS_FILTER_OPTIONS: [string, string][] = [
  ['purchased', 'Purchased'],
  ['shipped', 'Shipped'],
  ['submitted', 'Submitted'],
  ['scanned', 'Scanned'],
  ['payment_requested', 'Payment requested'],
  ['payment_sent', 'Payment sent'],
  ['payment_received', 'Paid'],
  ['canceled', 'Canceled'],
  ['needs_return', 'Needs return'],
  ['return_started', 'Return started'],
  ['return_sent', 'Return sent'],
  ['return_received', 'Return received'],
  ['return_refunded', 'Refunded'],
]

function buildOrdersPath(opts: {
  filterStatuses: Set<string>
  filterBuyingGroups: Set<number>
  filterStores: Set<number>
  filterStoreAccounts: Set<number>
  filterDateFrom: string
  filterDateTo: string
  searchText: string
}): string {
  const params = new URLSearchParams()
  const statuses =
    opts.filterStatuses.size > 0 ? [...opts.filterStatuses] : DEFAULT_STATUSES
  statuses.forEach((s) => params.append('status', s))
  opts.filterBuyingGroups.forEach((id) => params.append('buying_group_id', String(id)))
  opts.filterStores.forEach((id) => params.append('store_id', String(id)))
  opts.filterStoreAccounts.forEach((id) => params.append('store_account_id', String(id)))
  if (opts.filterDateFrom) params.set('date_from', opts.filterDateFrom)
  if (opts.filterDateTo) params.set('date_to', opts.filterDateTo)
  // Send UTC bounds so backend compares correctly to UTC purchase_date
  if (opts.filterDateFrom) {
    const start = new Date(opts.filterDateFrom + 'T00:00:00')
    params.set('date_from_utc', start.toISOString())
  }
  if (opts.filterDateTo) {
    const end = new Date(opts.filterDateTo + 'T23:59:59.999')
    params.set('date_to_utc', end.toISOString())
  }
  if (opts.searchText.trim()) params.set('q', opts.searchText.trim())
  return `/orders?${params.toString()}`
}

export default function Orders() {
  const [orders, setOrders] = useState<Order[]>([])
  const [groups, setGroups] = useState<BuyingGroup[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedItemIds, setSelectedItemIds] = useState<Set<number>>(new Set())
  const [bulkActionByOrder, setBulkActionByOrder] = useState<Record<number, { action: string; tracking: string; shippedAt: string }>>({})
  const [bulkActionShippingOrderId, setBulkActionShippingOrderId] = useState<number | null>(null)
  const [bulkStatusModal, setBulkStatusModal] = useState<{ order: Order; itemIds: number[] } | null>(null)
  const [bulkScanModal, setBulkScanModal] = useState<{ order: Order; itemIds: number[] } | null>(null)
  const [scanSingleItemModal, setScanSingleItemModal] = useState<Item | null>(null)
  const [scanReceiptModal, setScanReceiptModal] = useState<{
    group: { key: string; label: string; trackingNumber: string | null; items: Item[] }
  } | null>(null)
  const [submitShipmentModal, setSubmitShipmentModal] = useState<{
    group: { key: string; label: string; trackingNumber: string | null; items: Item[] }
  } | null>(null)
  const [trackingEdits, setTrackingEdits] = useState<Record<number, string>>({})
  const [savingTrackingId, setSavingTrackingId] = useState<number | null>(null)
  const [shipments, setShipments] = useState<Shipment[]>([])
  const [copyingId, setCopyingId] = useState<number | null>(null)
  const [stores, setStores] = useState<Store[]>([])
  const [paymentMethods, setPaymentMethods] = useState<PaymentMethod[]>([])
  const [accountsByStore, setAccountsByStore] = useState<Record<number, StoreAccount[]>>({})
  const [orderEdits, setOrderEdits] = useState<Record<number, { store_order_number?: string; purchase_date?: string }>>({})
  const [savingOrderId, setSavingOrderId] = useState<number | null>(null)
  const [itemEdits, setItemEdits] = useState<Record<number, { quantity?: number; description?: string; price_paid?: string; price_sold?: string; shipping?: string; sales_tax?: string; status?: ItemStatus }>>({})
  const [paymentEdits, setPaymentEdits] = useState<Record<number, { payment_method_id: number; amount: string }[]>>({})
  const [savingItemId, setSavingItemId] = useState<number | null>(null)
  const [deletingItemId, setDeletingItemId] = useState<number | null>(null)
  const [copyingItemId, setCopyingItemId] = useState<number | null>(null)
  const [confirmDeleteItemId, setConfirmDeleteItemId] = useState<number | null>(null)
  const [confirmBulkDeleteItemIds, setConfirmBulkDeleteItemIds] = useState<number[] | null>(null)
  const [confirmDeleteOrderId, setConfirmDeleteOrderId] = useState<number | null>(null)
  const [splitModalItem, setSplitModalItem] = useState<Item | null>(null)
  const [deletingOrderId, setDeletingOrderId] = useState<number | null>(null)
  const [advancingGroupKey, setAdvancingGroupKey] = useState<string | null>(null)
  const [addAccountModal, setAddAccountModal] = useState<{ orderId: number; storeName: string; storeId: number } | null>(null)
  const [addAccountName, setAddAccountName] = useState('')
  const [addingAccount, setAddingAccount] = useState(false)
  const [pendingAccountSelection, setPendingAccountSelection] = useState<{ orderId: number; storeId: number; accountId: number } | null>(null)
  const [creatingOrder, setCreatingOrder] = useState(false)
  const [filterStatuses, setFilterStatuses] = useState<Set<string>>(new Set())
  const [filterBuyingGroups, setFilterBuyingGroups] = useState<Set<number>>(new Set())
  const [filterStores, setFilterStores] = useState<Set<number>>(new Set())
  const [filterStoreAccounts, setFilterStoreAccounts] = useState<Set<number>>(new Set())
  const [filterDateFrom, setFilterDateFrom] = useState('')
  const [filterDateTo, setFilterDateTo] = useState('')
  const [filterStatusOpen, setFilterStatusOpen] = useState(false)
  const [filterBuyingGroupOpen, setFilterBuyingGroupOpen] = useState(false)
  const [filterStoreOpen, setFilterStoreOpen] = useState(false)
  const [filterDateOpen, setFilterDateOpen] = useState(false)
  const location = useLocation()
  const [searchText, setSearchText] = useState(() => {
    const state = location.state as { orderSearch?: string } | null
    if (state?.orderSearch) return state.orderSearch
    const q = new URLSearchParams(location.search).get('q')
    return q ?? ''
  })
  const [searchDebounced, setSearchDebounced] = useState(() => {
    const state = location.state as { orderSearch?: string } | null
    if (state?.orderSearch) return state.orderSearch
    const q = new URLSearchParams(location.search).get('q')
    return q ?? ''
  })
  const searchTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const searchInputRef = useRef<HTMLInputElement>(null)
  const restoreSearchFocusRef = useRef<{ start: number; end: number } | null>(null)
  const filterStatusRef = useRef<HTMLDivElement>(null)
  const filterBuyingGroupRef = useRef<HTMLDivElement>(null)
  const filterStoreRef = useRef<HTMLDivElement>(null)
  const filterDateRef = useRef<HTMLDivElement>(null)
  const paymentSaveTimeouts = useRef<Record<number, ReturnType<typeof setTimeout>>>({})
  const storeDropdownRefs = useRef<Record<number, { setOpen: (open: boolean) => void; selectItem: (item: any) => void }>>({})
  const [trackingInfoByItemId, setTrackingInfoByItemId] = useState<Record<number, TrackingInfo | null>>({})

  useEffect(() => {
    if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current)
    searchTimeoutRef.current = setTimeout(() => {
      setSearchDebounced(searchText)
      searchTimeoutRef.current = null
    }, 300)
    return () => {
      if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current)
    }
  }, [searchText])

  const ordersPath = useMemo(
    () =>
      buildOrdersPath({
        filterStatuses,
        filterBuyingGroups,
        filterStores,
        filterStoreAccounts,
        filterDateFrom,
        filterDateTo,
        searchText: searchDebounced,
      }),
    [filterStatuses, filterBuyingGroups, filterStores, filterStoreAccounts, filterDateFrom, filterDateTo, searchDebounced]
  )

  /** Format date as YYYY-MM-DD in local time (for display and date inputs). */
  const toYyyyMmDdLocal = (d: Date) =>
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  const applyDatePreset = (preset: 'today' | 'week' | 'past7' | 'past30' | 'month' | 'year' | 'lastYear') => {
    const now = new Date()
    const today = toYyyyMmDdLocal(now)
    switch (preset) {
      case 'today':
        setFilterDateFrom(today)
        setFilterDateTo(today)
        break
      case 'week': {
        const day = now.getDay() // 0 = Sunday, 1 = Monday, …
        const start = new Date(now)
        start.setDate(now.getDate() - day) // week starts on Sunday
        setFilterDateFrom(toYyyyMmDdLocal(start))
        setFilterDateTo(today)
        break
      }
      case 'past7': {
        const d = new Date(now)
        d.setDate(d.getDate() - 6)
        setFilterDateFrom(toYyyyMmDdLocal(d))
        setFilterDateTo(today)
        break
      }
      case 'past30': {
        const d = new Date(now)
        d.setDate(d.getDate() - 29)
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
        const y = now.getFullYear() - 1
        setFilterDateFrom(`${y}-01-01`)
        setFilterDateTo(`${y}-12-31`)
        break
      }
    }
    setFilterDateOpen(false)
  }

  const dateRangeLabel =
    !filterDateFrom && !filterDateTo
      ? 'Date range'
      : filterDateFrom && filterDateTo
        ? `${filterDateFrom} – ${filterDateTo}`
        : filterDateFrom
          ? `${filterDateFrom} –`
          : `– ${filterDateTo}`

  const toggleFilterStatus = (status: string) => {
    setFilterStatuses((prev) => {
      const next = new Set(prev)
      if (next.has(status)) next.delete(status)
      else next.add(status)
      return next
    })
  }
  const toggleFilterBuyingGroup = (id: number) => {
    setFilterBuyingGroups((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }
  const toggleFilterStore = (id: number) => {
    if (filterStores.has(id)) {
      const accountIds = (accountsByStore[id] ?? []).map((a) => a.id)
      setFilterStoreAccounts((prev) => {
        const next = new Set(prev)
        accountIds.forEach((aid) => next.delete(aid))
        return next
      })
      setFilterStores((prev) => {
        const next = new Set(prev)
        next.delete(id)
        return next
      })
    } else {
      setFilterStores((prev) => {
        const next = new Set(prev)
        next.add(id)
        return next
      })
    }
  }
  const toggleFilterStoreAccount = (accountId: number, storeId: number) => {
    const storeAccounts = accountsByStore[storeId] ?? []
    const storeIsSelected = filterStores.has(storeId)
    const accountCurrentlyChecked = filterStoreAccounts.has(accountId) || storeIsSelected
    if (accountCurrentlyChecked) {
      if (storeIsSelected) {
        setFilterStores((prev) => {
          const next = new Set(prev)
          next.delete(storeId)
          return next
        })
        setFilterStoreAccounts((prev) => {
          const next = new Set(prev)
          storeAccounts.forEach((a) => {
            if (a.id !== accountId) next.add(a.id)
          })
          return next
        })
      } else {
        setFilterStoreAccounts((prev) => {
          const next = new Set(prev)
          next.delete(accountId)
          return next
        })
      }
    } else {
      setFilterStoreAccounts((prev) => {
        const next = new Set(prev)
        next.add(accountId)
        const allSubaccountsSelected =
          storeAccounts.length > 0 && storeAccounts.every((a) => next.has(a.id))
        if (allSubaccountsSelected) {
          setFilterStores((prevStores) => {
            const nextStores = new Set(prevStores)
            nextStores.add(storeId)
            return nextStores
          })
          storeAccounts.forEach((a) => next.delete(a.id))
        }
        return next
      })
    }
  }

  const hasActiveFilters =
    filterStatuses.size > 0 ||
    filterBuyingGroups.size > 0 ||
    filterStores.size > 0 ||
    filterStoreAccounts.size > 0 ||
    !!filterDateFrom ||
    !!filterDateTo ||
    !!searchText.trim()

  const resetFilters = () => {
    setFilterStatuses(new Set())
    setFilterBuyingGroups(new Set())
    setFilterStores(new Set())
    setFilterStoreAccounts(new Set())
    setFilterDateFrom('')
    setFilterDateTo('')
    setSearchText('')
    setSearchDebounced('')
    setFilterStatusOpen(false)
    setFilterBuyingGroupOpen(false)
    setFilterStoreOpen(false)
    setFilterDateOpen(false)
  }

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      const target = e.target as Node
      if (
        filterStatusRef.current?.contains(target) ||
        filterBuyingGroupRef.current?.contains(target) ||
        filterStoreRef.current?.contains(target) ||
        filterDateRef.current?.contains(target)
      )
        return
      setFilterStatusOpen(false)
      setFilterBuyingGroupOpen(false)
      setFilterStoreOpen(false)
      setFilterDateOpen(false)
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const loadAccountsForStore = (storeId: number) =>
    api.get<StoreAccount[]>(`/stores/${storeId}/accounts`).then((list) => {
      setAccountsByStore((prev) => ({ ...prev, [storeId]: list }))
    })

  useEffect(() => {
    Promise.all([
      api.get<BuyingGroup[]>('/buying-groups'),
      api.get<Shipment[]>('/shipments'),
      api.get<Store[]>('/stores'),
      api.get<PaymentMethod[]>('/payment-methods'),
    ])
      .then(async ([groupsData, shipmentsData, storesData, pmData]) => {
        setGroups(groupsData)
        setShipments(shipmentsData)
        setStores(storesData)
        setPaymentMethods(pmData)
        const allAccountsByStore: Record<number, StoreAccount[]> = {}
        await Promise.all(
          storesData.map((store) =>
            api.get<StoreAccount[]>(`/stores/${store.id}/accounts`).then((list) => {
              allAccountsByStore[store.id] = list
            })
          )
        )
        setAccountsByStore(allAccountsByStore)
      })
      .catch(console.error)
  }, [])

  useEffect(() => {
    const el = searchInputRef.current
    const hadFocus = el && document.activeElement === el
    const start = el?.selectionStart ?? null
    const end = el?.selectionEnd ?? null
    if (hadFocus && start != null && end != null) {
      restoreSearchFocusRef.current = { start, end }
    }
    setLoading(true)
    api
      .get<Order[]>(ordersPath)
      .then(setOrders)
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [ordersPath])

  useEffect(() => {
    if (!loading && restoreSearchFocusRef.current) {
      const { start, end } = restoreSearchFocusRef.current
      restoreSearchFocusRef.current = null
      const input = searchInputRef.current
      if (input) {
        input.focus()
        input.setSelectionRange(start, end)
      }
    }
  }, [loading])

  useEffect(() => {
    if (pendingAccountSelection) {
      const ref = storeDropdownRefs.current[pendingAccountSelection.orderId]
      if (ref) {
        const newAccountOption = {
          id: pendingAccountSelection.accountId,
          name: `${stores.find((s) => s.id === pendingAccountSelection.storeId)?.name ?? ''} (${(accountsByStore[pendingAccountSelection.storeId] ?? []).find((a) => a.id === pendingAccountSelection.accountId)?.name ?? ''})`,
          type: 'account' as const,
          storeId: pendingAccountSelection.storeId,
          accountId: pendingAccountSelection.accountId,
        }
        ref.selectItem(newAccountOption)
        ref.setOpen(true)
        setPendingAccountSelection(null)
      }
    }
  }, [pendingAccountSelection, stores, accountsByStore])

  const flatPaymentMethods = useMemo(
    () => paymentMethods.flatMap((pm) => [pm, ...(pm.sub_methods ?? [])]),
    [paymentMethods]
  )
  const paymentMethodOptions = useMemo(() => {
    return flatPaymentMethods.map((pm: PaymentMethod | PaymentMethodNested) => {
      const parent = pm.parent_id != null ? paymentMethods.find((p) => p.id === pm.parent_id) : null
      const name = parent ? `${parent.label} — ${pm.label}` : pm.label
      return { id: pm.id, name }
    })
  }, [paymentMethods, flatPaymentMethods])

  const itemIdToTracking = useMemo(
    () =>
      shipments.reduce<Record<number, string>>((acc, s) => {
        const tn = s.tracking_number?.trim()
        if (tn) for (const si of s.shipment_items || []) acc[si.item_id] = tn
        return acc
      }, {}),
    [shipments]
  )
  const getTracking = (itemId: number) => itemIdToTracking[itemId] ?? ''

  useEffect(() => {
    const fetchTrackingInfos = async () => {
      const next: Record<number, TrackingInfo | null> = {}
      const orderedPairs: { itemId: number; tn: string }[] = []

      for (const order of orders) {
        for (const item of order.items ?? []) {
          const trackingRaw = trackingEdits[item.id] ?? getTracking(item.id)
          const tn = (trackingRaw || '').trim()
          if (!tn) {
            next[item.id] = null
            continue
          }
          orderedPairs.push({ itemId: item.id, tn })
        }
      }

      if (orderedPairs.length === 0) {
        setTrackingInfoByItemId(next)
        return
      }

      const uniqueTNs = [...new Set(orderedPairs.map((p) => p.tn))]
      const results = await getTrackingInfoBulk(uniqueTNs)
      const tnToInfo = new Map<string, TrackingInfo | null>()
      uniqueTNs.forEach((tn, i) => tnToInfo.set(tn, results[i] ?? null))

      for (const { itemId, tn } of orderedPairs) {
        next[itemId] = tnToInfo.get(tn) ?? null
      }
      setTrackingInfoByItemId(next)
    }

    void fetchTrackingInfos()
  }, [orders, shipments, trackingEdits])

  const getShipmentForItem = (itemId: number) =>
    shipments.find((s) => s.shipment_items?.some((si) => si.item_id === itemId))

  type ItemGroup = { key: string; label: string; trackingNumber: string | null; shipment: Shipment | null; items: Item[] }
  const groupOrderItemsByShipment = (order: Order): ItemGroup[] => {
    const itemIdToShipment = new Map<number, { shipment: Shipment; trackingNumber: string }>()
    for (const s of shipments) {
      const tn = s.tracking_number?.trim() ?? ''
      for (const si of s.shipment_items ?? []) {
        itemIdToShipment.set(si.item_id, { shipment: s, trackingNumber: tn })
      }
    }
    const byKey = new Map<string | number, ItemGroup>()
    for (const item of order.items ?? []) {
      const info = itemIdToShipment.get(item.id)
      const key = info ? info.shipment.id : 'unshipped'
      if (!byKey.has(key)) {
        byKey.set(key, {
          key: String(key),
          label: info ? 'Shipment' : 'Unshipped',
          trackingNumber: info?.trackingNumber ?? null,
          shipment: info?.shipment ?? null,
          items: [],
        })
      }
      byKey.get(key)!.items.push(item)
    }
    const unshipped = byKey.get('unshipped')
    const shipped = Array.from(byKey.entries())
      .filter(([k]) => k !== 'unshipped')
      .sort(([a], [b]) => (a as number) - (b as number))
      .map(([, g]) => g)
    return [...(unshipped ? [unshipped] : []), ...shipped]
  }

  const removeItemFromShipment = async (shipment: Shipment, itemId: number) => {
    const remaining = (shipment.shipment_items ?? [])
      .filter((si) => si.item_id !== itemId)
      .map((si) => si.item_id)
    if (remaining.length === 0) await api.delete(`/shipments/${shipment.id}`)
    else await api.patch(`/shipments/${shipment.id}`, { item_ids: remaining })
  }

  const saveItemTracking = async (itemId: number, newValue: string) => {
    const trimmed = newValue.trim()
    setSavingTrackingId(itemId)
    try {
      const shipment = getShipmentForItem(itemId)
      if (trimmed) {
        if (shipment) {
          await removeItemFromShipment(shipment, itemId)
          await api.post('/shipments', { item_ids: [itemId], tracking_number: trimmed })
        } else {
          await api.post('/shipments', { item_ids: [itemId], tracking_number: trimmed })
        }
      } else if (shipment) {
        await removeItemFromShipment(shipment, itemId)
      }
      const [ordersData, shipmentsData] = await Promise.all([
        api.get<Order[]>(ordersPath),
        api.get<Shipment[]>('/shipments'),
      ])
      setOrders(ordersData)
      setShipments(shipmentsData)
      setTrackingEdits((prev) => {
        const next = { ...prev }; delete next[itemId]; return next
      })
    } catch (e) {
      console.error(e)
    } finally {
      setSavingTrackingId(null)
    }
  }

  const toggleItemSelect = (itemId: number, items: { id: number }[]) => {
    setSelectedItemIds((prev) => {
      const next = new Set(prev)
      if (next.has(itemId)) next.delete(itemId)
      else next.add(itemId)
      return next
    })
  }
  const toggleSelectAll = (items: { id: number }[]) => {
    const ids = items.map((i) => i.id)
    const allSelected = ids.every((id) => selectedItemIds.has(id))
    setSelectedItemIds(allSelected ? new Set() : new Set(ids))
  }
  const getSelectedIdsForOrder = (order: Order) =>
    (order.items ?? []).filter((i) => selectedItemIds.has(i.id)).map((i) => i.id)

  const getBulkActionState = (orderId: number) =>
    bulkActionByOrder[orderId] ?? { action: '', tracking: '', shippedAt: new Date().toISOString().slice(0, 10) }
  const setBulkActionState = (orderId: number, patch: Partial<{ action: string; tracking: string; shippedAt: string }>) => {
    setBulkActionByOrder((prev) => {
      const current = prev[orderId] ?? { action: '', tracking: '', shippedAt: new Date().toISOString().slice(0, 10) }
      return { ...prev, [orderId]: { ...current, ...patch } }
    })
  }

  const applyBulkInputTracking = async (order: Order) => {
    const ids = getSelectedIdsForOrder(order)
    if (ids.length === 0) return
    const state = getBulkActionState(order.id)
    const tracking = state.tracking.trim()
    if (!tracking) return
    setBulkActionShippingOrderId(order.id)
    try {
      await api.post('/shipments', {
        item_ids: ids,
        tracking_number: tracking,
        shipped_at: state.shippedAt ? `${state.shippedAt}T00:00:00.000Z` : undefined,
      })
      const [ordersData, shipmentsData] = await Promise.all([
        api.get<Order[]>(ordersPath),
        api.get<Shipment[]>('/shipments'),
      ])
      setOrders(ordersData)
      setShipments(shipmentsData)
      setSelectedItemIds((prev) => {
        const next = new Set(prev); ids.forEach((id) => next.delete(id)); return next
      })
      setBulkActionByOrder((prev) => {
        const next = { ...prev }; delete next[order.id]; return next
      })
    } catch (e) {
      console.error(e)
    } finally {
      setBulkActionShippingOrderId(null)
    }
  }

  const mergeUpdatedItemsIntoOrders = (updatedItems: Item[]) => {
    if (updatedItems.length === 0) return
    setOrders((prev) =>
      prev.map((o) => {
        const itemIds = new Set(updatedItems.filter((i) => i.order_id === o.id).map((i) => i.id))
        if (itemIds.size === 0) return o
        return {
          ...o,
          items: (o.items ?? []).map((i) => (itemIds.has(i.id) ? updatedItems.find((u) => u.id === i.id)! : i)),
        }
      })
    )
  }

  const applySubmitShipment = async (group: { key: string; items: Item[] }, submissionId: string) => {
    const now = new Date().toISOString().slice(0, 19)
    const toUpdate = group.items.filter((item) => getNextStatus(getEffectiveItemStatus(item)) === 'submitted')
    if (toUpdate.length === 0) return
    setAdvancingGroupKey(group.key)
    try {
      const res = await api.post<{ items: Item[] }>('/items/bulk-update', {
        updates: toUpdate.map((item) => ({
          item_id: item.id,
          status: 'submitted',
          submitted_at: now,
          submission_id: submissionId.trim() || null,
        })),
      })
      mergeUpdatedItemsIntoOrders(res.items)
      setSubmitShipmentModal(null)
    } catch (e) {
      console.error(e)
    } finally {
      setAdvancingGroupKey(null)
    }
  }

  const applyScanShipment = async (group: { key: string; items: Item[] }, receiptIds: Record<number, string>) => {
    const now = new Date().toISOString().slice(0, 19)
    const toUpdate = group.items.filter((item) => getNextStatus(getEffectiveItemStatus(item)) === 'scanned')
    if (toUpdate.length === 0) return
    setAdvancingGroupKey(group.key)
    try {
      const res = await api.post<{ items: Item[] }>('/items/bulk-update', {
        updates: toUpdate.map((item) => ({
          item_id: item.id,
          status: 'scanned',
          scanned_at: now,
          receipt_id: (receiptIds[item.id] ?? '').trim() || null,
        })),
      })
      mergeUpdatedItemsIntoOrders(res.items)
      setScanReceiptModal(null)
    } catch (e) {
      console.error(e)
    } finally {
      setAdvancingGroupKey(null)
    }
  }

  const applyBulkReceived = async (itemIds: number[], receiptIds: Record<number, string>) => {
    const now = new Date().toISOString().slice(0, 19)
    try {
      const res = await api.post<{ items: Item[] }>('/items/bulk-update', {
        updates: itemIds.map((itemId) => ({
          item_id: itemId,
          delivered_at: now,
          receipt_id: (receiptIds[itemId] ?? '').trim() || null,
        })),
      })
      mergeUpdatedItemsIntoOrders(res.items)
      setSelectedItemIds((prev) => {
        const next = new Set(prev); itemIds.forEach((id) => next.delete(id)); return next
      })
      setBulkStatusModal(null)
    } catch (e) {
      console.error(e)
    }
  }

  const applyBulkScanned = async (itemIds: number[], receiptIds: Record<number, string>) => {
    const now = new Date().toISOString().slice(0, 19)
    try {
      const res = await api.post<{ items: Item[] }>('/items/bulk-update', {
        updates: itemIds.map((itemId) => ({
          item_id: itemId,
          status: 'scanned',
          scanned_at: now,
          receipt_id: (receiptIds[itemId] ?? '').trim() || null,
        })),
      })
      mergeUpdatedItemsIntoOrders(res.items)
      setSelectedItemIds((prev) => {
        const next = new Set(prev); itemIds.forEach((id) => next.delete(id)); return next
      })
      setBulkScanModal(null)
    } catch (e) {
      console.error(e)
    }
  }

  const updateOrder = async (orderId: number, data: Partial<Order>) => {
    setSavingOrderId(orderId)
    try {
      const updated = await api.patch<Order>(`/orders/${orderId}`, data)
      setOrders((prev) => prev.map((o) => (o.id === orderId ? updated : o)))
    } catch (e) {
      console.error(e)
    } finally {
      setSavingOrderId(null)
    }
  }

  const updateOrderPayments = async (orderId: number, payment_methods: { payment_method_id: number; amount: string | null }[]) => {
    setSavingOrderId(orderId)
    try {
      const payload = payment_methods.map((pm) => ({ payment_method_id: pm.payment_method_id, amount: pm.amount?.trim() || null }))
      const updated = await api.patch<Order>(`/orders/${orderId}`, { payment_methods: payload })
      setOrders((prev) => prev.map((o) => (o.id === orderId ? updated : o)))
    } catch (e) {
      console.error(e)
    } finally {
      setSavingOrderId(null)
    }
  }

  useEffect(() => {
    const orderIds = Object.keys(paymentEdits).map(Number)
    orderIds.forEach((orderId) => {
      if (savingOrderId === orderId) return
      const order = orders.find((o) => o.id === orderId)
      if (!order || !paymentEdits[orderId]?.length) return
      const rows = paymentEdits[orderId]!
      const validRows = rows.filter((r) => r.payment_method_id !== 0)
      const totalPaid = orderTotals(order.items ?? [])
      const paymentSum = validRows.reduce((s, r) => s + parseDecimal(r.amount), 0)
      const usedIds = new Set(validRows.map((r) => r.payment_method_id))
      const canSave =
        validRows.length > 0 &&
        Math.abs(paymentSum - totalPaid) < 0.01 &&
        usedIds.size === validRows.length
      if (!canSave) return
      const serverRows = (order.order_payments ?? []).map((op) => ({
        payment_method_id: op.payment_method_id,
        amount: op.amount != null ? String(op.amount) : '',
      }))
      const hasChanges =
        validRows.length !== serverRows.length ||
        validRows.some((r, i) => serverRows[i]?.payment_method_id !== r.payment_method_id || (serverRows[i]?.amount ?? '') !== (r.amount ?? ''))
      if (!hasChanges) return
      const t = paymentSaveTimeouts.current[orderId]
      if (t) clearTimeout(t)
      paymentSaveTimeouts.current[orderId] = setTimeout(() => {
        delete paymentSaveTimeouts.current[orderId]
        updateOrderPayments(orderId, validRows.map((r) => ({ payment_method_id: r.payment_method_id, amount: r.amount }))).then(() => {
          setPaymentEdits((prev) => { const next = { ...prev }; delete next[orderId]; return next })
        })
      }, 600)
    })
    return () => {
      orderIds.forEach((id) => {
        const t = paymentSaveTimeouts.current[id]
        if (t) clearTimeout(t)
        delete paymentSaveTimeouts.current[id]
      })
    }
  }, [paymentEdits, orders, savingOrderId])

  const updateItem = async (itemId: number, data: Partial<Item>) => {
    setSavingItemId(itemId)
    try {
      const updated = await api.patch<Item>(`/items/${itemId}`, data)
      setOrders((prev) =>
        prev.map((o) =>
          o.items?.some((i) => i.id === itemId)
            ? { ...o, items: o.items.map((i) => (i.id === itemId ? updated : i)) }
            : o
        )
      )
      setItemEdits((prev) => { const next = { ...prev }; delete next[itemId]; return next })
    } catch (e) {
      console.error(e)
    } finally {
      setSavingItemId(null)
    }
  }

  const advanceShipmentToNextStatus = async (group: { key: string; items: Item[] }) => {
    const toUpdate = group.items.filter((item) => getNextStatus(getEffectiveItemStatus(item)) != null)
    if (toUpdate.length === 0) return
    setAdvancingGroupKey(group.key)
    const now = new Date().toISOString()
    try {
      const next = getNextStatus(getEffectiveItemStatus(toUpdate[0]))
      if (next === 'payment_sent' || next === 'payment_received') {
        const paymentId = toUpdate[0].payment_id
        if (!paymentId) return
        await api.patch(`/payments/${paymentId}`, next === 'payment_sent' ? { payment_sent_at: now } : { payment_received_at: now })
        const ordersData = await api.get<Order[]>(ordersPath)
        setOrders(ordersData)
      } else {
        for (const item of toUpdate) {
          const n = getNextStatus(getEffectiveItemStatus(item))
          if (n && n !== 'payment_sent' && n !== 'payment_received') {
            const dateField = STATUS_TO_DATE_FIELD[n]
            const payload: Partial<Item> = { status: n }
            if (dateField) (payload as any)[dateField] = now.slice(0, 19)
            await updateItem(item.id, payload)
          }
        }
      }
    } finally {
      setAdvancingGroupKey(null)
    }
  }

  const addItem = async (order: Order) => {
    try {
      const newItem = await api.post<Item>('/items', { order_id: order.id, status: 'purchased' })
      setOrders((prev) =>
        prev.map((o) => (o.id === order.id ? { ...o, items: [...(o.items ?? []), newItem] } : o))
      )
    } catch (e) {
      console.error(e)
    }
  }

  const copyItem = async (order: Order, item: Item) => {
    setCopyingItemId(item.id)
    try {
      const newItem = await api.post<Item>('/items', {
        order_id: order.id,
        quantity: item.quantity ?? 1,
        description: item.description ?? null,
        price_paid: item.price_paid ?? null,
        price_sold: item.price_sold ?? null,
        status: item.status,
      })
      const items = order.items ?? []
      const idx = items.findIndex((i) => i.id === item.id)
      const nextItems = idx < 0 ? [...items, newItem] : [...items.slice(0, idx + 1), newItem, ...items.slice(idx + 1)]
      setOrders((prev) =>
        prev.map((o) => (o.id === order.id ? { ...o, items: nextItems } : o))
      )
    } catch (e) {
      console.error(e)
    } finally {
      setCopyingItemId(null)
    }
  }

  const deleteItem = async (itemId: number) => {
    setDeletingItemId(itemId)
    try {
      await api.delete(`/items/${itemId}`)
      const [ordersData, shipmentsData] = await Promise.all([
        api.get<Order[]>(ordersPath),
        api.get<Shipment[]>('/shipments'),
      ])
      setOrders(ordersData)
      setShipments(shipmentsData)
      setSelectedItemIds((prev) => { const next = new Set(prev); next.delete(itemId); return next })
      setItemEdits((prev) => { const next = { ...prev }; delete next[itemId]; return next })
      setTrackingEdits((prev) => { const next = { ...prev }; delete next[itemId]; return next })
    } catch (e) {
      console.error(e)
    } finally {
      setDeletingItemId(null)
    }
  }

  const deleteItems = async (itemIds: number[]) => {
    if (itemIds.length === 0) return
    try {
      await api.post('/items/bulk-delete', { item_ids: itemIds })
      const [ordersData, shipmentsData] = await Promise.all([
        api.get<Order[]>(ordersPath),
        api.get<Shipment[]>('/shipments'),
      ])
      setOrders(ordersData)
      setShipments(shipmentsData)
      const idSet = new Set(itemIds)
      setSelectedItemIds((prev) => {
        const next = new Set(prev)
        idSet.forEach((id) => next.delete(id))
        return next
      })
      setItemEdits((prev) => {
        const next = { ...prev }
        idSet.forEach((id) => delete next[id])
        return next
      })
      setTrackingEdits((prev) => {
        const next = { ...prev }
        idSet.forEach((id) => delete next[id])
        return next
      })
    } catch (e) {
      console.error(e)
    }
  }

  const splitItem = async (itemId: number, keepQuantity: number, currentQuantity: number, orderId: number) => {
    try {
      await api.patch<Item>(`/items/${itemId}`, { quantity: currentQuantity })
      const { kept, split_off } = await api.post<{ kept: Item; split_off: Item }>(
        `/items/${itemId}/split`,
        { keep_quantity: keepQuantity }
      )
      setOrders((prev) =>
        prev.map((o) => {
          if (o.id !== orderId) return o
          const items = (o.items ?? [])
            .map((i) => (i.id === itemId ? kept : i))
            .concat(split_off)
            .sort((a, b) => a.id - b.id)
          return { ...o, items }
        })
      )
      setSplitModalItem(null)
      setItemEdits((prev) => {
        const next = { ...prev }; delete next[itemId]; return next
      })
    } catch (e) {
      console.error(e)
    }
  }

  const deleteOrder = async (orderId: number) => {
    setDeletingOrderId(orderId)
    try {
      await api.delete(`/orders/${orderId}`)
      setOrders((prev) => prev.filter((o) => o.id !== orderId))
      setOrderEdits((prev) => { const next = { ...prev }; delete next[orderId]; return next })
      setPaymentEdits((prev) => { const next = { ...prev }; delete next[orderId]; return next })
    } catch (e) {
      console.error(e)
    } finally {
      setDeletingOrderId(null)
    }
  }

  const parseDecimal = (s: string | null | undefined): number => {
    if (s == null || String(s).trim() === '') return 0
    const n = parseFloat(String(s))
    return Number.isNaN(n) ? 0 : n
  }
  const orderTotals = (
    items: { price_paid?: string | null; quantity?: number; shipping?: string | null; sales_tax?: string | null }[]
  ) => {
    let totalPaid = 0
    for (const item of items) {
      const qty = Math.max(0, item.quantity ?? 1)
      totalPaid += (parseDecimal(item.price_paid) + parseDecimal(item.shipping) + parseDecimal(item.sales_tax)) * qty
    }
    return totalPaid
  }

  const nowIso = () => new Date().toISOString().slice(0, 19)

  const copyLineItems = (o: Order) => {
    const items = o.items ?? []
    if (items.length === 0) return
    const ids = getSelectedIdsForOrder(o)
    const toCopy = ids.length > 0 ? items.filter((i) => ids.includes(i.id)) : items
    if (toCopy.length === 0) return
    const header = ['Qty', 'Description', 'Tracking', 'Cost', 'Payout', 'Total cost', 'Total payout', 'Status']
    const rows = toCopy.map((item) => {
      const edits = itemEdits[item.id]
      const qty = edits?.quantity ?? item.quantity ?? 1
      const desc = edits?.description ?? item.description ?? ''
      const tracking = trackingEdits[item.id] ?? getTracking(item.id) ?? ''
      const cost = edits?.price_paid ?? item.price_paid ?? ''
      const payout = edits?.price_sold ?? item.price_sold ?? ''
      const totalCost = (parseDecimal(cost) * qty).toFixed(2)
      const totalPayout = (parseDecimal(payout) * qty).toFixed(2)
      const status = STATUS_LABELS[item.status] ?? item.status
      return [qty, desc, tracking, cost, payout, totalCost, totalPayout, status]
    })
    const tsv = [header.join('\t'), ...rows.map((r) => r.join('\t'))].join('\n')
    copyToClipboard(tsv)
  }

  const copyOrder = async (o: Order, e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (!o.items?.length) return
    setCopyingId(o.id)
    try {
      const created = await api.post<Order>('/orders', {
        store_id: o.store_id,
        store_account_id: o.store_account_id ?? null,
        store_order_number: null,
        purchase_date: nowIso(),
        notes: o.notes ?? undefined,
        buying_group_id: o.buying_group_id ?? null,
        payment_methods: (o.order_payments ?? []).map((op) => ({
          payment_method_id: op.payment_method_id,
          amount: op.amount ?? undefined,
        })),
        items: o.items.map((item) => ({
          price_paid: item.price_paid ?? undefined,
          price_sold: item.price_sold ?? undefined,
          status: 'purchased',
          quantity: item.quantity ?? 1,
          description: item.description ?? undefined,
          shipping: item.shipping ?? undefined,
          sales_tax: item.sales_tax ?? undefined,
        })),
      })
      setOrders((prev) => [created, ...prev])
    } catch (err) {
      console.error(err)
    } finally {
      setCopyingId(null)
    }
  }

  return (
    <div className="space-y-6">
      <div
        className={`flex items-center justify-between gap-4 flex-wrap transition-opacity duration-200 ${
          loading ? 'opacity-50 pointer-events-none' : ''
        }`}
      >
        <div className="flex items-center gap-2 shrink-0">
          <h1 className="text-xl font-semibold text-ink dark:text-gray-100">Orders</h1>
          <span className="inline-flex w-4 h-4 items-center justify-center" aria-hidden={!loading}>
            {loading && (
              <span
                className="inline-block w-4 h-4 border-2 border-brand-200 border-t-brand-600 dark:border-gray-600 dark:border-t-brand-400 rounded-full animate-spin"
                aria-label="Loading"
              />
            )}
          </span>
        </div>
        <div className="flex-1 flex justify-center items-center min-w-0">
          <div className="flex items-center gap-3 flex-wrap justify-center">
        <div ref={filterStatusRef} className="relative w-[110px] shrink-0">
          <button
            type="button"
            onClick={() => {
              setFilterBuyingGroupOpen(false)
              setFilterStoreOpen(false)
              setFilterDateOpen(false)
              setFilterStatusOpen((v) => !v)
            }}
            className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border text-sm font-medium transition w-full justify-between ${
              filterStatuses.size > 0
                ? 'border-brand-500 bg-brand-50 text-brand-700 dark:bg-brand-900/30 dark:border-brand-600 dark:text-brand-400'
                : 'border-brand-200 dark:border-gray-600 text-ink dark:text-gray-200 hover:bg-brand-50/50 dark:hover:bg-gray-700'
            }`}
            aria-label="Filter by status"
            aria-expanded={filterStatusOpen}
          >
            <span className="truncate">Status{filterStatuses.size > 0 ? ` (${filterStatuses.size})` : ''}</span>
            <span className="inline-block transition-transform shrink-0" style={{ transform: filterStatusOpen ? 'rotate(180deg)' : 'none' }}>▾</span>
          </button>
          {filterStatusOpen && (
            <div className="absolute left-0 top-full mt-1 z-50 py-2 min-w-[180px] rounded-lg border border-brand-200 dark:border-gray-600 bg-white dark:bg-gray-800 shadow-lg max-h-64 overflow-auto">
              {STATUS_FILTER_OPTIONS.map(([value, label]) => (
                <label key={value} className="flex items-center gap-2 px-3 py-1.5 hover:bg-brand-50 dark:hover:bg-gray-700 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={filterStatuses.has(value)}
                    onChange={() => toggleFilterStatus(value)}
                    className="rounded border-brand-300 text-brand-600 focus:ring-brand-500"
                  />
                  <span className="text-sm text-ink dark:text-gray-200">{label}</span>
                </label>
              ))}
            </div>
          )}
        </div>
        <div ref={filterBuyingGroupRef} className="relative w-[145px] shrink-0">
          <button
            type="button"
            onClick={() => {
              setFilterStatusOpen(false)
              setFilterStoreOpen(false)
              setFilterDateOpen(false)
              setFilterBuyingGroupOpen((v) => !v)
            }}
            className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border text-sm font-medium transition w-full justify-between ${
              filterBuyingGroups.size > 0
                ? 'border-brand-500 bg-brand-50 text-brand-700 dark:bg-brand-900/30 dark:border-brand-600 dark:text-brand-400'
                : 'border-brand-200 dark:border-gray-600 text-ink dark:text-gray-200 hover:bg-brand-50/50 dark:hover:bg-gray-700'
            }`}
            aria-label="Filter by buying group"
            aria-expanded={filterBuyingGroupOpen}
          >
            <span className="truncate">Buying group{filterBuyingGroups.size > 0 ? ` (${filterBuyingGroups.size})` : ''}</span>
            <span className="inline-block transition-transform shrink-0" style={{ transform: filterBuyingGroupOpen ? 'rotate(180deg)' : 'none' }}>▾</span>
          </button>
          {filterBuyingGroupOpen && (
            <div className="absolute left-0 top-full mt-1 z-50 py-2 min-w-[180px] rounded-lg border border-brand-200 dark:border-gray-600 bg-white dark:bg-gray-800 shadow-lg max-h-64 overflow-auto">
              {groups.length === 0 ? (
                <div className="px-3 py-2 text-sm text-ink-muted">No buying groups</div>
              ) : (
                groups.map((g) => (
                  <label key={g.id} className="flex items-center gap-2 px-3 py-1.5 hover:bg-brand-50 dark:hover:bg-gray-700 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={filterBuyingGroups.has(g.id)}
                      onChange={() => toggleFilterBuyingGroup(g.id)}
                      className="rounded border-brand-300 text-brand-600 focus:ring-brand-500"
                    />
                    <span className="text-sm text-ink dark:text-gray-200">{g.name}</span>
                  </label>
                ))
              )}
            </div>
          )}
        </div>
        <div ref={filterStoreRef} className="relative w-[120px] shrink-0">
          <button
            type="button"
            onClick={() => {
              setFilterStatusOpen(false)
              setFilterBuyingGroupOpen(false)
              setFilterDateOpen(false)
              setFilterStoreOpen((v) => !v)
            }}
            className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border text-sm font-medium transition w-full justify-between ${
              filterStores.size > 0 || filterStoreAccounts.size > 0
                ? 'border-brand-500 bg-brand-50 text-brand-700 dark:bg-brand-900/30 dark:border-brand-600 dark:text-brand-400'
                : 'border-brand-200 dark:border-gray-600 text-ink dark:text-gray-200 hover:bg-brand-50/50 dark:hover:bg-gray-700'
            }`}
            aria-label="Filter by store"
            aria-expanded={filterStoreOpen}
          >
            <span className="truncate">Store{filterStores.size > 0 || filterStoreAccounts.size > 0 ? ` (${filterStores.size + filterStoreAccounts.size})` : ''}</span>
            <span className="inline-block transition-transform shrink-0" style={{ transform: filterStoreOpen ? 'rotate(180deg)' : 'none' }}>▾</span>
          </button>
          {filterStoreOpen && (
            <div className="absolute left-0 top-full mt-1 z-50 py-2 min-w-[180px] rounded-lg border border-brand-200 dark:border-gray-600 bg-white dark:bg-gray-800 shadow-lg max-h-64 overflow-auto">
              {stores.length === 0 ? (
                <div className="px-3 py-2 text-sm text-ink-muted">No stores</div>
              ) : (
                stores.map((s) => {
                  const storeAccounts = accountsByStore[s.id] ?? []
                  const storeChecked = filterStores.has(s.id)
                  const someAccountsSelected = storeAccounts.some((a) => filterStoreAccounts.has(a.id))
                  const storeIndeterminate = !storeChecked && someAccountsSelected
                  return (
                    <div key={s.id}>
                      <label className="flex items-center gap-2 px-3 py-1.5 hover:bg-brand-50 dark:hover:bg-gray-700 cursor-pointer">
                        <input
                          ref={(el) => {
                            if (el) el.indeterminate = storeIndeterminate
                          }}
                          type="checkbox"
                          checked={storeChecked}
                          onChange={() => toggleFilterStore(s.id)}
                          className="rounded border-brand-300 text-brand-600 focus:ring-brand-500"
                        />
                        <span className="text-sm text-ink dark:text-gray-200">{s.name}</span>
                      </label>
                      {storeAccounts.map((a) => (
                        <label
                          key={a.id}
                          className="flex items-center gap-2 pl-8 pr-3 py-1.5 hover:bg-brand-50 dark:hover:bg-gray-700 cursor-pointer"
                        >
                          <input
                            type="checkbox"
                            checked={filterStoreAccounts.has(a.id) || storeChecked}
                            onChange={() => toggleFilterStoreAccount(a.id, s.id)}
                            className="rounded border-brand-300 text-brand-600 focus:ring-brand-500"
                          />
                          <span className="text-sm text-ink dark:text-gray-200">{a.name}</span>
                        </label>
                      ))}
                    </div>
                  )
                })
              )}
            </div>
          )}
        </div>
        <div ref={filterDateRef} className="relative w-[240px] shrink-0">
          <button
            type="button"
            onClick={() => {
              setFilterStatusOpen(false)
              setFilterBuyingGroupOpen(false)
              setFilterStoreOpen(false)
              setFilterDateOpen((v) => !v)
            }}
            className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border text-sm font-medium transition w-full justify-between ${
              filterDateFrom || filterDateTo
                ? 'border-brand-500 bg-brand-50 text-brand-700 dark:bg-brand-900/30 dark:border-brand-600 dark:text-brand-400'
                : 'border-brand-200 dark:border-gray-600 text-ink dark:text-gray-200 hover:bg-brand-50/50 dark:hover:bg-gray-700'
            }`}
            aria-label="Filter by date range"
            aria-expanded={filterDateOpen}
          >
            <span className="truncate">{dateRangeLabel}</span>
            <span className="inline-block transition-transform shrink-0" style={{ transform: filterDateOpen ? 'rotate(180deg)' : 'none' }}>▾</span>
          </button>
          {filterDateOpen && (
            <div className="absolute left-0 top-full mt-1 z-50 py-2 min-w-[260px] rounded-lg border border-brand-200 dark:border-gray-600 bg-white dark:bg-gray-800 shadow-lg">
              <div className="px-3 pb-2 mb-2 border-b border-brand-100 dark:border-gray-600">
                <div className="text-xs font-medium text-ink-muted dark:text-gray-400 mb-2">Presets</div>
                <div className="flex flex-wrap gap-1">
                  {(['today', 'week', 'past7', 'past30', 'month', 'year', 'lastYear'] as const).map((p) => (
                    <button
                      key={p}
                      type="button"
                      onClick={() => applyDatePreset(p)}
                      className="px-2 py-1 rounded text-xs font-medium text-ink dark:text-gray-200 hover:bg-brand-100 dark:hover:bg-gray-700 transition"
                    >
                      {p === 'today' && 'Today'}
                      {p === 'week' && 'This Week'}
                      {p === 'past7' && 'Past 7 Days'}
                      {p === 'past30' && 'Past 30 Days'}
                      {p === 'month' && 'This Month'}
                      {p === 'year' && 'This Year'}
                      {p === 'lastYear' && 'Last Year'}
                    </button>
                  ))}
                </div>
              </div>
              <div className="px-3 space-y-2">
                <div className="text-xs font-medium text-ink-muted dark:text-gray-400 mb-1">Custom range</div>
                <div className="flex items-center gap-2">
                  <input
                    type="date"
                    value={filterDateFrom}
                    onChange={(e) => setFilterDateFrom(e.target.value)}
                    className="flex-1 px-2 py-1.5 rounded border border-brand-200 dark:border-gray-600 text-sm text-ink dark:text-gray-200 bg-white dark:bg-gray-800 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent min-w-0"
                    title="From date"
                  />
                  <span className="text-ink-muted text-xs">–</span>
                  <input
                    type="date"
                    value={filterDateTo}
                    onChange={(e) => setFilterDateTo(e.target.value)}
                    className="flex-1 px-2 py-1.5 rounded border border-brand-200 dark:border-gray-600 text-sm text-ink dark:text-gray-200 bg-white dark:bg-gray-800 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent min-w-0"
                    title="To date"
                  />
                </div>
              </div>
            </div>
          )}
        </div>
        <input
          ref={searchInputRef}
          type="search"
          placeholder="Search order #, item, store, amount…"
          value={searchText}
          onChange={(e) => setSearchText(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
          className="h-8 w-[220px] shrink-0 rounded border border-brand-200 dark:border-gray-600 bg-white dark:bg-gray-800 text-ink dark:text-gray-200 text-sm px-2 placeholder:text-ink-muted focus:outline-none focus:ring-1 focus:ring-brand-500"
          aria-label="Search orders"
        />
        <button
          type="button"
          onClick={resetFilters}
          disabled={!hasActiveFilters}
          className={`px-3 py-1.5 rounded-lg border text-sm transition shrink-0 ${
            hasActiveFilters
              ? 'border-brand-200 dark:border-gray-600 text-ink dark:text-gray-200 hover:bg-brand-50 dark:hover:bg-gray-700'
              : 'border-brand-200 dark:border-gray-600 text-ink-muted dark:text-gray-500 opacity-60 cursor-not-allowed'
          }`}
        >
          Reset Filters
        </button>
          </div>
        </div>
        <button
          onClick={handleCreateNewOrder}
          disabled={creatingOrder}
          className="px-4 py-2 bg-brand-600 text-white rounded-lg font-medium hover:bg-brand-700 transition shrink-0 disabled:opacity-60"
        >
          {creatingOrder ? 'Creating…' : 'New order'}
        </button>
      </div>

      <div
        className={`space-y-4 transition-opacity duration-200 ${
          loading ? 'opacity-50 pointer-events-none' : ''
        }`}
      >
        {orders.length === 0 ? (
          <div className="bg-white dark:bg-gray-800 rounded-xl border border-brand-200/80 dark:border-gray-700 py-12 text-center text-ink-muted">
            No orders yet. Create one to get started.
          </div>
        ) : (
          orders.map((o) => {
            const paymentRows =
              paymentEdits[o.id] ??
              (o.order_payments ?? []).map((op) => ({
                payment_method_id: op.payment_method_id,
                amount: op.amount != null ? String(op.amount) : '',
              }))
            const totalPaid = orderTotals(o.items ?? [])
            const itemCount = o.items?.length ?? 0
            const lineItemsHeight = itemCount * 32 + 40 // approx row height + header

            return (
              <div
                key={o.id}
                className="flex gap-0 border-2 border-brand-400 dark:border-gray-400 rounded-xl overflow-hidden bg-brand-50/50 dark:bg-gray-600/80"
                style={{ minHeight: Math.max(120, lineItemsHeight) }}
              >
                {/* Left: vertical order box */}
                <div
                  className="w-[400px] shrink-0 flex flex-col gap-2 p-3 border-r-2 border-brand-400 dark:border-gray-400 bg-white/80 dark:bg-gray-700/80 overflow-hidden"
                  style={{ minHeight: lineItemsHeight }}
                >
                  <div className="flex items-center gap-2 min-w-0">
                    <label className="text-xs text-ink-muted shrink-0 w-14">Order #</label>
                    <input
                      type="text"
                      value={orderEdits[o.id]?.store_order_number ?? (o.store_order_number ?? '')}
                      onChange={(e) =>
                        setOrderEdits((prev) => ({ ...prev, [o.id]: { ...prev[o.id], store_order_number: e.target.value } }))
                      }
                      onBlur={(e) => {
                        const v = e.target.value.trim()
                        if (v !== (o.store_order_number ?? '')) updateOrder(o.id, { store_order_number: v || null })
                        setOrderEdits((prev) => {
                          const next = { ...prev }
                          if (next[o.id]) {
                            delete next[o.id].store_order_number
                            if (Object.keys(next[o.id]).length === 0) delete next[o.id]
                          }
                          return next
                        })
                      }}
                      disabled={savingOrderId === o.id}
                      className="flex-1 min-w-0 h-6 rounded border border-brand-200 dark:border-gray-600 bg-transparent px-1.5 py-0 text-sm font-medium text-brand-700 dark:text-brand-400 focus:border-brand-300 focus:bg-white dark:focus:bg-gray-700 focus:outline-none focus:ring-1 focus:ring-brand-500 disabled:opacity-60"
                    />
                  </div>
                  <div className="flex items-center gap-2 min-w-0">
                    <label className="text-xs text-ink-muted shrink-0 w-14">Date</label>
                    <input
                      type="datetime-local"
                      value={
                        orderEdits[o.id]?.purchase_date ??
                        toLocalDatetimeLocal(o.purchase_date ?? null)
                      }
                      onChange={(e) =>
                        setOrderEdits((prev) => ({
                          ...prev,
                          [o.id]: { ...prev[o.id], purchase_date: e.target.value || '' },
                        }))
                      }
                      onBlur={(e) => {
                        const v = e.target.value.trim() || null
                        const current = toLocalDatetimeLocal(o.purchase_date ?? null)
                        if ((v ?? '') !== current)
                          updateOrder(o.id, { purchase_date: v ? new Date(v).toISOString() : null })
                        setOrderEdits((prev) => {
                          const next = { ...prev }
                          if (next[o.id]) {
                            delete next[o.id].purchase_date
                            if (Object.keys(next[o.id]).length === 0) delete next[o.id]
                          }
                          return next
                        })
                      }}
                      disabled={savingOrderId === o.id}
                      className="flex-1 min-w-0 h-6 rounded border border-brand-200 dark:border-gray-600 px-1.5 py-0 text-sm font-mono !bg-brand-50/50 dark:!bg-gray-600/80 disabled:opacity-60"
                    />
                  </div>
                  <div className="flex items-center gap-2 min-w-0">
                    <label className="text-xs text-ink-muted shrink-0 w-14">Store</label>
                    <div className="min-w-0 flex-1">
                    <SearchableCombobox<{ id: number; name: string; type: 'store' | 'account'; storeId?: number; accountId?: number }>
                        onControlRef={(api) => {
                          storeDropdownRefs.current[o.id] = api
                        }}
                        inputClassName="h-6 py-0 px-2 text-sm rounded border border-brand-200 dark:border-gray-600 w-full min-w-0 text-ink focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent disabled:opacity-60 dark:bg-gray-700"
                        options={(() => {
                          const result: { id: number; name: string; type: 'store' | 'account'; storeId?: number; accountId?: number }[] = []
                          for (const store of stores) {
                            result.push({
                              id: store.id,
                              name: store.name,
                              type: 'store',
                              storeId: store.id,
                            })
                            const accounts = accountsByStore[store.id] ?? []
                            for (const account of accounts) {
                              result.push({
                                id: account.id,
                                name: `${store.name} (${account.name})`,
                                type: 'account',
                                storeId: store.id,
                                accountId: account.id,
                              })
                            }
                          }
                          return result
                        })()}
                        value={
                          o.store_id
                            ? o.store_account_id
                              ? {
                                  id: o.store_account_id,
                                  name: `${stores.find((s) => s.id === o.store_id)?.name ?? ''} (${(accountsByStore[o.store_id] ?? []).find((a) => a.id === o.store_account_id)?.name ?? ''})`,
                                  type: 'account',
                                  storeId: o.store_id,
                                  accountId: o.store_account_id,
                                }
                              : {
                                  id: o.store_id,
                                  name: stores.find((s) => s.id === o.store_id)?.name ?? '',
                                  type: 'store',
                                  storeId: o.store_id,
                                }
                            : null
                        }
                        onChange={(item) => {
                          if (!item) return
                          updateOrder(o.id, {
                            store_id: item.storeId,
                            store_account_id: item.accountId ?? null,
                          })
                          if (item.storeId && !accountsByStore[item.storeId]) {
                            loadAccountsForStore(item.storeId)
                          }
                        }}
                        onCreate={async (name) => {
                          const s = await api.post<Store>('/stores', { name })
                          setStores((prev) => [...prev, s].sort((a, b) => a.name.localeCompare(b.name)))
                          updateOrder(o.id, { store_id: s.id, store_account_id: null })
                          loadAccountsForStore(s.id)
                          return {
                            id: s.id,
                            name: s.name,
                            type: 'store',
                            storeId: s.id,
                          }
                        }}
                        renderOption={(opt) => {
                          if (opt.type === 'account') {
                            return <span className="pl-4">{opt.name}</span>
                          }
                          return (
                            <span className="flex items-center w-full">
                              <span className="flex-1">{opt.name}</span>
                              <button
                                type="button"
                                onMouseDown={(e) => {
                                  e.stopPropagation()
                                  e.preventDefault()
                                  setAddAccountModal({ orderId: o.id, storeName: opt.name, storeId: opt.storeId! })
                                  setAddAccountName('')
                                }}
                                className="p-0.5 rounded text-ink-muted hover:text-brand-600 hover:bg-brand-100 dark:hover:bg-gray-600"
                                aria-label={`Add account to ${opt.name}`}
                                title={`Add account to ${opt.name}`}
                              >
                                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                                </svg>
                              </button>
                            </span>
                          )
                        }}
                        placeholder="Store or account…"
                      />
                    </div>
                  </div>
                  <div className="flex items-center gap-2 min-w-0">
                    <label className="text-xs text-ink-muted shrink-0 w-14">Group</label>
                    <div className="min-w-0 flex-1">
                    <SearchableCombobox<BuyingGroup>
                      inputClassName="h-6 py-0 px-2 text-sm rounded border border-brand-200 dark:border-gray-600 w-full min-w-0 text-ink focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent disabled:opacity-60 dark:bg-gray-700"
                      options={groups}
                      value={groups.find((g) => g.id === o.buying_group_id) ?? null}
                      onChange={(g) => updateOrder(o.id, { buying_group_id: g?.id ?? null })}
                      onCreate={async (name) => {
                        const g = await api.post<BuyingGroup>('/buying-groups', { name })
                        setGroups((prev) => [...prev, g].sort((a, b) => a.name.localeCompare(b.name)))
                        updateOrder(o.id, { buying_group_id: g.id })
                        return g
                      }}
                      placeholder="Buying group…"
                      allowEmpty
                      disabled={savingOrderId === o.id}
                    />
                    </div>
                  </div>
                  <div className="space-y-1">
                    {(() => {
                      const displayPaymentRows =
                        paymentRows.length > 0 ? paymentRows : [{ payment_method_id: 0, amount: '' }]
                      const paymentSum = displayPaymentRows.reduce((s, r) => s + (parseDecimal(r.amount) || 0), 0)
                      const amountRemaining = Math.max(0, totalPaid - paymentSum)
                      const orderTotalReached = paymentSum >= totalPaid - 1e-9
                      return displayPaymentRows.map((row, idx) => (
                        <div key={idx} className="flex items-center gap-1 flex-nowrap">
                          <span className={idx === 0 ? 'text-xs font-medium text-ink-muted shrink-0 w-14' : 'w-14 shrink-0'}>
                            {idx === 0 ? 'Payment' : null}
                          </span>
                          <div className="min-w-0 flex-1 max-w-[11rem]">
                            <SearchableCombobox<{ id: number; name: string }>
                              inputClassName="h-6 py-0 px-2 text-sm rounded border border-brand-200 dark:border-gray-600 w-full min-w-0 text-ink focus:outline-none focus:ring-2 focus:ring-brand-500 dark:bg-gray-700"
                              options={paymentMethodOptions}
                              value={row.payment_method_id ? paymentMethodOptions.find((opt) => opt.id === row.payment_method_id) ?? null : null}
                              onChange={(opt) =>
                                setPaymentEdits((prev) => ({
                                  ...prev,
                                  [o.id]: displayPaymentRows.map((r, i) => (i === idx ? { ...r, payment_method_id: opt?.id ?? 0 } : r)),
                                }))
                              }
                              onCreate={async (label) => {
                                const pm = await api.post<PaymentMethod>('/payment-methods', { label })
                                setPaymentMethods((prev) => [...prev, pm].sort((a, b) => a.label.localeCompare(b.label)))
                                setPaymentEdits((prev) => ({
                                  ...prev,
                                  [o.id]: displayPaymentRows.map((r, i) => (i === idx ? { ...r, payment_method_id: pm.id } : r)),
                                }))
                                return { id: pm.id, name: pm.label }
                              }}
                              placeholder="Payment…"
                            />
                          </div>
                          <input
                            type="text"
                            placeholder="Amt"
                            value={row.amount}
                            onChange={(e) => {
                              const raw = e.target.value
                              const otherSum = displayPaymentRows.reduce((s, r, i) => (i === idx ? s : s + parseDecimal(r.amount)), 0)
                              const maxAmount = Math.max(0, totalPaid - otherSum)
                              const num = parseDecimal(raw)
                              const amount =
                                raw.trim() === '' || String(raw) === '.' || Number.isNaN(parseFloat(raw))
                                  ? raw
                                  : num > maxAmount
                                    ? maxAmount.toFixed(2)
                                    : raw
                              setPaymentEdits((prev) => ({
                                ...prev,
                                [o.id]: displayPaymentRows.map((r, i) => (i === idx ? { ...r, amount } : r)),
                              }))
                            }}
                            className="w-20 h-6 rounded border border-brand-200 dark:border-gray-600 px-1 py-0 text-sm font-mono !bg-brand-50/50 dark:!bg-gray-600/80 shrink-0"
                          />
                          {idx === displayPaymentRows.length - 1 ? (
                            <button
                              type="button"
                              onClick={() => {
                                setPaymentEdits((prev) => ({
                                  ...prev,
                                  [o.id]: [
                                    ...displayPaymentRows,
                                    {
                                      payment_method_id: 0,
                                      amount: amountRemaining > 0 ? amountRemaining.toFixed(2) : '',
                                    },
                                  ],
                                }))
                              }}
                              disabled={savingOrderId === o.id || orderTotalReached}
                              className="p-0.5 rounded text-ink-muted hover:text-ink hover:bg-brand-100 dark:hover:bg-gray-600 shrink-0 disabled:opacity-50 disabled:cursor-not-allowed"
                              aria-label="Add payment method"
                              title={orderTotalReached ? 'Order total already reached' : 'Add payment method'}
                            >
                              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                              </svg>
                            </button>
                          ) : null}
                          <button
                            type="button"
                            onClick={() =>
                              setPaymentEdits((prev) => ({
                                ...prev,
                                [o.id]: displayPaymentRows.filter((_, i) => i !== idx),
                              }))
                            }
                            className="p-0.5 rounded text-ink-muted hover:text-ink hover:bg-brand-100 dark:hover:bg-gray-600 shrink-0"
                            aria-label="Remove payment"
                          >
                            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                            </svg>
                          </button>
                        </div>
                      ))
                    })()}
                  </div>
                </div>

                {/* Right: line items table */}
                <div className="flex-1 min-w-0 overflow-x-auto">
                  {itemCount === 0 ? (
                    <div className="p-4 text-sm text-ink-muted flex items-center gap-2">
                      No line items.
                      <button
                        type="button"
                        onClick={() => addItem(o)}
                        className="text-brand-600 dark:text-brand-400 hover:underline"
                      >
                        Add item
                      </button>
                    </div>
                  ) : (
                    <div className="line-items-section rounded-lg overflow-hidden">
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="bg-brand-100/50 dark:bg-gray-700/50 text-left border-b border-brand-200 dark:border-gray-600">
                            <th className="w-8 py-1 px-2">
                              <input
                                type="checkbox"
                                checked={o.items!.every((i) => selectedItemIds.has(i.id)) && o.items!.length > 0}
                                onChange={() => toggleSelectAll(o.items!)}
                                className="rounded border-brand-300 text-brand-600 focus:ring-brand-500"
                              />
                            </th>
                            <th className="py-1 px-2 font-medium text-ink-muted w-12">Qty</th>
                            <th className="py-1 px-2 font-medium text-ink-muted">Description</th>
                            <th className="py-1 px-2 font-medium text-ink-muted">Tracking</th>
                            <th className="py-1 px-2 font-medium text-ink-muted w-0 whitespace-nowrap">Cost</th>
                            <th className="py-1 px-2 font-medium text-ink-muted w-0 whitespace-nowrap">Payout</th>
                            <th className="py-1 px-2 font-medium text-ink-muted w-0 whitespace-nowrap">Subtotal</th>
                            <th className="py-1 px-2 font-medium text-ink-muted w-0 whitespace-nowrap">Shipping</th>
                            <th className="py-1 px-2 font-medium text-ink-muted w-0 whitespace-nowrap">Sales Tax</th>
                            <th className="py-1 px-2 font-medium text-ink-muted w-0 whitespace-nowrap">Total Cost</th>
                            <th className="py-1 px-2 font-medium text-ink-muted w-0 whitespace-nowrap">Total Payout</th>
                            <th className="py-1 px-2 font-medium text-ink-muted">Status</th>
                            <th className="w-8 py-1 px-1.5 text-right">
                              <div className="flex items-center justify-end gap-0.5">
                                <button
                                  type="button"
                                  onClick={() => addItem(o)}
                                  className="p-1.5 rounded text-ink-muted hover:text-brand-600 hover:bg-brand-100 dark:hover:bg-gray-600"
                                  title="Add line item"
                                  aria-label="Add line item"
                                >
                                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                                  </svg>
                                </button>
                                {o.items && o.items.length > 0 && (
                                  <button
                                    type="button"
                                    title="Copy order"
                                    onClick={(e) => copyOrder(o, e)}
                                    disabled={copyingId === o.id}
                                    className="p-1.5 rounded text-ink-muted hover:text-brand-600 hover:bg-brand-100 dark:hover:bg-gray-600 transition disabled:opacity-50"
                                    aria-label="Copy order"
                                  >
                                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                                    </svg>
                                  </button>
                                )}
                                <button
                                  type="button"
                                  title="Delete order"
                                  onClick={() => setConfirmDeleteOrderId(o.id)}
                                  disabled={deletingOrderId === o.id}
                                  className="p-1.5 rounded text-red-600 hover:text-red-700 hover:bg-red-100 dark:text-red-400 dark:hover:bg-red-900/40 transition disabled:opacity-50"
                                  aria-label="Delete order"
                                >
                                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                                  </svg>
                                </button>
                              </div>
                            </th>
                          </tr>
                        </thead>
                        <tbody>
                          {groupOrderItemsByShipment(o).map((group) =>
                            group.items.map((item, itemIndex) => {
                              const isFirstInGroup = itemIndex === 0
                              const effectiveStatus = getEffectiveItemStatus(item)
                              const nextStatuses = group.items
                                .map((i) => getNextStatus(getEffectiveItemStatus(i)))
                                .filter((s): s is EffectiveItemStatus => s != null)
                              const lowestNext =
                                nextStatuses.length > 0
                                  ? STATUS_PROGRESSION[Math.min(...nextStatuses.map((s) => STATUS_PROGRESSION.indexOf(s)))]
                                  : null
                              const advanceLabel = lowestNext ? STATUS_LABELS[lowestNext] ?? lowestNext : null
                              const isSubmitted = lowestNext === 'submitted'
                              const isScanned = lowestNext === 'scanned'
                              const hideAdvanceButton = lowestNext === 'payment_requested'
                              const trackingRaw = trackingEdits[item.id] ?? getTracking(item.id)
                              const trackingInfo = trackingInfoByItemId[item.id] ?? null
                              return (
                                <tr key={item.id} className={getStatusRowClass(effectiveStatus)}>
                                  <td className="py-1 px-2">
                                    <input
                                      type="checkbox"
                                      checked={selectedItemIds.has(item.id)}
                                      onChange={() => toggleItemSelect(item.id, o.items!)}
                                      className="rounded border-brand-300 text-brand-600 focus:ring-brand-500"
                                    />
                                  </td>
                                  <td className="py-1 px-2">
                                    <input
                                      type="number"
                                      min={1}
                                      value={itemEdits[item.id]?.quantity ?? (item.quantity ?? 1)}
                                      onChange={(e) => {
                                        const n = parseInt(e.target.value, 10)
                                        if (!Number.isNaN(n) && n >= 1)
                                          setItemEdits((prev) => ({ ...prev, [item.id]: { ...prev[item.id], quantity: n } }))
                                      }}
                                      onBlur={() => {
                                        const v = itemEdits[item.id]?.quantity
                                        if (v != null && v !== (item.quantity ?? 1)) updateItem(item.id, { quantity: v })
                                      }}
                                      disabled={savingItemId === item.id}
                                      className={`w-12 h-5 rounded border border-brand-200 dark:border-gray-600 px-1 py-0 text-sm text-center focus:border-brand-500 focus:outline-none disabled:opacity-60 ${getStatusInputClass(effectiveStatus)}`}
                                    />
                                  </td>
                                  <td className="py-1 px-2">
                                    <input
                                      type="text"
                                      value={itemEdits[item.id]?.description ?? (item.description ?? '')}
                                      onChange={(e) =>
                                        setItemEdits((prev) => ({ ...prev, [item.id]: { ...prev[item.id], description: e.target.value } }))
                                      }
                                      onBlur={() => {
                                        const v = (itemEdits[item.id]?.description ?? item.description ?? '').trim()
                                        if (v !== (item.description ?? '')) updateItem(item.id, { description: v || null })
                                      }}
                                      placeholder="Description"
                                      disabled={savingItemId === item.id}
                                      className={`w-full min-w-[7rem] h-5 rounded border border-brand-200 dark:border-gray-600 px-2 py-0 text-sm focus:border-brand-500 focus:outline-none disabled:opacity-60 ${getStatusInputClass(effectiveStatus)}`}
                                    />
                                  </td>
                                  <td className="py-1 px-2">
                                    <div className="flex items-stretch min-w-0 h-5 rounded border border-brand-200 dark:border-gray-600 focus-within:border-brand-500">
                                      <input
                                        type="text"
                                        value={trackingRaw}
                                        onChange={(e) => setTrackingEdits((prev) => ({ ...prev, [item.id]: e.target.value }))}
                                        onBlur={(e) => {
                                          const v = e.target.value
                                          const current = getTracking(item.id)
                                          if (v.trim() !== current.trim() || (v === '' && current !== '')) saveItemTracking(item.id, v)
                                          else setTrackingEdits((prev) => { const next = { ...prev }; delete next[item.id]; return next })
                                        }}
                                        placeholder=""
                                        disabled={savingTrackingId === item.id}
                                        className="flex-1 min-w-[5rem] h-5 border-0 rounded-l px-2 py-0 text-sm focus:ring-0 focus:outline-none disabled:opacity-60"
                                      />
                                      {trackingInfo && trackingInfo.url && (
                                        <a
                                          href={trackingInfo.url}
                                          target="_blank"
                                          rel="noopener noreferrer"
                                          className="shrink-0 flex items-center gap-1 h-5 border-l border-brand-200 dark:border-gray-600 pl-2 pr-2 py-0 text-xs font-medium text-brand-600 dark:text-brand-400 hover:bg-brand-50 dark:hover:bg-gray-700/50"
                                          title={`Track via ${trackingInfo.carrier}`}
                                        >
                                          <span className="whitespace-nowrap">{trackingInfo.carrier}</span>
                                          <svg className="w-3.5 h-3.5 shrink-0 opacity-70" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                                          </svg>
                                        </a>
                                      )}
                                    </div>
                                  </td>
                                  <td className="py-1 px-2">
                                    <input
                                      type="text"
                                      value={itemEdits[item.id]?.price_paid ?? (item.price_paid ?? '')}
                                      onChange={(e) =>
                                        setItemEdits((prev) => ({ ...prev, [item.id]: { ...prev[item.id], price_paid: e.target.value } }))
                                      }
                                      onBlur={() => {
                                        const v = (itemEdits[item.id]?.price_paid ?? item.price_paid ?? '').trim()
                                        if (v !== (item.price_paid ?? '')) updateItem(item.id, { price_paid: v || null })
                                      }}
                                      placeholder="0.00"
                                      disabled={savingItemId === item.id}
                                      className={`w-20 h-5 rounded border border-brand-200 dark:border-gray-600 px-1.5 py-0 text-sm font-mono focus:border-brand-500 focus:outline-none disabled:opacity-60 ${getStatusInputClass(effectiveStatus)}`}
                                    />
                                  </td>
                                  <td className="py-1 px-2">
                                    <input
                                      type="text"
                                      value={itemEdits[item.id]?.price_sold ?? (item.price_sold ?? '')}
                                      onChange={(e) =>
                                        setItemEdits((prev) => ({ ...prev, [item.id]: { ...prev[item.id], price_sold: e.target.value } }))
                                      }
                                      onBlur={() => {
                                        const v = (itemEdits[item.id]?.price_sold ?? item.price_sold ?? '').trim()
                                        if (v !== (item.price_sold ?? '')) updateItem(item.id, { price_sold: v || null })
                                      }}
                                      placeholder="0.00"
                                      disabled={savingItemId === item.id}
                                      className={`w-20 h-5 rounded border border-brand-200 dark:border-gray-600 px-1.5 py-0 text-sm font-mono focus:border-brand-500 focus:outline-none disabled:opacity-60 ${getStatusInputClass(effectiveStatus)}`}
                                    />
                                  </td>
                                  <td className="py-1 px-2 text-right font-mono text-sm tabular-nums">
                                    ${((parseDecimal(itemEdits[item.id]?.price_paid ?? item.price_paid) * (itemEdits[item.id]?.quantity ?? item.quantity ?? 1))).toFixed(2)}
                                  </td>
                                  <td className="py-1 px-2">
                                    <input
                                      type="text"
                                      value={itemEdits[item.id]?.shipping ?? (item.shipping ?? '')}
                                      onChange={(e) =>
                                        setItemEdits((prev) => ({ ...prev, [item.id]: { ...prev[item.id], shipping: e.target.value } }))
                                      }
                                      onBlur={() => {
                                        const v = (itemEdits[item.id]?.shipping ?? item.shipping ?? '').trim()
                                        if (v !== (item.shipping ?? '')) updateItem(item.id, { shipping: v || null })
                                      }}
                                      placeholder="0.00"
                                      disabled={savingItemId === item.id}
                                      className={`w-20 h-5 rounded border border-brand-200 dark:border-gray-600 px-1.5 py-0 text-sm font-mono focus:border-brand-500 focus:outline-none disabled:opacity-60 ${getStatusInputClass(effectiveStatus)}`}
                                    />
                                  </td>
                                  <td className="py-1 px-2">
                                    <input
                                      type="text"
                                      value={itemEdits[item.id]?.sales_tax ?? (item.sales_tax ?? '')}
                                      onChange={(e) =>
                                        setItemEdits((prev) => ({ ...prev, [item.id]: { ...prev[item.id], sales_tax: e.target.value } }))
                                      }
                                      onBlur={() => {
                                        const v = (itemEdits[item.id]?.sales_tax ?? item.sales_tax ?? '').trim()
                                        if (v !== (item.sales_tax ?? '')) updateItem(item.id, { sales_tax: v || null })
                                      }}
                                      placeholder="0.00"
                                      disabled={savingItemId === item.id}
                                      className={`w-20 h-5 rounded border border-brand-200 dark:border-gray-600 px-1.5 py-0 text-sm font-mono focus:border-brand-500 focus:outline-none disabled:opacity-60 ${getStatusInputClass(effectiveStatus)}`}
                                    />
                                  </td>
                                  <td className="py-1 px-2 text-right font-mono text-sm tabular-nums">
                                    ${((parseDecimal(itemEdits[item.id]?.price_paid ?? item.price_paid) + parseDecimal(itemEdits[item.id]?.shipping ?? item.shipping) + parseDecimal(itemEdits[item.id]?.sales_tax ?? item.sales_tax)) * (itemEdits[item.id]?.quantity ?? item.quantity ?? 1)).toFixed(2)}
                                  </td>
                                  <td className="py-1 px-2 text-right font-mono text-sm tabular-nums">
                                    ${((parseDecimal(itemEdits[item.id]?.price_sold ?? item.price_sold) * (itemEdits[item.id]?.quantity ?? item.quantity ?? 1))).toFixed(2)}
                                  </td>
                                  <td className="py-1 px-2">
                                    <select
                                      value={itemEdits[item.id]?.status ?? item.status}
                                      onChange={(e) => {
                                        const s = e.target.value as ItemStatus
                                        setItemEdits((prev) => ({ ...prev, [item.id]: { ...prev[item.id], status: s } }))
                                        updateItem(item.id, { status: s })
                                      }}
                                      disabled={savingItemId === item.id}
                                      className={`min-w-[6.5rem] h-5 rounded border border-brand-200 dark:border-gray-600 px-1.5 py-0 text-sm focus:border-brand-500 focus:outline-none disabled:opacity-60 ${getStatusInputClass(effectiveStatus)}`}
                                    >
                                      {ITEM_STATUSES_FOR_EDIT.map((val) => (
                                        <option key={val} value={val}>
                                          {STATUS_LABELS[val]}
                                        </option>
                                      ))}
                                    </select>
                                  </td>
                                  <td className="py-1 px-1.5 text-right w-8">
                                    <div className="flex items-center justify-end gap-0.5">
                                      {getNextStatus(effectiveStatus) === 'scanned' ? (
                                        <button
                                          type="button"
                                          onClick={() => setScanSingleItemModal(item)}
                                          className="p-1 rounded text-xs font-medium bg-brand-600 text-white hover:bg-brand-700"
                                          title="Mark as Scanned"
                                          aria-label="Mark as Scanned"
                                        >
                                          Scanned
                                        </button>
                                      ) : isFirstInGroup && advanceLabel && !hideAdvanceButton ? (
                                        <button
                                          type="button"
                                          onClick={() => {
                                            if (isSubmitted) setSubmitShipmentModal({ group })
                                            else if (isScanned) setScanReceiptModal({ group })
                                            else advanceShipmentToNextStatus(group)
                                          }}
                                          disabled={advancingGroupKey === group.key}
                                          className="p-1 rounded text-xs font-medium bg-brand-600 text-white hover:bg-brand-700 disabled:opacity-50"
                                          title={isSubmitted ? 'Mark as Submitted' : isScanned ? 'Mark as Scanned' : `Advance to ${advanceLabel}`}
                                          aria-label={isSubmitted ? 'Mark as Submitted' : isScanned ? 'Mark as Scanned' : `Advance to ${advanceLabel}`}
                                        >
                                          {advancingGroupKey === group.key ? '…' : advanceLabel}
                                        </button>
                                      ) : null}
                                      {(item.quantity ?? 1) >= 2 ? (
                                        <button
                                          type="button"
                                          onClick={() => setSplitModalItem(item)}
                                          className="p-1 rounded text-ink-muted hover:text-red-600 hover:bg-brand-100 dark:hover:bg-gray-600 transition shrink-0"
                                          title="Split for separate shipping"
                                          aria-label="Split for separate shipping"
                                        >
                                          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}>
                                            <circle cx="6" cy="7" r="3" />
                                            <circle cx="6" cy="17" r="3" />
                                            <path d="M8.6 8.6l10.4 10.4M8.6 15.4l10.4 -10.4" />
                                          </svg>
                                        </button>
                                      ) : (
                                        <span className="inline-block w-6 h-6 shrink-0" aria-hidden />
                                      )}
                                      <button
                                        type="button"
                                        onClick={() => copyItem(o, item)}
                                        disabled={copyingItemId === item.id}
                                        className="p-1 rounded text-ink-muted hover:text-brand-600 hover:bg-brand-100 dark:hover:bg-gray-600 disabled:opacity-50"
                                        title="Copy line item"
                                        aria-label="Copy line item"
                                      >
                                        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                                        </svg>
                                      </button>
                                      <button
                                        type="button"
                                        onClick={() => setConfirmDeleteItemId(item.id)}
                                        disabled={deletingItemId === item.id}
                                        className="p-1 rounded text-ink-muted hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 disabled:opacity-50"
                                        title="Delete line item"
                                        aria-label="Delete line item"
                                      >
                                        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                                        </svg>
                                      </button>
                                    </div>
                                  </td>
                                </tr>
                              )
                            })
                          )}
                        </tbody>
                      </table>
                      {getSelectedIdsForOrder(o).length > 0 && (
                        <div className="mt-2 px-2 pb-2 flex flex-wrap items-center gap-3">
                          <select
                            value={getBulkActionState(o.id).action}
                            onChange={(e) => {
                              const v = e.target.value
                              if (v === 'mark_received') {
                                const ids = getSelectedIdsForOrder(o)
                                if (ids.length > 0) {
                                  setBulkStatusModal({ order: o, itemIds: ids })
                                  setBulkActionState(o.id, { action: '' })
                                }
                              } else if (v === 'mark_scanned') {
                                const ids = getSelectedIdsForOrder(o)
                                if (ids.length > 0) {
                                  setBulkScanModal({ order: o, itemIds: ids })
                                  setBulkActionState(o.id, { action: '' })
                                }
                              } else if (v === 'delete_items') {
                                const ids = getSelectedIdsForOrder(o)
                                if (ids.length > 0) {
                                  setConfirmBulkDeleteItemIds(ids)
                                  setBulkActionState(o.id, { action: '' })
                                }
                              } else if (v === 'copy_tracking') {
                                const ids = getSelectedIdsForOrder(o)
                                const numbers = [...new Set(ids.map((id) => getTracking(id)).filter(Boolean))]
                                if (numbers.length > 0) {
                                  copyToClipboard(numbers.join('\n'))
                                }
                                setBulkActionState(o.id, { action: '' })
                              } else if (v === 'copy_tracking_usabg') {
                                const ids = getSelectedIdsForOrder(o)
                                const selectedItems = (o.items ?? []).filter((item) => ids.includes(item.id))
                                const byTracking = new Map<string, number>()
                                for (const item of selectedItems) {
                                  const tn = getTracking(item.id)?.trim()
                                  if (!tn) continue
                                  const qty = itemEdits[item.id]?.quantity ?? item.quantity ?? 1
                                  const priceSold = itemEdits[item.id]?.price_sold ?? item.price_sold ?? ''
                                  const payout = parseDecimal(priceSold) * qty
                                  byTracking.set(tn, (byTracking.get(tn) ?? 0) + payout)
                                }
                                if (byTracking.size > 0) {
                                  const lines = [...byTracking.entries()].map(([tracking, total]) => `${tracking}$${total.toFixed(2)}`)
                                  copyToClipboard(lines.join('\n'))
                                }
                                setBulkActionState(o.id, { action: '' })
                              } else setBulkActionState(o.id, { action: v })
                            }}
                            className="h-6 rounded-lg border border-brand-200 dark:border-gray-600 px-2 py-0 text-sm !bg-brand-50/50 dark:!bg-gray-600/80"
                          >
                            <option value="">Choose action…</option>
                            <option value="copy_tracking">Copy Tracking Numbers</option>
                            <option value="copy_tracking_usabg">Copy Tracking Numbers (USABG)</option>
                            <option value="input_tracking">Input Tracking</option>
                            <option value="mark_received">Mark as Received</option>
                            <option value="mark_scanned">Mark scanned</option>
                            <option disabled>────────────</option>
                            <option value="delete_items">Delete items</option>
                          </select>
                          {getBulkActionState(o.id).action === 'input_tracking' && (
                            <>
                              <input
                                type="text"
                                placeholder="Tracking number"
                                value={getBulkActionState(o.id).tracking}
                                onChange={(e) => setBulkActionState(o.id, { tracking: e.target.value })}
                                className="h-6 rounded-lg border border-brand-200 dark:border-gray-600 px-3 py-0 text-sm w-48 !bg-brand-50/50 dark:!bg-gray-600/80"
                              />
                              <input
                                type="date"
                                value={getBulkActionState(o.id).shippedAt}
                                onChange={(e) => setBulkActionState(o.id, { shippedAt: e.target.value })}
                                className="h-6 rounded-lg border border-brand-200 dark:border-gray-600 px-2 py-0 text-sm"
                              />
                              <button
                                type="button"
                                disabled={!getBulkActionState(o.id).tracking.trim() || bulkActionShippingOrderId === o.id}
                                onClick={() => applyBulkInputTracking(o)}
                                className="text-sm px-3 py-1.5 bg-brand-600 text-white rounded-lg hover:bg-brand-700 disabled:opacity-50"
                              >
                                {bulkActionShippingOrderId === o.id ? 'Applying…' : 'Apply'}
                              </button>
                            </>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>
            )
          })
        )}
      </div>

      <ConfirmDialog
        open={confirmDeleteItemId !== null}
        message="Delete this line item? This cannot be undone."
        confirmLabel="Delete"
        danger
        onConfirm={() => {
          if (confirmDeleteItemId !== null) {
            deleteItem(confirmDeleteItemId)
            setConfirmDeleteItemId(null)
          }
        }}
        onCancel={() => setConfirmDeleteItemId(null)}
      />
      <ConfirmDialog
        open={confirmBulkDeleteItemIds !== null && confirmBulkDeleteItemIds.length > 0}
        message={`Delete ${confirmBulkDeleteItemIds?.length ?? 0} selected items? This cannot be undone.`}
        confirmLabel="Delete"
        danger
        onConfirm={() => {
          const ids = confirmBulkDeleteItemIds ?? []
          setConfirmBulkDeleteItemIds(null)
          deleteItems(ids)
        }}
        onCancel={() => setConfirmBulkDeleteItemIds(null)}
      />
      <ConfirmDialog
        open={confirmDeleteOrderId !== null}
        message="Delete this order and all its line items? This cannot be undone."
        confirmLabel="Delete order"
        danger
        onConfirm={() => {
          if (confirmDeleteOrderId !== null) {
            deleteOrder(confirmDeleteOrderId)
            setConfirmDeleteOrderId(null)
          }
        }}
        onCancel={() => setConfirmDeleteOrderId(null)}
      />
      {bulkStatusModal && (
        <BulkStatusModal
          order={bulkStatusModal.order}
          itemIds={bulkStatusModal.itemIds}
          onApply={(receiptIds) => applyBulkReceived(bulkStatusModal.itemIds, receiptIds)}
          onClose={() => setBulkStatusModal(null)}
        />
      )}
      {bulkScanModal && (
        <BulkScanModal
          order={bulkScanModal.order}
          itemIds={bulkScanModal.itemIds}
          onApply={(receiptIds) => applyBulkScanned(Object.keys(receiptIds).map(Number), receiptIds)}
          onClose={() => setBulkScanModal(null)}
        />
      )}
      {scanSingleItemModal && (
        <ScanSingleItemModal
          item={scanSingleItemModal}
          onApply={async (receiptId) => {
            const now = new Date().toISOString().slice(0, 19)
            await updateItem(scanSingleItemModal.id, {
              status: 'scanned',
              scanned_at: now,
              receipt_id: receiptId.trim() || null,
            })
            setScanSingleItemModal(null)
          }}
          onClose={() => setScanSingleItemModal(null)}
        />
      )}
      {scanReceiptModal && (
        <ScanReceiptModal
          group={scanReceiptModal.group}
          onApply={(receiptIds) => applyScanShipment(scanReceiptModal.group, receiptIds)}
          onClose={() => setScanReceiptModal(null)}
          applying={advancingGroupKey === scanReceiptModal.group.key}
        />
      )}
      {submitShipmentModal && (
        <SubmitShipmentModal
          group={submitShipmentModal.group}
          onApply={(submissionId) => applySubmitShipment(submitShipmentModal.group, submissionId)}
          onClose={() => setSubmitShipmentModal(null)}
          applying={advancingGroupKey === submitShipmentModal.group.key}
        />
      )}
      {splitModalItem && (
        <SplitItemModal
          item={splitModalItem}
          onConfirm={(keepQuantity) =>
            splitItem(splitModalItem.id, keepQuantity, splitModalItem.quantity ?? 1, splitModalItem.order_id)
          }
          onClose={() => setSplitModalItem(null)}
        />
      )}
      {addAccountModal && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50" onClick={() => setAddAccountModal(null)}>
          <div
            className="bg-white dark:bg-gray-800 rounded-xl shadow-xl p-6 max-w-sm w-full mx-4 border border-brand-200/80 dark:border-gray-700"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-lg font-medium text-ink mb-4">Add Account</h3>
            <p className="text-sm text-ink-muted mb-4">
              Add a new account for <span className="font-medium text-ink">{addAccountModal.storeName}</span>
            </p>
            <input
              type="text"
              value={addAccountName}
              onChange={(e) => setAddAccountName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && addAccountName.trim() && !addingAccount) {
                  handleAddAccount()
                }
              }}
              placeholder="Account name"
              autoFocus
              className="w-full h-10 rounded-lg border border-brand-200 dark:border-gray-600 px-3 py-2 text-sm bg-white dark:bg-gray-700 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500 mb-6"
            />
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setAddAccountModal(null)}
                className="px-3 py-1.5 border border-brand-300 dark:border-gray-600 rounded-lg text-sm text-ink hover:bg-brand-50 dark:hover:bg-gray-700"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleAddAccount}
                disabled={!addAccountName.trim() || addingAccount}
                className="px-3 py-1.5 bg-brand-600 text-white rounded-lg text-sm hover:bg-brand-700 disabled:opacity-50"
              >
                {addingAccount ? 'Adding…' : 'Add'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )

  async function handleAddAccount() {
    if (!addAccountName.trim() || !addAccountModal) return
    setAddingAccount(true)
    try {
      const a = await api.post<StoreAccount>(`/stores/${addAccountModal.storeId}/accounts`, { name: addAccountName.trim() })
      setAccountsByStore((prev) => ({
        ...prev,
        [addAccountModal.storeId]: [...(prev[addAccountModal.storeId] ?? []), a],
      }))
      setPendingAccountSelection({ orderId: addAccountModal.orderId, storeId: addAccountModal.storeId, accountId: a.id })
      setAddAccountModal(null)
      setAddAccountName('')
    } catch (err) {
      console.error(err)
    } finally {
      setAddingAccount(false)
    }
  }

  async function handleCreateNewOrder() {
    if (stores.length === 0) {
      console.error('No stores available to create an order')
      return
    }
    setCreatingOrder(true)
    try {
      const newOrder = await api.post<Order>('/orders', {
        store_id: stores[0].id,
        purchase_date: nowIso(),
      })
      const newItem = await api.post<Item>('/items', { order_id: newOrder.id, status: 'purchased' })
      setOrders((prev) => [{ ...newOrder, items: [newItem] }, ...prev])
    } catch (err) {
      console.error(err)
    } finally {
      setCreatingOrder(false)
    }
  }
}

function ScanSingleItemModal({
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

function BulkScanModal({
  order,
  itemIds,
  onApply,
  onClose,
}: {
  order: Order
  itemIds: number[]
  onApply: (receiptIds: Record<number, string>) => Promise<void>
  onClose: () => void
}) {
  const items = (order.items ?? []).filter((i) => itemIds.includes(i.id) && getNextStatus(getEffectiveItemStatus(i)) === 'scanned')
  const [receiptIds, setReceiptIds] = useState<Record<number, string>>(() =>
    items.reduce<Record<number, string>>((acc, i) => {
      acc[i.id] = i.receipt_id ?? ''
      return acc
    }, {})
  )
  const [applying, setApplying] = useState(false)
  if (items.length === 0) {
    return (
      <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50" onClick={onClose}>
        <div
          className="bg-white dark:bg-gray-800 rounded-xl shadow-xl p-6 max-w-md w-full mx-4 border border-brand-200/80 dark:border-gray-700"
          onClick={(e) => e.stopPropagation()}
        >
          <h3 className="text-lg font-medium text-ink mb-2">Mark scanned</h3>
          <p className="text-sm text-ink-muted mb-4">None of the selected items can be marked scanned (they must be in Delivered status).</p>
          <div className="flex justify-end">
            <button type="button" onClick={onClose} className="px-3 py-1.5 border border-brand-300 dark:border-gray-600 rounded-lg text-sm text-ink hover:bg-brand-50 dark:hover:bg-gray-700">
              Close
            </button>
          </div>
        </div>
      </div>
    )
  }
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-white dark:bg-gray-800 rounded-xl shadow-xl p-6 max-w-2xl w-full mx-4 border border-brand-200/80 dark:border-gray-700 max-h-[80vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-lg font-medium text-ink mb-4">
          Mark {items.length} item{items.length !== 1 ? 's' : ''} as Scanned
        </h3>
        <div className="overflow-auto flex-1 min-h-0">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left border-b border-brand-200 dark:border-gray-600">
                <th className="py-2 px-2 font-medium text-ink-muted w-12">Qty</th>
                <th className="py-2 px-2 font-medium text-ink-muted">Description</th>
                <th className="py-2 px-2 font-medium text-ink-muted min-w-[10rem]">Receipt ID</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id} className="border-b border-brand-100 dark:border-gray-700 last:border-0">
                  <td className="py-2 px-2">{item.quantity ?? 1}</td>
                  <td className="py-2 px-2 text-ink">{item.description || '—'}</td>
                  <td className="py-2 px-2">
                    <input
                      type="text"
                      value={receiptIds[item.id] ?? ''}
                      onChange={(e) => setReceiptIds((prev) => ({ ...prev, [item.id]: e.target.value }))}
                      placeholder="Receipt ID (optional)"
                      className="w-full min-w-[10rem] h-8 rounded border border-brand-200 dark:border-gray-600 px-2 py-1 text-sm bg-white dark:bg-gray-700 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="flex justify-end gap-2 mt-4 pt-4 border-t border-brand-200 dark:border-gray-600">
          <button type="button" onClick={onClose} className="px-3 py-1.5 border border-brand-300 dark:border-gray-600 rounded-lg text-sm text-ink hover:bg-brand-50 dark:hover:bg-gray-700">
            Cancel
          </button>
          <button
            type="button"
            onClick={async () => {
              setApplying(true)
              try {
                await onApply(receiptIds)
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

function BulkStatusModal({
  order,
  itemIds,
  onApply,
  onClose,
}: {
  order: Order
  itemIds: number[]
  onApply: (receiptIds: Record<number, string>) => Promise<void>
  onClose: () => void
}) {
  const items = (order.items ?? []).filter((i) => itemIds.includes(i.id))
  const [receiptIds, setReceiptIds] = useState<Record<number, string>>(() =>
    items.reduce<Record<number, string>>((acc, i) => {
      acc[i.id] = i.receipt_id ?? ''
      return acc
    }, {})
  )
  const [applying, setApplying] = useState(false)
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-white dark:bg-gray-800 rounded-xl shadow-xl p-6 max-w-2xl w-full mx-4 border border-brand-200/80 dark:border-gray-700 max-h-[80vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-lg font-medium text-ink mb-4">
          Mark {items.length} item{items.length !== 1 ? 's' : ''} as Received
        </h3>
        <div className="overflow-auto flex-1 min-h-0">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left border-b border-brand-200 dark:border-gray-600">
                <th className="py-2 px-2 font-medium text-ink-muted w-12">Qty</th>
                <th className="py-2 px-2 font-medium text-ink-muted">Description</th>
                <th className="py-2 px-2 font-medium text-ink-muted min-w-[10rem]">Receipt ID</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id} className="border-b border-brand-100 dark:border-gray-700 last:border-0">
                  <td className="py-2 px-2">{item.quantity ?? 1}</td>
                  <td className="py-2 px-2 text-ink">{item.description || '—'}</td>
                  <td className="py-2 px-2">
                    <input
                      type="text"
                      value={receiptIds[item.id] ?? ''}
                      onChange={(e) => setReceiptIds((prev) => ({ ...prev, [item.id]: e.target.value }))}
                      placeholder="Receipt ID (optional)"
                      className="w-full min-w-[10rem] h-8 rounded border border-brand-200 dark:border-gray-600 px-2 py-1 text-sm bg-white dark:bg-gray-700 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="flex justify-end gap-2 mt-4 pt-4 border-t border-brand-200 dark:border-gray-600">
          <button type="button" onClick={onClose} className="px-3 py-1.5 border border-brand-300 dark:border-gray-600 rounded-lg text-sm text-ink hover:bg-brand-50 dark:hover:bg-gray-700">
            Cancel
          </button>
          <button
            type="button"
            onClick={async () => {
              setApplying(true)
              try {
                await onApply(receiptIds)
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

function ScanReceiptModal({
  group,
  onApply,
  onClose,
  applying,
}: {
  group: { key: string; label: string; trackingNumber: string | null; items: Item[] }
  onApply: (receiptIds: Record<number, string>) => Promise<void>
  onClose: () => void
  applying: boolean
}) {
  const toUpdate = group.items.filter((item) => getNextStatus(getEffectiveItemStatus(item)) === 'scanned')
  const [receiptIds, setReceiptIds] = useState<Record<number, string>>(() =>
    toUpdate.reduce<Record<number, string>>((acc, i) => {
      acc[i.id] = i.receipt_id ?? ''
      return acc
    }, {})
  )
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-white dark:bg-gray-800 rounded-xl shadow-xl p-6 max-w-2xl w-full mx-4 border border-brand-200/80 dark:border-gray-700 max-h-[80vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-lg font-medium text-ink mb-2">Mark as Scanned</h3>
        <p className="text-sm text-ink-muted mb-4">
          {toUpdate.length} item{toUpdate.length !== 1 ? 's' : ''} in this shipment
          {group.trackingNumber && <span className="ml-1 font-mono text-ink">({group.trackingNumber})</span>}
        </p>
        <div className="overflow-auto flex-1 min-h-0">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left border-b border-brand-200 dark:border-gray-600">
                <th className="py-2 px-2 font-medium text-ink-muted w-12">Qty</th>
                <th className="py-2 px-2 font-medium text-ink-muted">Description</th>
                <th className="py-2 px-2 font-medium text-ink-muted min-w-[10rem]">Receipt ID</th>
              </tr>
            </thead>
            <tbody>
              {toUpdate.map((item) => (
                <tr key={item.id} className="border-b border-brand-100 dark:border-gray-700 last:border-0">
                  <td className="py-2 px-2">{item.quantity ?? 1}</td>
                  <td className="py-2 px-2 text-ink">{item.description || '—'}</td>
                  <td className="py-2 px-2">
                    <input
                      type="text"
                      value={receiptIds[item.id] ?? ''}
                      onChange={(e) => setReceiptIds((prev) => ({ ...prev, [item.id]: e.target.value }))}
                      placeholder="Receipt ID (optional)"
                      className="w-full h-8 rounded border border-brand-200 dark:border-gray-600 px-2 py-1 text-sm bg-white dark:bg-gray-700 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="flex justify-end gap-2 mt-4 pt-4 border-t border-brand-200 dark:border-gray-600">
          <button type="button" onClick={onClose} className="px-3 py-1.5 border border-brand-300 dark:border-gray-600 rounded-lg text-sm text-ink hover:bg-brand-50 dark:hover:bg-gray-700">
            Cancel
          </button>
          <button
            type="button"
            onClick={async () => await onApply(receiptIds)}
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

function SubmitShipmentModal({
  group,
  onApply,
  onClose,
  applying,
}: {
  group: { key: string; label: string; trackingNumber: string | null; items: Item[] }
  onApply: (submissionId: string) => Promise<void>
  onClose: () => void
  applying: boolean
}) {
  const [submissionId, setSubmissionId] = useState('')
  const itemCount = group.items.length
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-white dark:bg-gray-800 rounded-xl shadow-xl p-6 max-w-md w-full mx-4 border border-brand-200/80 dark:border-gray-700"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-lg font-medium text-ink mb-2">Mark shipment as Submitted</h3>
        <p className="text-sm text-ink-muted mb-4">
          {itemCount} item{itemCount !== 1 ? 's' : ''} in this shipment
          {group.trackingNumber && <span className="ml-1 font-mono text-ink">({group.trackingNumber})</span>}
        </p>
        <label className="block text-sm font-medium text-ink mb-2">Submission ID (optional)</label>
        <input
          type="text"
          value={submissionId}
          onChange={(e) => setSubmissionId(e.target.value)}
          placeholder="ID the buying group assigns to this submission"
          className="w-full h-10 rounded-lg border border-brand-200 dark:border-gray-600 px-3 py-2 text-sm bg-white dark:bg-gray-700 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500 mb-6"
        />
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className="px-3 py-1.5 border border-brand-300 dark:border-gray-600 rounded-lg text-sm text-ink hover:bg-brand-50 dark:hover:bg-gray-700">
            Cancel
          </button>
          <button
            type="button"
            onClick={async () => await onApply(submissionId)}
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

function SplitItemModal({
  item,
  onConfirm,
  onClose,
}: {
  item: Item
  onConfirm: (keepQuantity: number) => void
  onClose: () => void
}) {
  const qty = item.quantity ?? 1
  const [keepQuantity, setKeepQuantity] = useState(1)
  const splitOff = Math.max(0, qty - keepQuantity)
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-white dark:bg-gray-800 rounded-xl shadow-xl p-6 max-w-md w-full mx-4 border border-brand-200/80 dark:border-gray-700"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-lg font-medium text-ink mb-2">Split for separate shipping</h3>
        <p className="text-sm text-ink-muted mb-4">
          Split {qty}× {item.description || 'Item'} into two lines so they can ship with different tracking numbers.
        </p>
        <div className="flex gap-4 items-center mb-6">
          <label className="flex items-center gap-2">
            <span className="text-sm text-ink-muted">Keep</span>
            <input
              type="number"
              min={1}
              max={Math.max(1, qty - 1)}
              value={keepQuantity}
              onChange={(e) => setKeepQuantity(Math.max(1, Math.min(qty - 1, parseInt(e.target.value, 10) || 1)))}
              className="w-16 rounded border border-brand-200 dark:border-gray-600 px-2 py-1.5 text-sm bg-white dark:bg-gray-700"
            />
            <span className="text-sm text-ink-muted">here</span>
          </label>
          <span className="text-ink-muted">→</span>
          <span className="text-sm text-ink-muted">Split off {splitOff} as new line</span>
        </div>
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-1.5 border border-brand-300 dark:border-gray-600 rounded-lg text-sm text-ink hover:bg-brand-50 dark:hover:bg-gray-700"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => onConfirm(keepQuantity)}
            disabled={splitOff < 1}
            className="px-3 py-1.5 bg-brand-600 text-white rounded-lg text-sm hover:bg-brand-700 disabled:opacity-50"
          >
            Split
          </button>
        </div>
      </div>
    </div>
  )
}
