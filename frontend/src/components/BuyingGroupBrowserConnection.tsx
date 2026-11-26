import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { LiveBrowserView } from './BrowserProfileControls'
import { NoticeLabel } from './NoticeLabel'
import { BROWSER_LOGIN_REQUIRED } from '../utils/browserLogin'

interface Connection {
  profile_id: number | null
  status: string
  login_url: string
}

export default function BuyingGroupBrowserConnection({ groupId, needsLogin, onStatusChange }: {
  groupId: number
  needsLogin: boolean
  onStatusChange: () => void
}) {
  const path = `/buying-groups/${groupId}/browser-connection`
  const [connection, setConnection] = useState<Connection | null>(null)
  const [loginOpen, setLoginOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    api.get<Connection>(path)
      .then(value => { if (active) setConnection(value) })
      .catch(e => { if (active) setError(e.message) })
    return () => { active = false }
  }, [path])

  async function act(work: () => Promise<void>) {
    setBusy(true)
    setError('')
    try {
      await work()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not open the BuyingGroup.com browser.')
    } finally {
      setBusy(false)
      onStatusChange()
    }
  }

  async function openLogin() {
    const value = await api.post<Connection>(path, {})
    const id = value.profile_id
    if (id === null) throw new Error('Could not create the BuyingGroup.com browser profile.')
    if (value.status !== 'login_in_progress') {
      await api.post(`/browser-profiles/${id}/login/start`, {})
    }
    setConnection({ ...value, status: 'login_in_progress' })
    setLoginOpen(true)
  }

  async function finishLogin(action: 'done' | 'cancel') {
    const result = await api.post<{ status: string; last_error?: string }>(
      `/browser-profiles/${connection?.profile_id}/login/${action}`, {},
    )
    setLoginOpen(false)
    setConnection(current => current ? { ...current, status: result.status } : current)
    if (action === 'done' && result.status !== 'ready') {
      throw new Error(result.last_error || 'Complete sign-in in the browser and try again.')
    }
  }

  const connected = connection?.status === 'ready' && !needsLogin

  return (
    <>
      <button
        type="button"
        disabled={busy || connection?.status === 'importing'}
        onClick={() => void act(openLogin)}
        aria-description={needsLogin ? BROWSER_LOGIN_REQUIRED : undefined}
        className="rounded-md border border-brand-200 px-2 py-1 text-xs text-ink hover:bg-brand-50 disabled:opacity-50 dark:border-gray-600 dark:hover:bg-gray-700"
      >
        <NoticeLabel notice={needsLogin ? BROWSER_LOGIN_REQUIRED : undefined}>
          {connection?.status === 'login_in_progress' ? 'Resume browser sign-in'
            : connected ? 'Reconnect browser' : 'Sign in with browser'}
        </NoticeLabel>
      </button>
      {connected && <span>Browser connected</span>}
      {error && <span role="alert" className="text-red-600 dark:text-red-400">{error}</span>}
      {loginOpen && connection?.profile_id != null && (
        <LiveBrowserView
          profileId={connection.profile_id}
          suggestedUrl={connection.login_url}
          onDone={() => { if (!busy) void act(() => finishLogin('done')) }}
          onCancel={() => { if (!busy) void act(() => finishLogin('cancel')) }}
        />
      )}
    </>
  )
}
