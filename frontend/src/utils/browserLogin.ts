export const BROWSER_LOGIN_REQUIRED = 'Browser login required'

export function needsBrowserLogin(profile: { status: string }) {
  return profile.status === 'login_required' || profile.status === 'logged_out'
}
