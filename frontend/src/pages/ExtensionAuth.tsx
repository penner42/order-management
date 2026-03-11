import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'

interface MeResponse {
  id: number
  username: string
  role: string
}

interface ExtensionTokenResponse {
  access_token: string
  token_type: string
}

export default function ExtensionAuth() {
  const [status, setStatus] = useState<'checking' | 'ready' | 'authorizing' | 'done' | 'error'>('checking')
  const [error, setError] = useState<string | null>(null)
  const [user, setUser] = useState<MeResponse | null>(null)
  const navigate = useNavigate()

  useEffect(() => {
    let cancelled = false
    async function load() {
      setStatus('checking')
      setError(null)
      try {
        const me = await api.get<MeResponse>('/auth/me')
        if (cancelled) return
        setUser(me)
        setStatus('ready')
      } catch {
        if (cancelled) return
        const redirect = encodeURIComponent('/extension-auth')
        navigate(`/login?redirect=${redirect}`, { replace: true })
      }
    }
    load()
    return () => {
      cancelled = true
    }
  }, [navigate])

  async function handleAuthorize() {
    setStatus('authorizing')
    setError(null)
    try {
      const res = await api.post<ExtensionTokenResponse>('/auth/extension-token', {})
      setStatus('done')
      window.location.hash = 'ext-token=' + encodeURIComponent(res.access_token)
    } catch (err) {
      setStatus('error')
      setError(err instanceof Error ? err.message : 'Failed to authorize extension')
    }
  }

  if (status === 'checking') {
    return (
      <div className="min-h-screen flex items-center justify-center bg-brand-50 dark:bg-gray-900 px-4">
        <p className="text-ink-muted dark:text-gray-400">Checking your session…</p>
      </div>
    )
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-brand-50 dark:bg-gray-900 px-4">
      <div className="w-full max-w-md">
        <div className="bg-white dark:bg-gray-800 border border-brand-200 dark:border-gray-700 rounded-xl shadow-lg p-8 space-y-4">
          <h1 className="text-xl font-semibold text-brand-800 dark:text-gray-100 text-center">
            Connect browser extension
          </h1>
          {user && (
            <p className="text-sm text-ink-muted dark:text-gray-400 text-center">
              You are signed in as <span className="font-medium text-ink dark:text-gray-100">{user.username}</span>.
              Authorize the Order Management browser extension to act on your behalf.
            </p>
          )}
          {status === 'done' ? (
            <p className="text-sm text-ink-muted dark:text-gray-300 text-center">
              The extension has been authorized. This window will close automatically.
            </p>
          ) : (
            <>
              <button
                type="button"
                onClick={handleAuthorize}
                disabled={status === 'authorizing'}
                className="w-full py-2 px-4 bg-brand-600 hover:bg-brand-700 disabled:opacity-60 text-white font-medium rounded-lg transition"
              >
                {status === 'authorizing' ? 'Authorizing…' : 'Authorize browser extension'}
              </button>
              {error && (
                <p className="text-sm text-red-600 dark:text-red-400 text-center" role="alert">
                  {error}
                </p>
              )}
              <p className="text-xs text-ink-muted dark:text-gray-500 text-center">
                This will create a separate access token for the browser extension. You can revoke access by changing
                your password or resetting tokens server-side.
              </p>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
