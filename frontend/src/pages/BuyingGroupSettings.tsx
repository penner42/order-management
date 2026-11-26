import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../api/client'
import { useGroupSubmissionsEnabled } from '../utils/capabilities'
import type { BuyingGroup, BuyingGroupApiFramework } from '../api/types'
import BuyingGroupAddresses from '../components/BuyingGroupAddresses'
import {
  API_FRAMEWORK_OPTIONS, BFMR_DEFAULT_BASE_URL, BUYINGGROUP_DEFAULT_ADD_TRACKINGS_PATH, BUYINGGROUP_DEFAULT_BASE_URL,
  DEALANDRUNNER_DEFAULT_BASE_URL, DEFAULT_INTERVAL_MINUTES, GOBUYME_API_KEYS_URL, GOBUYME_SIGN_IN_URL,
  PARSEFILE_DEFAULT_API_URL, USABG_DEFAULT_BASE_URL, formatLastRun, frameworkLabel, supportsCheckReceiving,
  supportsTrackingSubmit, usesUsernamePassword,
} from '../utils/buyingGroupConfig'

function AliasEditor({
  group,
  onChange,
}: {
  group: BuyingGroup
  onChange: (updated: BuyingGroup) => void
}) {
  const [newAlias, setNewAlias] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const updateAliases = async (aliases: string[]) => {
    setSaving(true)
    setError('')
    try {
      const updated = await api.patch<BuyingGroup>(`/buying-groups/${group.id}`, { aliases })
      onChange(updated)
      return true
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not update import aliases.')
      return false
    } finally {
      setSaving(false)
    }
  }

  const addAlias = async (e: React.FormEvent) => {
    e.preventDefault()
    const alias = newAlias.trim()
    if (!alias) return
    const existing = new Set((group.aliases ?? []).map((a) => a.toLowerCase()))
    if (existing.has(alias.toLowerCase())) {
      setNewAlias('')
      return
    }
    if (await updateAliases([...(group.aliases ?? []), alias])) setNewAlias('')
  }

  const removeAlias = async (alias: string) => {
    await updateAliases((group.aliases ?? []).filter((a) => a !== alias))
  }

  return (
    <div className="mt-2">
      <div className="flex flex-wrap items-center gap-1.5">
        {(group.aliases ?? []).length === 0 ? (
          <span className="text-xs text-ink-muted">No aliases</span>
        ) : (
          (group.aliases ?? []).map((alias) => (
            <span
              key={alias}
              className="inline-flex items-center gap-1 rounded-full bg-brand-50 dark:bg-brand-900/30 px-2 py-0.5 text-xs text-ink"
            >
              {alias}
              <button
                type="button"
                onClick={() => removeAlias(alias)}
                disabled={saving}
                className="text-ink-muted hover:text-red-600 disabled:opacity-50"
                aria-label={`Remove alias ${alias}`}
              >
                ×
              </button>
            </span>
          ))
        )}
      </div>
      {error && <p role="alert" className="text-sm text-red-600 dark:text-red-400">{error}</p>}
      <form onSubmit={addAlias} className="mt-2 flex gap-2 max-w-md">
        <input
          type="text"
          className="rounded border border-brand-200 px-2 py-1 text-sm flex-1"
          placeholder="Add alias for import matching"
          aria-label="Alias for import matching"
          value={newAlias}
          onChange={(e) => setNewAlias(e.target.value)}
          disabled={saving}
        />
        <button
          type="submit"
          disabled={saving || !newAlias.trim()}
          className="text-sm text-brand-600 hover:underline disabled:opacity-50"
        >
          Add
        </button>
      </form>
    </div>
  )
}

type EditDraft = {
  name: string
  api_framework: '' | BuyingGroupApiFramework
  base_url: string
  api_url: string
  bearer_token: string
  api_user_id: string
  api_email: string
  api_username: string
  api_password: string
  tracking_submit_enabled: boolean
  order_number_submit_enabled: boolean
  schedule_interval_minutes: number
  received_check_enabled: boolean
  payment_sync_enabled: boolean
}

function draftFromGroup(g: BuyingGroup): EditDraft {
  return {
    name: g.name,
    api_framework: g.api_framework ?? '',
    base_url: g.base_url ?? '',
    api_url: g.api_url ?? '',
    bearer_token: g.bearer_token ?? '',
    api_user_id: g.api_user_id != null ? String(g.api_user_id) : '',
    api_email: g.api_email ?? '',
    api_username: g.api_username ?? '',
    api_password: g.api_password ?? '',
    tracking_submit_enabled: !!g.tracking_submit_enabled,
    order_number_submit_enabled: !!g.order_number_submit_enabled,
    schedule_interval_minutes: g.schedule_interval_minutes ?? DEFAULT_INTERVAL_MINUTES,
    received_check_enabled: !!g.received_check_enabled,
    payment_sync_enabled: g.payment_sync_enabled ?? true,
  }
}

const listPath = '/settings/buying-groups'

export default function BuyingGroupSettings() {
  const { groupId } = useParams()
  return <BuyingGroupDetails key={groupId} groupId={groupId} />
}

function BuyingGroupDetails({ groupId }: { groupId: string | undefined }) {
  const [group, setGroup] = useState<BuyingGroup | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!groupId || !/^[1-9][0-9]*$/.test(groupId)) {
      setError('Buying group not found')
      return
    }
    let active = true
    api.get<BuyingGroup>(`/buying-groups/${groupId}`)
      .then((result) => { if (active) setGroup(result) })
      .catch((e) => { if (active) setError(e instanceof Error ? e.message : 'Could not load buying group') })
    return () => { active = false }
  }, [groupId])

  return (
    <div className="space-y-5">
      <Link to={listPath} className="text-sm text-brand-600 dark:text-brand-400 hover:underline">Back to buying groups</Link>
      {error && <p role="alert">{error}</p>}
      {!group && !error && <p role="status">Loading buying group…</p>}
      {group && <>
        <h2 className="text-2xl font-semibold">{group.name}</h2>
        <BuyingGroupAddresses groupId={group.id} />
        <section aria-labelledby="group-aliases" className="rounded-lg border border-brand-200 dark:border-gray-700 p-4">
          <h3 id="group-aliases" className="font-semibold">Import aliases</h3>
          <p className="text-sm text-ink-muted">Match shipping names and address lines on store imports.</p>
          <AliasEditor group={group} onChange={setGroup} />
        </section>
        <GroupConfiguration group={group} />
      </>}
    </div>
  )
}

function GroupConfiguration({ group: g }: { group: BuyingGroup }) {
  const navigate = useNavigate()
  const groupSubmissionsEnabled = useGroupSubmissionsEnabled()
  const [draft, setDraft] = useState(() => draftFromGroup(g))
  const [saving, setSaving] = useState(false)
  const [submitAlert, setSubmitAlert] = useState('')
  const isGoBuyMe = draft.api_framework === 'gobuyme'
  const usesEmailLogin = draft.api_framework === 'dealandrunner' || isGoBuyMe

  const saveEdit = async () => {
    if (!draft.name.trim()) return
    if (![draft.schedule_interval_minutes]
      .every((n) => Number.isInteger(n) && n >= 1 && n <= 525600)) {
      setSubmitAlert('Schedule intervals must be whole numbers from 1 to 525600 minutes.')
      return
    }
    const userIdRaw = draft.api_user_id.trim()
    let apiUserId: number | null = null
    if (userIdRaw) {
      const n = parseInt(userIdRaw, 10)
      if (Number.isNaN(n)) return
      apiUserId = n
    }
    setSaving(true)
    try {
      await api.patch<BuyingGroup>(`/buying-groups/${g.id}`, {
        name: draft.name.trim(),
        api_framework: draft.api_framework || null,
        base_url: draft.api_framework === 'gobuyme' ? null : draft.base_url.trim() || null,
        api_url: usesUsernamePassword(draft.api_framework)
          ? draft.api_framework === 'buyinggroup'
            ? draft.api_url.trim() || null
            : null
          : draft.api_url.trim() || null,
        bearer_token: !isGoBuyMe && usesUsernamePassword(draft.api_framework)
          ? null
          : draft.bearer_token.trim() || null,
        api_user_id: draft.api_framework === 'parsefile' ? apiUserId : null,
        api_email: draft.api_framework === 'parsefile' ? draft.api_email.trim() || null : null,
        api_username: usesUsernamePassword(draft.api_framework)
          ? draft.api_username.trim() || null
          : null,
        // Parsefile uses portal password (api_password) for encrypted orders-all id lookup.
        api_password:
          usesUsernamePassword(draft.api_framework) || draft.api_framework === 'parsefile'
            ? draft.api_password.trim() || null
            : null,
        tracking_submit_enabled: supportsTrackingSubmit(draft.api_framework)
          ? draft.tracking_submit_enabled
          : false,
        order_number_submit_enabled: draft.api_framework === 'bfmr' && draft.order_number_submit_enabled,
        schedule_interval_minutes: draft.schedule_interval_minutes,
        received_check_enabled: supportsCheckReceiving(draft.api_framework) && draft.received_check_enabled,
        payment_sync_enabled: draft.api_framework !== 'gobuyme' && draft.payment_sync_enabled,
      })
      navigate(listPath)
    } catch (e) {
      console.error(e)
      setSubmitAlert(e instanceof Error ? e.message : 'Could not save buying group.')
    } finally {
      setSaving(false)
    }
  }
  return (
    <section aria-labelledby="group-configuration" className="rounded-lg border border-brand-200 dark:border-gray-700 p-4">
      <h3 id="group-configuration" className="font-semibold mb-3">Group configuration</h3>
      {submitAlert && <p role="alert" className="mb-3 text-sm text-red-600 dark:text-red-400">{submitAlert}</p>}
      <div className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <input
            type="text"
            className="rounded border border-brand-200 px-2 py-1 flex-1 max-w-sm"
            value={draft.name}
            onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
            placeholder="Name"
            aria-label="Buying group name"
            disabled={saving}
          />
          <div className="flex gap-2 shrink-0">
            <button
              type="button"
              onClick={saveEdit}
              disabled={saving || !draft.name.trim()}
              className="text-sm text-brand-600 hover:underline disabled:opacity-50"
            >
              Save
            </button>
            <button
              type="button"
              onClick={() => navigate(listPath)}
              disabled={saving}
              className="text-sm text-ink-muted hover:underline disabled:opacity-50"
            >
              Cancel
            </button>
          </div>
        </div>
        <div className="grid gap-2 max-w-lg">
          <label className="block">
            <span className="text-xs text-ink-muted">API framework</span>
            <select
              className="mt-0.5 w-full rounded border border-brand-200 px-2 py-1 text-sm"
              value={draft.api_framework}
              onChange={(e) => {
                const framework = e.target.value as EditDraft['api_framework']
                setDraft((d) => ({
                  ...d,
                  api_framework: framework,
                  ...((framework === 'gobuyme' || d.api_framework === 'gobuyme') && {
                    base_url: '', api_username: '', api_password: '', bearer_token: '',
                  }),
                }))
              }}
              disabled={saving}
            >
              {API_FRAMEWORK_OPTIONS.map((opt) => (
                <option key={opt.value || 'none'} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </label>
          {draft.api_framework === 'gobuyme' ? (
            <p className="text-xs text-ink-muted">
              Tracking submission matches the item UPC to a GoBuyMe Tracker reservation.{' '}
              <a href={GOBUYME_SIGN_IN_URL} target="_blank" rel="noreferrer" className="underline">
                Open GoBuyMe sign-in
              </a>
            </p>
          ) : (
            <label className="block">
              <span className="text-xs text-ink-muted">
                {draft.api_framework === 'usabg'
                  ? 'Base URL (optional; blank uses USABG default)'
                  : draft.api_framework === 'buyinggroup'
                    ? 'Base URL (optional; blank uses BuyingGroup.com default)'
                    : draft.api_framework === 'dealandrunner'
                      ? 'Base URL (optional; blank uses Deal & Runner default)'
                      : draft.api_framework === 'bfmr'
                        ? 'Base URL (optional; blank uses BFMR default)'
                        : 'Base URL'}
              </span>
              <input
                type="url"
                className="mt-0.5 w-full rounded border border-brand-200 px-2 py-1 text-sm"
                placeholder={
                  draft.api_framework === 'usabg'
                    ? USABG_DEFAULT_BASE_URL
                    : draft.api_framework === 'buyinggroup'
                      ? BUYINGGROUP_DEFAULT_BASE_URL
                      : draft.api_framework === 'dealandrunner'
                        ? DEALANDRUNNER_DEFAULT_BASE_URL
                        : draft.api_framework === 'bfmr'
                          ? BFMR_DEFAULT_BASE_URL
                          : 'https://www.powerbuynetwork.com'
                }
                value={draft.base_url}
                onChange={(e) => setDraft((d) => ({ ...d, base_url: e.target.value }))}
                disabled={saving}
              />
            </label>
          )}
          {draft.api_framework === 'buyinggroup' && (
            <label className="block">
              <span className="text-xs text-ink-muted">
                Add-trackings path/URL (optional; blank uses {BUYINGGROUP_DEFAULT_ADD_TRACKINGS_PATH})
              </span>
              <input
                type="text"
                className="mt-0.5 w-full rounded border border-brand-200 px-2 py-1 text-sm"
                placeholder={BUYINGGROUP_DEFAULT_ADD_TRACKINGS_PATH}
                value={draft.api_url}
                onChange={(e) => setDraft((d) => ({ ...d, api_url: e.target.value }))}
                disabled={saving}
              />
            </label>
          )}
          {!usesUsernamePassword(draft.api_framework) && (
            <label className="block">
              <span className="text-xs text-ink-muted">
                {draft.api_framework === 'parsefile'
                  ? 'API URL (optional; blank uses Parsefile default; commands like /cmd/addtracking are added automatically)'
                  : 'API URL (optional, relative to base)'}
              </span>
              <input
                type="text"
                className="mt-0.5 w-full rounded border border-brand-200 px-2 py-1 text-sm"
                placeholder={
                  draft.api_framework === 'parsefile'
                    ? PARSEFILE_DEFAULT_API_URL
                    : '/v1/tracking'
                }
                value={draft.api_url}
                onChange={(e) => setDraft((d) => ({ ...d, api_url: e.target.value }))}
                disabled={saving}
              />
            </label>
          )}
          {(isGoBuyMe || !usesUsernamePassword(draft.api_framework)) && (
            <label className="block">
              <span className="text-xs text-ink-muted">{isGoBuyMe ? 'GoBuyMe API key' : 'Bearer token'}</span>
              <input
                type="password"
                className="mt-0.5 w-full rounded border border-brand-200 px-2 py-1 text-sm"
                placeholder={isGoBuyMe ? 'gbm_bk_…' : 'Token'}
                value={draft.bearer_token}
                onChange={(e) => setDraft((d) => ({ ...d, bearer_token: e.target.value }))}
                disabled={saving}
                autoComplete="off"
              />
              {isGoBuyMe && (
                <a href={GOBUYME_API_KEYS_URL} target="_blank" rel="noreferrer" className="text-xs text-ink-muted underline">
                  Open GoBuyMe API keys and docs
                </a>
              )}
            </label>
          )}
          {draft.api_framework === 'parsefile' && (
            <>
              <label className="block">
                <span className="text-xs text-ink-muted">API user id</span>
                <input
                  type="number"
                  className="mt-0.5 w-full rounded border border-brand-200 px-2 py-1 text-sm"
                  placeholder="8546"
                  value={draft.api_user_id}
                  onChange={(e) => setDraft((d) => ({ ...d, api_user_id: e.target.value }))}
                  disabled={saving}
                />
              </label>
              <label className="block">
                <span className="text-xs text-ink-muted">API email</span>
                <input
                  type="email"
                  className="mt-0.5 w-full rounded border border-brand-200 px-2 py-1 text-sm"
                  placeholder="you@example.com"
                  value={draft.api_email}
                  onChange={(e) => setDraft((d) => ({ ...d, api_email: e.target.value }))}
                  disabled={saving}
                />
              </label>
              <label className="block">
                <span className="text-xs text-ink-muted">
                  Portal password (for submission IDs)
                </span>
                <input
                  type="password"
                  className="mt-0.5 w-full rounded border border-brand-200 px-2 py-1 text-sm"
                  placeholder={`${draft.name.trim() || 'Buying group'} portal password`}
                  value={draft.api_password}
                  onChange={(e) => setDraft((d) => ({ ...d, api_password: e.target.value }))}
                  disabled={saving}
                  autoComplete="off"
                />
              </label>
            </>
          )}
          {usesUsernamePassword(draft.api_framework) && (
            <>
              <label className="block">
                <span className="text-xs text-ink-muted">
                  {draft.api_framework === 'bfmr'
                    ? 'API Key'
                    : usesEmailLogin
                      ? 'Email'
                      : 'Username'}
                </span>
                <input
                  type={usesEmailLogin ? 'email' : 'text'}
                  className="mt-0.5 w-full rounded border border-brand-200 px-2 py-1 text-sm"
                  placeholder={
                    draft.api_framework === 'buyinggroup'
                      ? 'BuyingGroup.com username'
                      : usesEmailLogin
                        ? 'you@example.com'
                        : draft.api_framework === 'bfmr'
                          ? 'BFMR API-KEY'
                          : 'USABG username'
                  }
                  value={draft.api_username}
                  onChange={(e) => setDraft((d) => ({ ...d, api_username: e.target.value }))}
                  disabled={saving}
                  autoComplete="off"
                />
              </label>
              <label className="block">
                <span className="text-xs text-ink-muted">
                  {draft.api_framework === 'bfmr' ? 'API Secret' : 'Password'}
                </span>
                <input
                  type="password"
                  className="mt-0.5 w-full rounded border border-brand-200 px-2 py-1 text-sm"
                  placeholder={
                    draft.api_framework === 'buyinggroup'
                      ? 'BuyingGroup.com password'
                      : draft.api_framework === 'dealandrunner'
                        ? 'Deal & Runner password'
                        : draft.api_framework === 'bfmr'
                          ? 'BFMR API-SECRET'
                          : `${frameworkLabel(draft.api_framework)} password`
                  }
                  value={draft.api_password}
                  onChange={(e) => setDraft((d) => ({ ...d, api_password: e.target.value }))}
                  disabled={saving}
                  autoComplete="off"
                />
              </label>
            </>
          )}
          {supportsTrackingSubmit(draft.api_framework) && (
            <div className="rounded-lg border border-brand-100 dark:border-gray-600 px-3 py-2 space-y-2">
              <label className="inline-flex items-center gap-2 text-sm text-ink">
                <input
                  type="checkbox"
                  checked={groupSubmissionsEnabled && draft.tracking_submit_enabled}
                  title={!groupSubmissionsEnabled ? 'Group submissions are disabled in this environment' : undefined}
                  onChange={(e) =>
                    setDraft((d) => ({
                      ...d,
                      tracking_submit_enabled: e.target.checked,
                    }))
                  }
                  disabled={saving || !groupSubmissionsEnabled}
                  className="rounded border-brand-300"
                />
                <span>Auto-submit tracking</span>
              </label>
              <label className="block">
                <span className="text-xs text-ink-muted">
                  Run every (minutes)
                </span>
                <input
                  type="number"
                  min={1}
                  max={525600}
                  step={1}
                  className="mt-0.5 w-full rounded border border-brand-200 px-2 py-1 text-sm font-mono"
                  value={draft.schedule_interval_minutes}
                  onChange={(e) =>
                    setDraft((d) => ({ ...d, schedule_interval_minutes: Number(e.target.value) }))
                  }
                  disabled={saving}
                />
              </label>
              <p className="text-[11px] text-ink-muted">
                Last submit: {formatLastRun(g.tracking_submit_last_run_at)}
                {' '}· Next: {draft.tracking_submit_enabled ? formatLastRun(g.schedule_next_run_at) : 'off'}
              </p>
            </div>
          )}
          {draft.api_framework === 'bfmr' && (
            <div className="rounded-lg border border-brand-100 dark:border-gray-600 px-3 py-2 space-y-2">
              <label className="inline-flex items-center gap-2 text-sm text-ink">
                <input type="checkbox" checked={draft.order_number_submit_enabled}
                  onChange={(e) => setDraft((d) => ({ ...d, order_number_submit_enabled: e.target.checked }))}
                  disabled={saving || !groupSubmissionsEnabled} className="rounded border-brand-300" />
                <span>Auto-submit order numbers after import</span>
              </label>
              <p className="text-xs text-ink-muted">Registers unique product matches after saving. Uncertain matches need review. Existing orders require an explicit scan.</p>
            </div>
          )}
          {supportsCheckReceiving(draft.api_framework) && (
            <div className="rounded-lg border border-brand-100 dark:border-gray-600 px-3 py-2 space-y-2">
              <label className="inline-flex items-center gap-2 text-sm text-ink">
                <input type="checkbox" checked={draft.received_check_enabled}
                  onChange={(e) => setDraft((d) => ({ ...d, received_check_enabled: e.target.checked }))}
                  disabled={saving} className="rounded border-brand-300" />
                <span>Auto-check receiving</span>
              </label>
              <p className="text-[11px] text-ink-muted">
                Last check: {formatLastRun(g.received_check_last_run_at)}
              </p>
            </div>
          )}
          {['parsefile', 'buyinggroup', 'usabg', 'dealandrunner'].includes(draft.api_framework) && <div className="space-y-2">
            <label className="inline-flex items-center gap-2 text-sm"><input type="checkbox" checked={draft.payment_sync_enabled} disabled={saving} onChange={e => setDraft(d => ({...d, payment_sync_enabled: e.target.checked}))} />Automatically sync payments</label>
            <p className="text-xs text-ink-muted">Last payment sync: {formatLastRun(g.payment_sync_last_run_at)}{g.payment_sync_error ? ` · ${g.payment_sync_error}` : ''}</p>
          </div>}
          {draft.api_framework !== 'gobuyme' && (
            <p className="text-xs text-ink-muted">Each run submits, checks receiving, then syncs payments, when enabled. Saving schedule changes rebuilds the staggered run times for all groups.</p>
          )}
        </div>
      </div>
    </section>
  )
}
