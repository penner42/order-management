export const BUYINGGROUP_DEFAULT_BASE_URL = 'https://api.prod.buyinggroup.com'

export function supportsBuyingGroupBrowser(group: { api_framework?: string | null; base_url?: string | null }): boolean {
  const base = (group.base_url?.trim() || BUYINGGROUP_DEFAULT_BASE_URL).replace(/\/+$/, '').replace(/\/v1$/, '')
  return group.api_framework === 'buyinggroup' && base === BUYINGGROUP_DEFAULT_BASE_URL
}
