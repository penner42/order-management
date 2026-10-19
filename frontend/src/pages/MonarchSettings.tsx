import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import { MONARCH, type MonarchSettings as Settings } from '../api/monarch'
import type { BrowserJob, BuyingGroup } from '../api/types'
import { LiveBrowserView } from '../components/BrowserProfileControls'

const inputClass = 'rounded border border-brand-200 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2'
const buttonClass = 'rounded bg-brand-600 px-3 py-2 text-white disabled:opacity-50'

export default function MonarchSettings() {
  const [settings, setSettings] = useState<Settings | null>(null)
  const [groups, setGroups] = useState<BuyingGroup[]>([])
  const [keywords, setKeywords] = useState<Record<string, string>>({})
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [login, setLogin] = useState(false)
  const [job, setJob] = useState<BrowserJob | null>(null)
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')

  function apply(value: Settings) {
    setSettings(value)
    setKeywords(Object.fromEntries(Object.entries(value.sender_rules).map(([key, words]) => [key, words.join('\n')])))
  }
  async function refresh() { apply(await api.get<Settings>(MONARCH)) }
  useEffect(() => {
    Promise.all([api.get<Settings>(MONARCH), api.get<BuyingGroup[]>('/buying-groups')])
      .then(([value, buyingGroups]) => { apply(value); setGroups(buyingGroups) })
      .catch(e => setError(String(e.message)))
  }, [])
  useEffect(() => {
    if (!job || !['queued', 'running'].includes(job.status)) return
    let active = true
    const timer = window.setInterval(() => {
      api.get<BrowserJob>(`${MONARCH}/jobs/${job.id}`).then(async value => {
        if (!active) return
        setJob(value)
        if (!['queued', 'running'].includes(value.status)) {
          if (value.error) setError(value.error)
          else setMessage(value.message ?? 'Check completed')
          await refresh()
        }
      }).catch(e => { if (active) { setError(e.message); setJob(null) } })
    }, 1500)
    return () => { active = false; window.clearInterval(timer) }
  }, [job?.id, job?.status])

  async function action(work: () => Promise<void>) {
    setBusy(true); setError(''); setMessage('')
    try { await work() } catch (e) { setError(e instanceof Error ? e.message : 'Request failed') }
    finally { setBusy(false) }
  }
  const profile = settings?.profile
  const checking = profile?.status === 'importing' || !!job && ['queued', 'running'].includes(job.status)
  const disabled = busy || checking || login
  async function save() {
    if (!settings) return
    const sender_rules = Object.fromEntries(groups.map(group => [String(group.id), (keywords[group.id] ?? '').split('\n').map(s => s.trim()).filter(Boolean)]))
    apply(await api.patch<Settings>(MONARCH, { selected_account_ids: settings.selected_account_ids, sender_rules, daily_enabled: settings.daily_enabled }))
    setMessage('Settings saved')
  }

  return <section className="space-y-5 text-ink dark:text-gray-100">
    <h2 className="text-xl font-semibold">Monarch</h2>
    <p>Connect Monarch to review bank deposits against buying-group payments. Every receipt requires your confirmation.</p>
    {error && <p role="alert" className="text-red-700 dark:text-red-300">{error}</p>}
    {message && <p role="status">{message}</p>}
    {!settings ? <p>Loading Monarch settings…</p> : <>
      {!profile ? <button className={buttonClass} disabled={busy} onClick={() => void action(async () => apply(await api.post<Settings>(`${MONARCH}/connect`, {})))}>Set up Monarch</button> : <>
        <p>Connection: <strong>{profile.status.replace(/_/g, ' ')}</strong></p>
        {(settings.last_error || profile.last_error) && <p role="alert">{settings.last_error || profile.last_error}</p>}
        <p>Last successful check: {settings.last_check_at ? new Date(settings.last_check_at).toLocaleString() : 'Never'}</p>
        <div className="flex flex-wrap gap-3">
          <button className={buttonClass} disabled={disabled || profile.status === 'login_in_progress'} onClick={() => void action(async () => {
            await api.post(`/browser-profiles/${profile.id}/login/start`, {}); setLogin(true); await refresh()
          })}>Log in to Monarch</button>
          {profile.status === 'login_in_progress' && !login && <button className={buttonClass} onClick={() => setLogin(true)}>Resume login</button>}
          <Link className="underline self-center" to="/payments/monarch">Review deposits</Link>
        </div>
        {login && <LiveBrowserView profileId={profile.id} suggestedUrl="https://app.monarch.com" onDone={() => void action(async () => {
          const result = await api.post<{ status: string }>(`/browser-profiles/${profile.id}/login/done`, {})
          setLogin(false); await refresh()
          if (result.status !== 'ready') throw new Error('Login was not verified. Start login and complete sign-in again.')
          setMessage('Connected. Use Check now to discover accounts.')
        })} onCancel={() => void action(async () => { await api.post(`/browser-profiles/${profile.id}/login/cancel`, {}); setLogin(false); await refresh() })} />}
        <fieldset disabled={disabled} className="space-y-3">
          <legend className="font-semibold">Receiving bank accounts</legend>
          <details open>
            <summary className="cursor-pointer font-medium">Cash and investment accounts ({settings.selected_account_ids.length} selected of {settings.accounts.length})</summary>
            <div className="mt-3 space-y-2">
              {!settings.accounts.length && <p>Use Check now to discover cash and investment accounts, then select accounts and save.</p>}
              {settings.accounts.map(account => <label key={account.id} className="flex gap-2 items-center">
                <input type="checkbox" checked={settings.selected_account_ids.includes(account.id)} onChange={e => setSettings({ ...settings, selected_account_ids: e.target.checked ? [...settings.selected_account_ids, account.id] : settings.selected_account_ids.filter(id => id !== account.id) })} />{account.name}
              </label>)}
            </div>
          </details>
          <h3 className="font-semibold">Sender keywords</h3>
          <p>One keyword or phrase per line. Matched against the merchant and original bank description.</p>
          {groups.map(group => <label key={group.id} className="block">{group.name}
            <textarea className={`${inputClass} block w-full mt-1`} rows={2} value={keywords[group.id] ?? ''} onChange={e => setKeywords({ ...keywords, [group.id]: e.target.value })} />
          </label>)}
          <label className="flex gap-2 items-center"><input type="checkbox" checked={settings.daily_enabled} onChange={e => setSettings({ ...settings, daily_enabled: e.target.checked })} />Check daily at 8:00 a.m. America/Los_Angeles</label>
          {settings.next_check_at && <p>Next scheduled check: {new Date(settings.next_check_at).toLocaleString()}</p>}
          <button className={buttonClass} onClick={() => void action(save)}>Save Monarch settings</button>
        </fieldset>
        <fieldset disabled={disabled || !['ready', 'error'].includes(profile.status)} className="space-y-3 border-t pt-4">
          <legend className="font-semibold">Check deposits</legend>
          <p>Checks use your saved account selections. Leave dates blank for the last 90 days.</p>
          <div className="flex flex-wrap gap-3">
            <label>Start date <input className={inputClass} type="date" value={start} onChange={e => setStart(e.target.value)} /></label>
            <label>End date <input className={inputClass} type="date" value={end} onChange={e => setEnd(e.target.value)} /></label>
            <button className={buttonClass} disabled={!!start !== !!end || !!start && start > end} onClick={() => void action(async () => {
              setJob(await api.post<BrowserJob>(`${MONARCH}/checks`, start && end ? { start_date: start, end_date: end } : {})); await refresh()
            })}>Check now</button>
          </div>
        </fieldset>
        {checking && <p role="status">{job?.message ?? 'Checking Monarch…'}</p>}
      </>}
    </>}
  </section>
}
