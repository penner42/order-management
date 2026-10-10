export type ItemStatus =
  | 'purchased'
  | 'shipped'
  | 'submitted'
  | 'scanned'
  | 'canceled'
  | 'needs_return'
  | 'return_started'
  | 'return_sent'
  | 'return_received'
  | 'return_refunded'
  | 'lost_package'

/** Effective status for display: item status or payment status when item is on a payment. */
export type EffectiveItemStatus = ItemStatus | 'payment_requested' | 'payment_sent' | 'payment_received'

export type UserRole = 'admin' | 'user'

export interface User {
  id: number
  username: string
  email: string | null
  name: string | null
  role: UserRole
  created_at: string
  updated_at?: string
}

export type BuyingGroupApiFramework = 'parsefile'

export interface BuyingGroup {
  id: number
  user_id: number | null
  name: string
  aliases: string[]
  api_framework?: BuyingGroupApiFramework | null
  /** Present on buying-groups CRUD responses; omitted when nested on orders/payments. */
  base_url?: string | null
  api_url?: string | null
  bearer_token?: string | null
  api_user_id?: number | null
  api_email?: string | null
  tracking_submit_enabled?: boolean
  tracking_submit_cron?: string
  tracking_submit_last_run_at?: string | null
}

export interface Reward {
  id: number
  user_id: number | null
  name: string
}

/** Sub-method (no further nesting). Same shape as PaymentMethod for display. */
export interface PaymentMethodNested {
  id: number
  user_id: number | null
  reward_id: number | null
  parent_id: number | null
  label: string
  reward?: Reward | null
  created_at?: string | null
}

export interface PaymentMethod {
  id: number
  user_id: number | null
  reward_id: number | null
  parent_id?: number | null
  reward?: Reward | null
  label: string
  created_at: string
  /** Only present on top-level methods from list API */
  sub_methods?: PaymentMethodNested[]
}

/** Per-store earnings for a payment method (e.g. points per dollar). */
export interface PaymentMethodStoreEarningsEntry {
  store_id: number
  store: Store
  points_per_dollar: number
}

export interface Store {
  id: number
  user_id: number | null
  name: string
}

export interface StoreAccount {
  id: number
  store_id: number
  name: string
}

export interface OrderPaymentMethod {
  id: number
  order_id: number
  payment_method_id: number
  amount: string | null
  payment_method?: PaymentMethod
}

export interface Item {
  id: number
  order_id: number
  price_paid: string | null
  price_sold: string | null
  status: ItemStatus
  quantity: number
  description: string | null
  shipping: string | null
  sales_tax: string | null
  submission_id: string | null
  receipt_id: string | null
  created_at: string
  updated_at?: string
  purchased_at: string | null
  submitted_at: string | null
  scanned_at: string | null
  /** Set when item is on a payment (from Payment). */
  payment_id: number | null
  payment_requested_at: string | null
  payment_sent_at: string | null
  payment_received_at: string | null
  canceled_at: string | null
  needs_return_at: string | null
  return_started_at: string | null
  return_sent_at: string | null
  return_received_at: string | null
  return_refunded_at: string | null
  lost_package_at: string | null
}

export type OrderStatus = 'imported' | 'personal'

export interface Order {
  id: number
  user_id: number | null
  store_id: number
  store_account_id: number | null
  buying_group_id: number | null
  store_order_number: string | null
  status: OrderStatus
  purchase_date: string | null
  shipping: string | null
  sales_tax: string | null
  order_discount: string
  insurance_cost: string
  notes: string | null
  has_invoice: boolean
  created_at: string
  updated_at?: string
  store?: Store
  store_account?: StoreAccount
  buying_group?: BuyingGroup
  items: Item[]
  order_payments: OrderPaymentMethod[]
}

export interface OrderListPage {
  items: Order[]
  page: number
  per_page: number
  total: number
  pages: number
  /** Buying groups with matches for current filters (excluding BG filter). */
  available_buying_group_ids?: number[]
}

export interface ShipmentItem {
  id: number
  shipment_id: number
  item_id: number
  item?: Item
}

export interface Shipment {
  id: number
  user_id: number | null
  tracking_number: string | null
  status: string | null
  shipped_at: string | null
  delivered_at: string | null
  notes: string | null
  created_at: string
  shipment_items: ShipmentItem[]
}

export interface PaymentLineItem {
  id: number
  payment_id: number
  item_id: number
  amount: string | null
  item?: Item | null
}

export interface Payment {
  id: number
  buying_group_id: number
  payment_id: string | null
  payment_bonus?: string | number | null
  payment_requested_at: string | null
  payment_sent_at: string | null
  payment_received_at: string | null
  created_at: string
  updated_at?: string | null
  buying_group?: BuyingGroup | null
  line_items: PaymentLineItem[]
}

export interface BrowserExtensionArtifact {
  filename: string
  size_bytes: number
  updated_at: string
}

export interface BrowserExtensionMeta {
  version: string
  fingerprint: string
  built_at: string
  chrome?: BrowserExtensionArtifact | null
  firefox?: BrowserExtensionArtifact | null
}

export interface BrowserExtensionStatus {
  status: string
  error?: string | null
  available: boolean
  meta?: BrowserExtensionMeta | null
}

export type BrowserProfileStatus =
  | 'logged_out'
  | 'ready'
  | 'login_required'
  | 'login_in_progress'
  | 'importing'
  | 'error'

export interface BrowserProfile {
  id: number
  store_account_id: number
  retailer: string
  status: BrowserProfileStatus | string
  last_error: string | null
  last_import_at: string | null
  full_check_enabled: boolean
  full_check_cron: string
  full_check_max_pages: number
  full_check_last_run_at: string | null
  unshipped_check_enabled: boolean
  unshipped_check_cron: string
  unshipped_check_last_run_at: string | null
  created_at?: string | null
  updated_at?: string | null
  store_id?: number | null
  store_name?: string | null
  store_account_name?: string | null
}

export interface BrowserProfileScheduleUpdate {
  store_account_id?: number
  full_check_enabled?: boolean
  full_check_cron?: string
  full_check_max_pages?: number
  unshipped_check_enabled?: boolean
  unshipped_check_cron?: string
}

export interface BrowserJob {
  id: string
  profile_id: number
  kind: string
  status: string
  message?: string | null
  progress?: Record<string, unknown> | null
  review_url?: string | null
  token?: string | null
  order_count?: number | null
  error?: string | null
}

export type BrowserImportLogLevel = 'updates' | 'info' | 'error'

export type BrowserImportLogEvent =
  | 'order_imported'
  | 'tracking_updated'
  | 'order_checked'
  | 'order_marked_personal'
  | 'order_skipped_ignored_zip' // legacy
  | 'order_error'
  | 'check_started'
  | 'check_finished'

export interface IgnoredZipCode {
  id: number
  zip_code: string
}

export interface BrowserImportLog {
  id: number
  browser_profile_id: number | null
  job_id: string | null
  level: BrowserImportLogLevel | string
  event_type: BrowserImportLogEvent | string
  mode: 'full' | 'unshipped' | string
  scheduled: boolean
  retailer: string
  store_order_number: string | null
  tracking_numbers: string[]
  message: string | null
  store_name: string | null
  store_account_name: string | null
  created_at: string | null
}
