import type { BrowserProfile } from './types'

export const MONARCH = '/integrations/monarch'
export interface MonarchSettings {
  profile: BrowserProfile | null
  accounts: { id: string; name: string; type: 'depository' | 'brokerage' }[]
  selected_account_ids: string[]
  sender_rules: Record<string, string[]>
  daily_enabled: boolean
  next_check_at: string | null
  last_check_at: string | null
  last_error: string | null
}
export interface MonarchPaymentChoice {
  id: number
  reference: string | null
  buying_group_id: number
  buying_group_name: string
  amount: string
  fingerprint: string
  date: string
  received_at?: string | null
}
export interface MonarchDeposit {
  id: number
  transaction_id: string
  account_name: string
  posted_date: string
  amount: string
  merchant: string
  description: string
  revision: number
  pending: boolean
  dismissed: boolean
  eligible: boolean
  confirmation_id: number | null
  confirmation: { deposit: { amount: string; posted_date: string }; payments: MonarchPaymentChoice[] } | null
  evidence_changed: boolean
  suggestions: { payment_ids: number[]; reason: string }[]
  search_truncated: boolean
}
export interface MonarchReviewData {
  deposits: MonarchDeposit[]
  payments: MonarchPaymentChoice[]
}

// Integer cents keep the UI's equality gate consistent with backend Decimal amounts.
export function cents(amount: string): number {
  const match = /^(-?)(\d+)(?:\.(\d{1,2}))?$/.exec(amount)
  if (!match) return NaN
  return (match[1] ? -1 : 1) * (Number(match[2]) * 100 + Number((match[3] ?? '').padEnd(2, '0')))
}
export function dollars(value: number): string {
  return (value / 100).toLocaleString('en-US', { style: 'currency', currency: 'USD' })
}
