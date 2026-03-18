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

export interface BuyingGroup {
  id: number
  user_id: number | null
  name: string
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
}

export type OrderStatus = 'active' | 'imported'

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
  notes: string | null
  created_at: string
  updated_at?: string
  store?: Store
  store_account?: StoreAccount
  buying_group?: BuyingGroup
  items: Item[]
  order_payments: OrderPaymentMethod[]
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
