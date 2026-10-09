const API = '/api'
const AUTH_TOKEN_KEY = 'auth_token'

export function getStoredToken(): string | null {
  return localStorage.getItem(AUTH_TOKEN_KEY)
}

export function setStoredToken(token: string | null): void {
  if (token) localStorage.setItem(AUTH_TOKEN_KEY, token)
  else localStorage.removeItem(AUTH_TOKEN_KEY)
}

function formatApiErrorDetail(err: unknown, fallback: string): string {
  if (typeof err === 'string' && err.trim()) return err.trim()
  if (!err || typeof err !== 'object') return fallback
  const detail = (err as { detail?: unknown }).detail
  if (typeof detail === 'string' && detail.trim()) return detail.trim()
  if (Array.isArray(detail)) {
    const parts = detail
      .map((entry) => {
        if (typeof entry === 'string') return entry
        if (entry && typeof entry === 'object' && 'msg' in entry) {
          return String((entry as { msg: unknown }).msg ?? '')
        }
        try {
          return JSON.stringify(entry)
        } catch {
          return ''
        }
      })
      .map((s) => s.trim())
      .filter(Boolean)
    if (parts.length > 0) return parts.join('; ')
  }
  if (detail != null && detail !== '') {
    try {
      return JSON.stringify(detail)
    } catch {
      // fall through
    }
  }
  return fallback
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const token = getStoredToken()
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options?.headers as Record<string, string>),
  }
  if (token) headers['Authorization'] = `Bearer ${token}`

  const res = await fetch(`${API}${path}`, { ...options, headers })
  if (res.status === 401) {
    setStoredToken(null)
    window.dispatchEvent(new CustomEvent('auth:401'))
    const err = await res.json().catch(() => ({ detail: 'Unauthorized' }))
    throw new Error(formatApiErrorDetail(err, 'Unauthorized'))
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(formatApiErrorDetail(err, `Request failed (${res.status})`))
  }
  if (res.status === 204) return undefined as T
  return res.json()
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body: unknown) =>
    request<T>(path, { method: 'POST', body: JSON.stringify(body) }),
  patch: <T>(path: string, body: unknown) =>
    request<T>(path, { method: 'PATCH', body: JSON.stringify(body) }),
  delete: (path: string) => request<void>(path, { method: 'DELETE' }),
}
