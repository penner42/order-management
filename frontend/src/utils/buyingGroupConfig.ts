import type { BuyingGroupApiFramework } from '../api/types'

export const API_FRAMEWORK_OPTIONS: { value: '' | BuyingGroupApiFramework; label: string }[] = [
  { value: '', label: 'None' },
  { value: 'parsefile', label: 'Parsefile' },
  { value: 'usabg', label: 'USABG' },
  { value: 'gobuyme', label: 'GoBuyMe' },
  { value: 'buyinggroup', label: 'BuyingGroup.com' },
  { value: 'dealandrunner', label: 'Deal & Runner' },
  { value: 'bfmr', label: 'BFMR' },
]

export const PARSEFILE_DEFAULT_API_URL = '/p/it@api@order-management'
export const USABG_DEFAULT_BASE_URL = 'https://api.usabuying.group/buyers'
export const GOBUYME_SIGN_IN_URL = 'https://gobuyme.com/auth'
export const GOBUYME_API_KEYS_URL = 'https://gobuyme.com/app/profile?step=api-keys'
export const BUYINGGROUP_DEFAULT_BASE_URL = 'https://api.prod.buyinggroup.com'
export const BUYINGGROUP_DEFAULT_ADD_TRACKINGS_PATH = '/v1/order/add_trackings'
export const DEALANDRUNNER_DEFAULT_BASE_URL = 'https://api.dealandrunner.com'
export const BFMR_DEFAULT_BASE_URL = 'https://api.bfmr.com'
export const DEFAULT_INTERVAL_MINUTES = 360

export function usesUsernamePassword(framework: string | null | undefined): boolean {
  return (
    framework === 'usabg' ||
    framework === 'gobuyme' ||
    framework === 'buyinggroup' ||
    framework === 'dealandrunner' ||
    framework === 'bfmr'
  )
}

export function supportsTrackingSubmit(framework: string | null | undefined): boolean {
  return (
    framework === 'parsefile' ||
    framework === 'usabg' ||
    framework === 'gobuyme' ||
    framework === 'buyinggroup' ||
    framework === 'dealandrunner' ||
    framework === 'bfmr'
  )
}

export function supportsCheckReceiving(framework: string | null | undefined): boolean {
  return (
    framework === 'parsefile' ||
    framework === 'buyinggroup' ||
    framework === 'bfmr' ||
    framework === 'usabg' ||
    framework === 'dealandrunner'
  )
}

export function formatLastRun(iso: string | null | undefined): string {
  if (!iso) return 'never'
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return 'never'
  }
}

export function frameworkLabel(framework: string | null | undefined): string | null {
  if (!framework) return null
  const opt = API_FRAMEWORK_OPTIONS.find((o) => o.value === framework)
  return opt?.label ?? framework
}
