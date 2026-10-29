export interface Allocation { receipt_source_id: number; item_id: number; quantity: number; amount: string }
export interface WorkspaceReceipt {
  id: number; external_id: string; associated: boolean; description: string; quantity: number | null
  amount: string; baseline_amount: string; other_amount: string; present: boolean; void: boolean
  tracking_numbers: string[]; other_commitments: {item_id: number; quantity: number}[]
}
export interface WorkspaceItem {
  id: number; description: string; quantity: number; order_id: number; order_number: string | null
  store: string; receipt_id: string | null; tracking_numbers: string[]; eligible: boolean; status: string
  payable_amount: string; other_amount: string; correction_amount: string
  other_commitments: {receipt_source_id: number; quantity: number}[]
}
export interface Workspace {
  payment: {
    id: number; payment_id: string | null; buying_group_id: number; group_name: string
    allocation_version: number; allocation_manual: boolean; payment_requested_at: string; payment_sent_at: string | null
    status: string | null; total_amount: string; sync_issues: string[]; receipt_conflict: boolean
    monarch_receipt: {account_name: string; posted_date: string} | null
    allocation_breakdown: {historical_amount: string; adjustment_amount: string; correction_amount: string}
    allocation_details: {note?: string}
    corrections: {item_id: number; amount: string; explanation: string}[]
  }
  unassigned_links?: {item_id: number; quantity: number; amount: string}[]
  receipts: WorkspaceReceipt[]; items: WorkspaceItem[]; allocations: Allocation[]
}
// Monetary arithmetic uses cents, never floating point dollars.
export function cents(value: string): number {
  if (!/^-?\d+(\.\d{1,2})?$/.test(value)) return NaN
  const negative = value.startsWith('-')
  const [whole, fraction = ''] = value.replace('-', '').split('.')
  return (Number(whole) * 100 + Number(fraction.padEnd(2, '0'))) * (negative ? -1 : 1)
}
export const decimal = (value: number) => `${value < 0 ? '-' : ''}${Math.floor(Math.abs(value) / 100)}.${String(Math.abs(value) % 100).padStart(2, '0')}`
export const money = (value: number) => Number.isFinite(value) ? (value / 100).toLocaleString('en-US', {style: 'currency', currency: 'USD'}) : '—'
export const sum = (rows: Allocation[]) => rows.reduce((n, a) => n + cents(a.amount), 0)
export function remaining(item: WorkspaceItem, rows: Allocation[]) {
  return cents(item.payable_amount) - cents(item.other_amount) - cents(item.correction_amount) - sum(rows.filter(a => a.item_id === item.id))
}
export function availableQuantity(item: WorkspaceItem, receiptId: number, rows: Allocation[]) {
  const commitments = new Map(item.other_commitments.map(c => [c.receipt_source_id, c.quantity]))
  rows.filter(a => a.item_id === item.id).forEach(a => commitments.set(a.receipt_source_id, a.quantity))
  const fixed = item.other_commitments.find(c => c.receipt_source_id === receiptId)
  return fixed?.quantity ?? item.quantity - [...commitments].reduce((n, [id, qty]) => n + (id === receiptId ? 0 : qty), 0)
}
export function validate(workspace: Workspace, rows: Allocation[], historical: string, adjustment: string): string | null {
  const keys = new Set<string>()
  for (const a of rows) {
    const key = `${a.receipt_source_id}:${a.item_id}`
    if (keys.has(key)) return 'This item is already linked to the selected receipt. Edit its existing link.'
    keys.add(key)
    const receipt = workspace.receipts.find(r => r.id === a.receipt_source_id)
    const item = workspace.items.find(i => i.id === a.item_id)
    if (!receipt?.present || receipt.void) return 'Unlink missing or void receipts before saving.'
    if (!item?.eligible) return 'Only received items can be linked.'
    if (!Number.isInteger(a.quantity) || a.quantity < 1 || a.quantity > item.quantity || !Number.isSafeInteger(cents(a.amount)) || cents(a.amount) <= 0) return 'Enter a positive quantity and amount with at most two decimal places.'
    const prior = receipt.other_commitments
    if (prior.length && !prior.some(c => c.item_id === a.item_id && c.quantity === a.quantity)) return 'Installments must keep the items and quantities used by previous payments.'
  }
  for (const receipt of workspace.receipts) {
    const selected = rows.filter(a => a.receipt_source_id === receipt.id)
    if (!selected.length) continue
    if (sum(selected) + cents(receipt.other_amount) > cents(receipt.baseline_amount)) return `Links exceed receipt ${receipt.external_id}’s remaining balance.`
    if (receipt.quantity != null && selected.reduce((n, a) => n + a.quantity, 0) > receipt.quantity) return `Links exceed receipt ${receipt.external_id}’s quantity.`
  }
  for (const item of workspace.items) {
    const selected = rows.filter(a => a.item_id === item.id)
    if (!selected.length) continue
    const quantities = new Map(item.other_commitments.map(c => [c.receipt_source_id, c.quantity]))
    selected.forEach(a => quantities.set(a.receipt_source_id, a.quantity))
    if ([...quantities.values()].reduce((a, b) => a + b, 0) > item.quantity) return `Quantity for ${item.description} is already allocated to another receipt.`
    if (remaining(item, rows) < 0) return `Links exceed ${item.description}’s remaining payout.`
  }
  const h = cents(historical), adj = cents(adjustment)
  if (!Number.isSafeInteger(h) || h < 0 || !Number.isSafeInteger(adj)) return 'Enter valid historical and adjustment amounts with at most two decimal places.'
  const total = sum(rows) + h + adj + cents(workspace.payment.allocation_breakdown.correction_amount)
  if (total < 0 || total > cents(workspace.payment.total_amount)) return 'Links and adjustments must fit within the payment total.'
  return null
}
