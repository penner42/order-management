import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { BuyingGroup } from '../api/types'

interface AuditItem {
  item_id: number
  order_id: number
  store_order_number: string | null
  description: string | null
  quantity: number
  received_quantity: number
  status: string
  receipt_id: string | null
  receipt_tracking_number: string | null
  tracking_numbers: string[]
  scanned_at: string | null
  reward: string | null
  payment_linked: boolean
}
interface Finding {
  receipt_id: string | null
  status: 'excess' | 'stale_details' | 'unverified' | 'within_quantity'
  bg_quantity: number | null
  local_quantity_min: number
  local_quantity_max: number
  excess_quantity: number | null
  notes: string[]
  bg_lines: {
    description?: string | null
    tracking_number: string | null
    alternate_tracking: string | null
    quantity: number | null
    void: boolean
    bg_order_id: string | null
    received_at: string | null
    total: string | null
    note: string | null
  }[]
  items: AuditItem[]
}
interface AuditReport {
  checked_at: string
  groups: {
    group_id: number
    group_name: string
    error: string | null
    local_item_count: number
    receipt_row_count: number
    findings: Finding[]
  }[]
}

const labels = { excess: 'More units marked received than BG', stale_details: 'Receipt details on unreceived items', unverified: 'Cannot verify', within_quantity: 'Within BG quantity' }

export default function ReceivingAudit() {
  const [groups, setGroups] = useState<BuyingGroup[]>([])
  const [groupId, setGroupId] = useState('')
  const [report, setReport] = useState<AuditReport | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [showAll, setShowAll] = useState(false)

  useEffect(() => {
    api.get<BuyingGroup[]>('/buying-groups')
      .then(data => setGroups(data.filter(group => group.api_framework?.trim().toLowerCase() === 'buyinggroup')))
      .catch(e => setError(e instanceof Error ? e.message : 'Could not load buying groups'))
  }, [])

  async function runReport() {
    setLoading(true)
    setError('')
    setReport(null)
    try {
      setReport(await api.get<AuditReport>(`/buying-groups/receiving-audit${groupId ? `?group_id=${groupId}` : ''}`))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not run report')
    } finally {
      setLoading(false)
    }
  }

  const findings = report?.groups.flatMap(group => group.findings) ?? []
  return (
    <div className="space-y-5 text-ink dark:text-gray-100">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Receiving audit</h1>
          <p className="text-sm text-ink-muted dark:text-gray-400 mt-1">
            Compare local received quantities with current BuyingGroup.com receipts. This report does not change orders or BG data.
          </p>
        </div>
        <Link to="/receiving-review" className="text-sm text-brand-600 dark:text-brand-400 underline">Back to Receiving</Link>
      </div>
      <p className="text-sm text-ink-muted dark:text-gray-400">
        Scanned items and receipt-linked return/lost items count as received. Submitted and Shipped items do not count, even if old receipt details remain.
        Totals within BG quantity do not verify product allocation or which physical item arrived.
        Lines linked to multiple receipts appear under each receipt with a quantity range; do not add those ranges together.
      </p>
      <div className="flex flex-wrap items-end gap-3">
        <div className="text-sm"><label htmlFor="receiving-audit-group">Buying group</label>
          <select id="receiving-audit-group" value={groupId} disabled={loading} onChange={e => setGroupId(e.target.value)}
            className="block mt-1 rounded border border-brand-200 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2">
            <option value="">All BuyingGroup.com groups</option>
            {groups.map(group => <option key={group.id} value={group.id}>{group.name}</option>)}
          </select>
        </div>
        <button type="button" onClick={runReport} disabled={loading || groups.length === 0}
          className="rounded bg-brand-600 text-white px-4 py-2 text-sm disabled:opacity-50">
          {loading ? 'Checking BG receipt history…' : 'Run report'}
        </button>
        <label className="flex items-center gap-2 text-sm py-2">
          <input type="checkbox" checked={showAll} onChange={e => setShowAll(e.target.checked)} />
          Include receipts within BG quantity
        </label>
      </div>
      {error && <p role="alert" className="text-red-600 dark:text-red-400">{error}</p>}
      {!report && !loading && !error && <p className="text-sm text-ink-muted dark:text-gray-400">
        {groups.length ? 'Run the report to check existing received items, including receipt-linked items whose status has since changed.' : 'No BuyingGroup.com groups are available.'}
      </p>}
      {report && <>
        <div className="rounded-lg border border-brand-200 dark:border-gray-700 p-3 text-sm" role="status">
          Checked {new Date(report.checked_at).toLocaleString()} · {findings.filter(f => f.status === 'excess').length} receipts with excess quantity
          {' · '}{findings.filter(f => f.status === 'unverified').length} comparisons cannot be verified
          {' · '}{findings.filter(f => f.status === 'stale_details').length} receipts with details on unreceived items
          {report.groups.some(group => group.error) && <strong className="block text-red-600 dark:text-red-400">Report incomplete: some groups could not be checked.</strong>}
        </div>
        {report.groups.map(group => {
          const visible = group.findings.filter(f => showAll || f.status !== 'within_quantity')
          return <section key={group.group_id} className="rounded-xl border border-brand-200 dark:border-gray-700 p-4 space-y-3">
            <h2 className="font-semibold">{group.group_name}</h2>
            {group.error ? <p role="alert" className="text-red-600 dark:text-red-400">{group.error}</p> : <>
              <p className="text-sm text-ink-muted dark:text-gray-400">{group.local_item_count} local lines checked · {group.receipt_row_count} BG receipt rows fetched</p>
              {!visible.length && <p className="text-sm">{!group.local_item_count ? 'No local received items to check.' : 'No excess quantities or unverifiable receipt links found.'}</p>}
              {visible.map((finding, index) => <div key={`${finding.receipt_id}:${index}`} className="border-t border-brand-100 dark:border-gray-700 pt-3 space-y-2">
                <div className="flex flex-wrap gap-x-5 gap-y-1 text-sm">
                  <strong>Receipt {finding.receipt_id || 'not recorded'}</strong>
                  <span className={finding.status === 'excess' ? 'text-red-700 dark:text-red-400 font-semibold' : finding.status === 'unverified' ? 'text-amber-700 dark:text-amber-400' : ''}>{labels[finding.status]}</span>
                  <span>BG quantity: {finding.bg_quantity ?? 'Unknown'}</span>
                  <span>Marked received locally: {finding.local_quantity_min === finding.local_quantity_max ? finding.local_quantity_min : `${finding.local_quantity_min}–${finding.local_quantity_max}`}</span>
                  {!!finding.excess_quantity && <strong>At least {finding.excess_quantity} excess</strong>}
                </div>
                {finding.notes.map(note => <p key={note} className="text-sm text-ink-muted dark:text-gray-400">{note}</p>)}
                <div className="overflow-x-auto">
                  <table className="w-full text-sm text-left">
                    <thead><tr>{['Order / line', 'Description', 'Qty', 'Counted received', 'Status', 'Shipment tracking', 'Receipt tracking', 'Scanned', 'Reward', 'Payment linked'].map(label => <th key={label} className="px-2 py-2 whitespace-nowrap">{label}</th>)}</tr></thead>
                    <tbody>{finding.items.map(item => <tr key={item.item_id} className="border-t border-brand-100 dark:border-gray-700">
                      <td className="px-2 py-2 whitespace-nowrap"><Link className="text-brand-600 dark:text-brand-400 underline" to={`/?order_id=${item.order_id}`}>{item.store_order_number || `Order #${item.order_id}`}</Link><div className="text-xs">Line #{item.item_id} · Receipt {item.receipt_id || '—'}</div></td>
                      <td className="px-2 py-2 min-w-48">{item.description || '—'}</td>
                      <td className="px-2 py-2">{item.quantity}</td>
                      <td className="px-2 py-2">{item.received_quantity}</td>
                      <td className="px-2 py-2">{item.status}</td>
                      <td className="px-2 py-2">{item.tracking_numbers.join(', ') || '—'}</td>
                      <td className="px-2 py-2">{item.receipt_tracking_number || '—'}</td>
                      <td className="px-2 py-2 whitespace-nowrap">{item.scanned_at ? item.scanned_at.slice(0, 10) : '—'}</td>
                      <td className="px-2 py-2">{item.reward == null ? '—' : `$${item.reward}`}</td>
                      <td className="px-2 py-2">{item.payment_linked ? 'Yes' : 'No'}</td>
                    </tr>)}</tbody>
                  </table>
                </div>
                {!!finding.bg_lines.length && <details className="text-sm">
                  <summary className="cursor-pointer">BG receipt evidence</summary>
                  <ul className="list-disc pl-5 mt-2 space-y-1">{finding.bg_lines.map((line, i) => <li key={i}>
                    {line.description && `${line.description} · `}
                    Quantity {line.quantity ?? 'unknown'}{line.void ? ' (voided)' : ''} · Tracking {line.tracking_number || '—'}
                    {line.alternate_tracking && ` · Alternate ${line.alternate_tracking}`}
                    {line.bg_order_id && ` · BG order ${line.bg_order_id}`}
                    {line.total != null && ` · Total $${line.total}`}
                    {line.received_at && ` · Received ${line.received_at}`}
                    {line.note && ` · ${line.note}`}
                  </li>)}</ul>
                </details>}
              </div>)}
            </>}
          </section>
        })}
      </>}
    </div>
  )
}
