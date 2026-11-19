import { useState } from 'react'

export type GoBuyMeReservationChoice = {
  code: 'gobuyme_reservation_choice'
  message: string
  tracking: string
  units: number
  choices: {
    id: string
    title: string
    deal_number: string | number | null
    upc: string | null
    remaining_units: number
    exact_match: boolean
  }[]
}

export type GoBuyMeTrackingSelection = {
  commitment_id: string
  tracking: string
  units: number
}

export function isGoBuyMeReservationChoice(detail: unknown): detail is GoBuyMeReservationChoice {
  return Boolean(detail && typeof detail === 'object' && 'code' in detail &&
    detail.code === 'gobuyme_reservation_choice' && 'choices' in detail && Array.isArray(detail.choices))
}

export function GoBuyMeReservationDialog({ review, busy, onSubmit, onClose }: {
  review: GoBuyMeReservationChoice
  busy: boolean
  onSubmit: (selection: GoBuyMeTrackingSelection) => void
  onClose: () => void
}) {
  const [selected, setSelected] = useState('')
  const choices = [...review.choices].sort((a, b) => Number(b.exact_match) - Number(a.exact_match))
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <form
        role="dialog"
        aria-modal="true"
        aria-labelledby="gobuyme-reservation-title"
        className="w-full max-w-lg rounded-xl border border-brand-200 bg-white p-6 shadow-xl dark:bg-gray-800"
        onSubmit={(e) => {
          e.preventDefault()
          if (selected && !busy) onSubmit({ commitment_id: selected, tracking: review.tracking, units: review.units })
        }}
        onKeyDown={(e) => { if (e.key === 'Escape' && !busy) onClose() }}
      >
        <h2 id="gobuyme-reservation-title" className="text-lg font-semibold text-ink">Choose a GoBuyMe reservation</h2>
        <p className="mt-2 text-sm text-ink-muted">{review.message}</p>
        <p className="mt-2 text-sm text-ink">{review.tracking} · {review.units} {review.units === 1 ? 'unit' : 'units'}</p>
        <label className="mt-4 block text-sm text-ink">
          Tracker reservation
          <select
            autoFocus
            required
            disabled={busy}
            value={selected}
            onChange={(e) => setSelected(e.target.value)}
            className="mt-1 w-full rounded-lg border border-brand-300 bg-white px-2 py-2 dark:bg-gray-700"
          >
            <option value="">Choose a reservation</option>
            {choices.map((choice) => (
              <option key={choice.id} value={choice.id}>
                {[
                  choice.title, choice.deal_number && `Deal ${choice.deal_number}`,
                  choice.upc && `UPC ${choice.upc}`, `${choice.remaining_units} unlinked`,
                  choice.exact_match && 'Product match',
                ].filter(Boolean).join(' · ')}
              </option>
            ))}
          </select>
        </label>
        <div className="mt-6 flex justify-end gap-2">
          <button type="button" disabled={busy} onClick={onClose} className="rounded-lg border border-brand-300 px-3 py-1.5 text-sm">
            Cancel
          </button>
          <button type="submit" disabled={busy || !selected} className="rounded-lg bg-brand-600 px-3 py-1.5 text-sm text-white disabled:opacity-50">
            {busy ? 'Submitting…' : 'Submit tracking'}
          </button>
        </div>
      </form>
    </div>
  )
}
