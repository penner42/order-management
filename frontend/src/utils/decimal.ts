/** Parse API decimal strings and numeric payment amounts for display totals. */
export function parseDecimal(value: string | number | null | undefined): number {
  if (value == null || value === '') return 0
  const parsed = parseFloat(String(value).replace(/,/g, ''))
  return Number.isFinite(parsed) ? parsed : 0
}
