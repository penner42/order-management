import { Link } from 'react-router-dom'
import type { DuplicateAuditFinding, Order } from '../api/types'

function timestamp(value: string | null | undefined) {
  return value ? new Date(value).toLocaleString() : '—'
}

export function DuplicateAuditDetails({ order, findings, onMarkNotDuplicate, saving }: {
  order: Order
  findings: DuplicateAuditFinding[]
  onMarkNotDuplicate: (itemIds: number[]) => void
  saving: boolean
}) {
  const itemIds = new Set(findings.flatMap(finding => finding.item_ids))
  return (
    <div className="rounded-lg border border-amber-300 dark:border-amber-700 bg-amber-50 dark:bg-gray-800 p-3 text-sm text-ink dark:text-gray-200 space-y-2">
      <div className="flex flex-wrap justify-between gap-2">
        <strong className="dark:text-amber-200">{findings.some(f => f.confidence === 'likely') ? 'Likely duplicate' : 'Possible duplicate'}</strong>
        <Link to={`/?order_id=${order.id}`} className="text-brand-700 dark:text-amber-300 underline hover:text-brand-900 dark:hover:text-amber-200">
          Review in Orders
        </Link>
      </div>
      <ul className="list-disc pl-5 space-y-1">
        {findings.map((finding, index) => (
          <li key={index}>
            <span className="font-medium">Lines {finding.item_ids.map(id => `#${id}`).join(', ')}: </span>
            {finding.reasons.join(' · ')}
            {finding.tracking_numbers.length > 0 && <span> · Tracking: {finding.tracking_numbers.join(', ')}</span>}
            <button type="button" disabled={saving}
              onClick={() => onMarkNotDuplicate(finding.item_ids)}
              aria-label={`Mark lines ${finding.item_ids.map(id => `#${id}`).join(', ')} as not duplicates`}
              className="ml-2 px-2 py-1 rounded border border-amber-400 dark:border-gray-500 hover:bg-amber-100 dark:hover:bg-gray-700 disabled:opacity-50">
              Not duplicates
            </button>
          </li>
        ))}
      </ul>
      <details>
        <summary className="cursor-pointer dark:text-gray-200">Line IDs, receipts, and timestamps</summary>
        <div className="overflow-x-auto mt-2">
          <table className="w-full text-xs text-left whitespace-nowrap text-ink dark:text-gray-200">
            <thead className="dark:bg-gray-900/50 dark:text-gray-300"><tr>{['Line', 'Description', 'Receipt', 'Created', 'Updated', 'Scanned', 'Review'].map(label => <th key={label} className="px-2 py-1">{label}</th>)}</tr></thead>
            <tbody className="divide-y divide-amber-200 dark:divide-gray-700">{order.items.filter(item => itemIds.has(item.id)).map(item => (
              <tr key={item.id}>
                <td className="px-2 py-1">#{item.id}</td>
                <td className="px-2 py-1">{item.description || '—'}</td>
                <td className="px-2 py-1">{item.receipt_id || '—'}</td>
                <td className="px-2 py-1">{timestamp(item.created_at)}</td>
                <td className="px-2 py-1">{timestamp(item.updated_at)}</td>
                <td className="px-2 py-1">{timestamp(item.scanned_at)}</td>
                <td className="px-2 py-1">
                  <button type="button" disabled={saving} onClick={() => onMarkNotDuplicate([item.id])}
                    aria-label={`Mark line #${item.id} as not a duplicate`}
                    className="underline text-brand-700 dark:text-amber-300 disabled:opacity-50">
                    Not a duplicate
                  </button>
                </td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      </details>
    </div>
  )
}
