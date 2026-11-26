import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { useGroupSubmissionsEnabled } from '../utils/capabilities'
import { BUYINGGROUP_DEFAULT_BASE_URL, supportsBuyingGroupBrowser } from '../utils/buyingGroupBrowser'
import { ConfirmDialog } from '../components/ConfirmDialog'
import BuyingGroupBrowserConnection from '../components/BuyingGroupBrowserConnection'
import { NoticeLabel } from '../components/NoticeLabel'
import { BROWSER_LOGIN_REQUIRED } from '../utils/browserLogin'
import type { BrowserJob, BuyingGroup, BuyingGroupApiFramework } from '../api/types'
import type { PaymentCapability } from './PaymentReview'

const API_FRAMEWORK_OPTIONS: { value: '' | BuyingGroupApiFramework; label: string }[] = [
  { value: '', label: 'None' },
  { value: 'parsefile', label: 'Parsefile' },
  { value: 'usabg', label: 'USABG' },
  { value: 'gobuyme', label: 'GoBuyMe' },
  { value: 'buyinggroup', label: 'BuyingGroup.com' },
  { value: 'dealandrunner', label: 'Deal & Runner' },
  { value: 'bfmr', label: 'BFMR' },
]

const PARSEFILE_DEFAULT_API_URL = '/p/it@api@order-management'
const USABG_DEFAULT_BASE_URL = 'https://api.usabuying.group/buyers'
const GOBUYME_SIGN_IN_URL = 'https://gobuyme.com/auth'
const GOBUYME_API_KEYS_URL = 'https://gobuyme.com/app/profile?step=api-keys'
const BUYINGGROUP_DEFAULT_ADD_TRACKINGS_PATH = '/v1/order/add_trackings'
const DEALANDRUNNER_DEFAULT_BASE_URL = 'https://api.dealandrunner.com'
const BFMR_DEFAULT_BASE_URL = 'https://api.bfmr.com'
const DEFAULT_INTERVAL_MINUTES = 360

function receivingJobActive(job: BrowserJob | undefined): boolean {
  return job?.status === 'queued' || job?.status === 'running'
}

function ReceivingJobStatus({ job }: { job: BrowserJob }) {
  const retryAt = job.progress?.retry_at
  const completed = job.progress?.completed_requests
  const active = receivingJobActive(job)
  return (
    <div className="space-y-1 text-sm">
      <p className={job.error ? 'text-red-600 dark:text-red-400' : 'text-ink-muted'}>
        {job.error || job.message}
      </p>
      {active && typeof retryAt === 'string' &&
        <p className="text-ink-muted">Next attempt {new Date(retryAt).toLocaleTimeString()}</p>}
      {active && typeof completed === 'number' &&
        <p className="text-ink-muted">{completed} requests completed</p>}
      {job.review_url &&
        <Link className="text-brand-600 underline" to={job.review_url}>Open receiving review</Link>}
    </div>
  )
}

function usesUsernamePassword(framework: string | null | undefined): boolean {
  return (
    framework === 'usabg' ||
    framework === 'gobuyme' ||
    framework === 'buyinggroup' ||
    framework === 'dealandrunner' ||
    framework === 'bfmr'
  )
}

function supportsTrackingSubmit(framework: string | null | undefined): boolean {
  return (
    framework === 'parsefile' ||
    framework === 'usabg' ||
    framework === 'gobuyme' ||
    framework === 'buyinggroup' ||
    framework === 'dealandrunner' ||
    framework === 'bfmr'
  )
}

function supportsCheckReceiving(framework: string | null | undefined): boolean {
  return (
    framework === 'parsefile' ||
    framework === 'buyinggroup' ||
    framework === 'bfmr' ||
    framework === 'usabg' ||
    framework === 'dealandrunner'
  )
}

function formatLastRun(iso: string | null | undefined): string {
  if (!iso) return 'never'
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return 'never'
  }
}

function canSubmitTrackings(g: BuyingGroup): boolean {
  if (!supportsTrackingSubmit(g.api_framework)) return false
  if (supportsBuyingGroupBrowser(g)) return true
  if (g.api_framework === 'parsefile') {
    return (
      Boolean(g.bearer_token?.trim()) &&
      Boolean(g.base_url?.trim()) &&
      // Blank api_url uses the Parsefile framework default.
      g.api_user_id != null &&
      Boolean(g.api_email?.trim())
    )
  }
  if (usesUsernamePassword(g.api_framework)) {
    // Blank base_url uses the framework default.
    return Boolean(g.api_username?.trim()) && Boolean(g.api_password?.trim()) &&
      (g.api_framework !== 'gobuyme' || Boolean(g.bearer_token?.trim()))
  }
  return false
}

function canCheckReceiving(g: BuyingGroup): boolean {
  if (!supportsCheckReceiving(g.api_framework)) return false
  if (supportsBuyingGroupBrowser(g)) return true
  if (g.api_framework === 'bfmr') {
    return Boolean(g.api_username?.trim()) && Boolean(g.api_password?.trim())
  }
  return canSubmitTrackings(g)
}

function effectiveBaseUrl(g: BuyingGroup): string {
  const base = g.base_url?.trim()
  if (base) return base
  if (g.api_framework === 'usabg') return USABG_DEFAULT_BASE_URL
  if (g.api_framework === 'buyinggroup') return BUYINGGROUP_DEFAULT_BASE_URL
  if (g.api_framework === 'dealandrunner') return DEALANDRUNNER_DEFAULT_BASE_URL
  if (g.api_framework === 'bfmr') return BFMR_DEFAULT_BASE_URL
  return ''
}

function effectiveApiUrl(g: BuyingGroup): string {
  if (g.api_framework === 'usabg') return '/trackings'
  if (g.api_framework === 'dealandrunner') return '/tracking'
  if (g.api_framework === 'bfmr') return '/api/v2/my-tracker'
  if (g.api_framework === 'buyinggroup') {
    return g.api_url?.trim() || BUYINGGROUP_DEFAULT_ADD_TRACKINGS_PATH
  }
  return g.api_url?.trim() || (g.api_framework === 'parsefile' ? PARSEFILE_DEFAULT_API_URL : '')
}

function endpointSummary(g: BuyingGroup): string | null {
  if (g.api_framework === 'gobuyme') return GOBUYME_SIGN_IN_URL
  const base = effectiveBaseUrl(g)
  if (!base && g.api_framework !== 'buyinggroup') return null
  if (g.api_framework === 'buyinggroup') {
    const pathOrUrl = effectiveApiUrl(g)
    if (pathOrUrl.startsWith('http://') || pathOrUrl.startsWith('https://')) {
      return pathOrUrl
    }
    return `${base || BUYINGGROUP_DEFAULT_BASE_URL}${pathOrUrl}`
  }
  return `${base}${effectiveApiUrl(g)}`
}

function AliasEditor({
  group,
  onChange,
}: {
  group: BuyingGroup
  onChange: (updated: BuyingGroup) => void
}) {
  const [newAlias, setNewAlias] = useState('')
  const [saving, setSaving] = useState(false)

  const updateAliases = async (aliases: string[]) => {
    setSaving(true)
    try {
      const updated = await api.patch<BuyingGroup>(`/buying-groups/${group.id}`, { aliases })
      onChange(updated)
    } catch (e) {
      console.error(e)
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
    await updateAliases([...(group.aliases ?? []), alias])
    setNewAlias('')
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
      <form onSubmit={addAlias} className="mt-2 flex gap-2 max-w-md">
        <input
          type="text"
          className="rounded border border-brand-200 px-2 py-1 text-sm flex-1"
          placeholder="Add alias for import matching"
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

function emptyDraft(): EditDraft {
  return {
    name: '',
    api_framework: '',
    base_url: '',
    api_url: '',
    bearer_token: '',
    api_user_id: '',
    api_email: '',
    api_username: '',
    api_password: '',
    tracking_submit_enabled: false,
    order_number_submit_enabled: false,
    schedule_interval_minutes: DEFAULT_INTERVAL_MINUTES,
    received_check_enabled: false,
    payment_sync_enabled: true,
  }
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

function frameworkLabel(framework: string | null | undefined): string | null {
  if (!framework) return null
  const opt = API_FRAMEWORK_OPTIONS.find((o) => o.value === framework)
  return opt?.label ?? framework
}

function sortGroupsByName(groups: BuyingGroup[]): BuyingGroup[] {
  return [...groups].sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }))
}

export default function BuyingGroups({ groupsNeedingLogin, onBrowserStatusChange }: {
  groupsNeedingLogin: ReadonlySet<number>
  onBrowserStatusChange: () => void
}) {
  const groupSubmissionsEnabled = useGroupSubmissionsEnabled()
  const navigate = useNavigate()
  const [groups, setGroups] = useState<BuyingGroup[]>([])
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState<number | null>(null)
  const [draft, setDraft] = useState<EditDraft>(emptyDraft())
  const [createName, setCreateName] = useState('')
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null)
  const [saving, setSaving] = useState(false)
  const [submittingGroupId, setSubmittingGroupId] = useState<number | null>(null)
  const [checkingReceivingGroupId, setCheckingReceivingGroupId] = useState<number | null>(null)
  const [receivingJobs, setReceivingJobs] = useState<Record<number, BrowserJob>>({})
  const browserGroupIds = groups.filter(supportsBuyingGroupBrowser).map(g => g.id).join(',')
  const runningReceivingJobs = Object.entries(receivingJobs)
    .filter(([, job]) => receivingJobActive(job)).map(([id, job]) => `${id}:${job.id}`).join(',')

  useEffect(() => {
    if (!browserGroupIds) return
    let active = true
    for (const id of browserGroupIds.split(',')) {
      api.get<BrowserJob | null>(`/buying-groups/${id}/receiving-check-job`)
        .then(job => {
          if (active && job) setReceivingJobs(prev => prev[Number(id)] ? prev : { ...prev, [id]: job })
        })
        .catch(() => {})
    }
    return () => { active = false }
  }, [browserGroupIds])

  useEffect(() => {
    if (!runningReceivingJobs) return
    let active = true
    let timer: number
    async function poll() {
      await Promise.all(runningReceivingJobs.split(',').map(async key => {
        const [id, expectedJobId] = key.split(':')
        try {
          const job = await api.get<BrowserJob | null>(`/buying-groups/${id}/receiving-check-job`)
          if (active) setReceivingJobs(prev => {
            const current = prev[Number(id)]
            if (current?.id !== expectedJobId) return prev
            return { ...prev, [id]: job ?? { ...current, status: 'failed', error: 'The check was interrupted. Start Check receiving again.' } }
          })
        } catch {
          // Keep showing the last status if a local poll fails.
        }
      }))
      if (active) timer = window.setTimeout(poll, 2000)
    }
    timer = window.setTimeout(poll, 2000)
    return () => { active = false; window.clearTimeout(timer) }
  }, [runningReceivingJobs])
  const [submitAlert, setSubmitAlert] = useState<string | null>(null)
  const [paymentCapabilities, setPaymentCapabilities] = useState<PaymentCapability[]>([])
  const [checkingPayments, setCheckingPayments] = useState<number | null>(null)
  const isGoBuyMe = draft.api_framework === 'gobuyme'
  const usesEmailLogin = draft.api_framework === 'dealandrunner' || isGoBuyMe

  useEffect(() => {
    api.get<PaymentCapability[]>('/payment-review/capabilities').then(setPaymentCapabilities).catch(console.error)
  }, [groups])

  async function checkPayments(groupId: number) {
    setCheckingPayments(groupId)
    setSubmitAlert(null)
    try {
      await api.post(`/payment-review/groups/${groupId}/refresh`, {})
      navigate(`/payments?group=${groupId}`)
    } catch (e) {
      setSubmitAlert(e instanceof Error ? e.message : 'Failed to check payments')
    } finally { setCheckingPayments(null) }
  }

  useEffect(() => {
    api
      .get<BuyingGroup[]>('/buying-groups')
      .then((data) => setGroups(sortGroupsByName(data)))
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [])

  const startEdit = (g: BuyingGroup) => {
    setEditing(g.id)
    setDraft(draftFromGroup(g))
  }
  const saveEdit = async () => {
    if (editing == null) return
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
      await api.patch<BuyingGroup>(`/buying-groups/${editing}`, {
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
      setGroups(sortGroupsByName(await api.get<BuyingGroup[]>('/buying-groups')))
      setEditing(null)
    } catch (e) {
      console.error(e)
      setSubmitAlert(e instanceof Error ? e.message : 'Could not save buying group.')
    } finally {
      setSaving(false)
    }
  }
  const cancelEdit = () => setEditing(null)

  const runTrackingSubmit = async (groupId: number) => {
    setSubmittingGroupId(groupId)
    setSubmitAlert(null)
    try {
      const res = await api.post<{
        buying_group_id: number
        submitted_count: number
        message: string | null
        tracking_submit_last_run_at: string | null
      }>(`/buying-groups/${groupId}/submit-trackings`, {})
      setGroups((prev) =>
        prev.map((g) =>
          g.id === groupId
            ? { ...g, tracking_submit_last_run_at: res.tracking_submit_last_run_at }
            : g
        )
      )
      setSubmitAlert(res.message || `Submitted ${res.submitted_count} tracking number(s).`)
    } catch (e) {
      console.error(e)
      setSubmitAlert(e instanceof Error ? e.message : 'Failed to submit tracking numbers')
    } finally {
      setSubmittingGroupId(null)
    }
  }

  const runCheckReceiving = async (groupId: number) => {
    setCheckingReceivingGroupId(groupId)
    setSubmitAlert(null)
    try {
      const res = await api.post<{
        buying_group_id: number
        matched_count: number
        message: string | null
        review_path: string | null
        job_id?: string | null
      }>(`/buying-groups/${groupId}/check-receiving`, {})
      const jobId = res.job_id
      if (jobId) {
        setReceivingJobs(prev => ({ ...prev, [groupId]: {
          id: jobId, profile_id: 0, kind: 'buyinggroup_receiving', status: 'queued',
          message: 'Starting receiving check…',
        } }))
        return
      }
      if (res.review_path) {
        navigate(res.review_path)
        return
      }
      setSubmitAlert(res.message || 'No received items found to mark scanned.')
    } catch (e) {
      console.error(e)
      setSubmitAlert(e instanceof Error ? e.message : 'Failed to check receiving')
    } finally {
      setCheckingReceivingGroupId(null)
    }
  }

  const create = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!createName.trim()) return
    try {
      await api.post<BuyingGroup>('/buying-groups', { name: createName.trim() })
      setGroups(sortGroupsByName(await api.get<BuyingGroup[]>('/buying-groups')))
      setCreateName('')
    } catch (e) {
      console.error(e)
    }
  }

  const remove = async (id: number) => {
    try {
      await api.delete(`/buying-groups/${id}`)
      onBrowserStatusChange()
      setGroups(sortGroupsByName(await api.get<BuyingGroup[]>('/buying-groups')))
    } catch (e) {
      console.error(e)
    } finally {
      setConfirmDeleteId(null)
    }
  }

  if (loading) return <div className="text-ink-muted">Loading…</div>

  return (
    <div>
      <div className="flex flex-wrap items-start justify-between gap-4 mb-2">
        <h1 className="text-2xl font-semibold text-ink">Buying groups</h1>
        <Link
          to="/browser-import-log?category=groups"
          className="text-sm text-brand-600 dark:text-brand-400 hover:underline"
        >
          View log
        </Link>
      </div>
      <p className="text-sm text-ink-muted mb-8">
        Add aliases to match shipping names and address lines on store imports. Configure API
        credentials and one interval per group, with separate submit and receiving toggles. When both
        are enabled, submissions run first, then receiving checks. Groups are automatically staggered
        whenever schedules change. Manual submit and check buttons
        stay separate. For Parsefile, BuyingGroup.com, BFMR, and USABG groups, received checks land
        on the Receiving page for review and apply.
      </p>
      {!groupSubmissionsEnabled && (
        <p className="mb-4 text-sm text-ink-muted" role="status">
          Group submissions are disabled in this environment. Lookups and receiving checks remain available.
        </p>
      )}
      {submitAlert && (
        <p className="mb-4 text-sm text-ink dark:text-gray-200 rounded-lg border border-brand-200 dark:border-gray-600 bg-brand-50/50 dark:bg-gray-700/40 px-3 py-2">
          {submitAlert}
          <button
            type="button"
            onClick={() => setSubmitAlert(null)}
            className="ml-2 text-ink-muted hover:underline"
          >
            Dismiss
          </button>
        </p>
      )}
      <form onSubmit={create} className="flex gap-2 mb-6">
        <input
          type="text"
          className="rounded-lg border border-brand-200 px-3 py-2 flex-1 max-w-xs"
          placeholder="New group name"
          value={createName}
          onChange={(e) => setCreateName(e.target.value)}
        />
        <button type="submit" className="px-4 py-2 bg-brand-600 text-white rounded-lg font-medium hover:bg-brand-700">
          Add
        </button>
      </form>
      <div className="w-full md:w-fit md:min-w-[600px] md:max-w-full bg-white dark:bg-gray-800 rounded-xl border border-brand-200/80 dark:border-gray-700 shadow-sm overflow-hidden">
        <ul className="divide-y divide-brand-100 dark:divide-gray-700">
          {groups.length === 0 ? (
            <li className="py-8 text-center text-ink-muted">No buying groups yet.</li>
          ) : (
            groups.map((g) => (
              <li key={g.id} className="px-4 py-3">
                {editing === g.id ? (
                  <div className="space-y-3">
                    <div className="flex items-center justify-between gap-3">
                      <input
                        type="text"
                        className="rounded border border-brand-200 px-2 py-1 flex-1 max-w-sm"
                        value={draft.name}
                        onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
                        placeholder="Name"
                        autoFocus
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
                          onClick={cancelEdit}
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
                          {supportsBuyingGroupBrowser(draft) ? (
                            <p className="text-xs text-ink-muted">Use the browser sign-in to sync payments, submit tracking, and check receiving.</p>
                          ) : (
                            <label className="block">
                              <span className="text-xs text-ink-muted">
                                {draft.api_framework === 'bfmr' ? 'API Secret' : 'Password'}
                              </span>
                              <input
                                type="password"
                                className="mt-0.5 w-full rounded border border-brand-200 px-2 py-1 text-sm"
                                placeholder={
                                  draft.api_framework === 'bfmr'
                                    ? 'BFMR API-SECRET'
                                    : `${frameworkLabel(draft.api_framework)} password`
                                }
                                value={draft.api_password}
                                onChange={(e) => setDraft((d) => ({ ...d, api_password: e.target.value }))}
                                disabled={saving}
                                autoComplete="off"
                              />
                            </label>
                          )}
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
                ) : (
                  <div className="space-y-2">
                    <div className="flex items-center justify-between gap-3">
                      <div className="min-w-0">
                        <span className="font-medium text-ink">
                          <NoticeLabel notice={groupsNeedingLogin.has(g.id) ? BROWSER_LOGIN_REQUIRED : undefined}>{g.name}</NoticeLabel>
                        </span>
                        {(g.api_framework || g.base_url || g.bearer_token || g.api_username) && (
                          <p
                            className="text-xs text-ink-muted truncate mt-0.5"
                            title={endpointSummary(g) ?? undefined}
                          >
                            {[
                              frameworkLabel(g.api_framework),
                              endpointSummary(g),
                              g.api_framework === 'gobuyme' && g.bearer_token && 'API key set',
                              usesUsernamePassword(g.api_framework)
                                ? g.api_username
                                  ? 'credentials set'
                                  : null
                                : g.bearer_token
                                  ? 'token set'
                                  : null,
                            ]
                              .filter(Boolean)
                              .join(' · ')}
                          </p>
                        )}
                      </div>
                      <div className="flex gap-2 shrink-0">
                        <button
                          type="button"
                          onClick={() => startEdit(g)}
                          className="p-1.5 rounded text-ink-muted hover:text-brand-600 hover:bg-brand-50 dark:hover:bg-brand-900/20 transition"
                          title="Edit"
                          aria-label="Edit"
                        >
                          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                          </svg>
                        </button>
                        <button
                          type="button"
                          onClick={() => setConfirmDeleteId(g.id)}
                          className="p-1.5 rounded text-ink-muted hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 transition"
                          title="Delete"
                          aria-label="Delete"
                        >
                          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                          </svg>
                        </button>
                      </div>
                    </div>
                    {paymentCapabilities.find(c => c.buying_group_id === g.id)?.supported && (
                      <div className="flex flex-wrap items-center gap-2 text-xs text-ink-muted">
                        <button type="button" onClick={() => checkPayments(g.id)}
                          title={paymentCapabilities.find(c => c.buying_group_id === g.id)?.reason ?? undefined}
                          disabled={checkingPayments !== null || !paymentCapabilities.find(c => c.buying_group_id === g.id)?.ready}
                          className="rounded-md border border-brand-200 dark:border-gray-600 px-2 py-1 text-xs text-ink disabled:opacity-50">
                          {checkingPayments === g.id ? 'Syncing payments…' : 'Sync payments'}
                        </button>
                        <Link className="underline" to={`/payments?group=${g.id}`}>Payments</Link>
                        <span>Auto-sync: {g.payment_sync_enabled ? 'on' : 'off'} · Last run: {formatLastRun(g.payment_sync_last_run_at)}{g.payment_sync_error ? ` · ${g.payment_sync_error}` : ''}</span>
                      </div>
                    )}
                    {g.api_framework === 'bfmr' && (
                      <div className="flex items-center gap-3 text-xs text-ink-muted">
                        <span>Order numbers: {!groupSubmissionsEnabled ? 'disabled in this environment' : g.order_number_submit_enabled ? 'automatic after import' : 'off'}</span>
                        <Link className="underline" to={`/bfmr-order-review?group=${g.id}`}>Order number review</Link>
                      </div>
                    )}
                    {supportsTrackingSubmit(g.api_framework) && (
                      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-muted">
                        <span>
                          Auto-submit:{' '}
                          {!groupSubmissionsEnabled ? 'disabled in this environment' : g.tracking_submit_enabled
                            ? `every ${g.schedule_interval_minutes ?? DEFAULT_INTERVAL_MINUTES} minutes`
                            : 'off'}
                        </span>
                        <span>Last submit: {formatLastRun(g.tracking_submit_last_run_at)}</span>
                        {(g.tracking_submit_enabled || g.received_check_enabled || g.payment_sync_enabled) && <span>Every {g.schedule_interval_minutes ?? DEFAULT_INTERVAL_MINUTES} minutes · Next run: {formatLastRun(g.schedule_next_run_at)}</span>}
                        {supportsCheckReceiving(g.api_framework) && (
                          <span>
                            Receiving: {g.received_check_enabled ? 'on' : 'off'} · Last check:{' '}
                            {formatLastRun(g.received_check_last_run_at)}
                          </span>
                        )}
                        <button
                          type="button"
                          onClick={() => runTrackingSubmit(g.id)}
                          title={!groupSubmissionsEnabled ? 'Group submissions are disabled in this environment' : undefined}
                          disabled={!groupSubmissionsEnabled || !canSubmitTrackings(g) || submittingGroupId === g.id}
                          className="rounded-md border border-brand-200 dark:border-gray-600 px-2 py-1 text-xs text-ink hover:bg-brand-50 dark:hover:bg-gray-700 disabled:opacity-50"
                        >
                          {submittingGroupId === g.id ? 'Submitting…' : 'Submit trackings now'}
                        </button>
                        {supportsCheckReceiving(g.api_framework) && (
                          <button
                            type="button"
                            onClick={() => runCheckReceiving(g.id)}
                            disabled={!canCheckReceiving(g) || checkingReceivingGroupId === g.id || receivingJobActive(receivingJobs[g.id])}
                            className="rounded-md border border-brand-200 dark:border-gray-600 px-2 py-1 text-xs text-ink hover:bg-brand-50 dark:hover:bg-gray-700 disabled:opacity-50"
                          >
                            {checkingReceivingGroupId === g.id || receivingJobActive(receivingJobs[g.id]) ? 'Checking…' : 'Check receiving'}
                          </button>
                        )}
                        {supportsBuyingGroupBrowser(g) && (
                          <BuyingGroupBrowserConnection groupId={g.id} needsLogin={groupsNeedingLogin.has(g.id)} onStatusChange={onBrowserStatusChange} />
                        )}
                      </div>
                    )}
                    {receivingJobs[g.id] && <ReceivingJobStatus job={receivingJobs[g.id]} />}
                    {supportsCheckReceiving(g.api_framework) && !supportsTrackingSubmit(g.api_framework) && (
                      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-muted">
                        <span>Last receiving check: {formatLastRun(g.received_check_last_run_at)}</span>
                        <button
                          type="button"
                          onClick={() => runCheckReceiving(g.id)}
                          disabled={!canCheckReceiving(g) || checkingReceivingGroupId === g.id}
                          className="rounded-md border border-brand-200 dark:border-gray-600 px-2 py-1 text-xs text-ink hover:bg-brand-50 dark:hover:bg-gray-700 disabled:opacity-50"
                        >
                          {checkingReceivingGroupId === g.id ? 'Checking…' : 'Check receiving'}
                        </button>
                      </div>
                    )}
                  </div>
                )}
                <AliasEditor
                  group={g}
                  onChange={(updated) => setGroups((prev) => prev.map((p) => (p.id === updated.id ? updated : p)))}
                />
              </li>
            ))
          )}
        </ul>
      </div>
      <ConfirmDialog
        open={confirmDeleteId !== null}
        message="Delete this buying group?"
        confirmLabel="Delete"
        danger
        onConfirm={() => confirmDeleteId !== null && remove(confirmDeleteId)}
        onCancel={() => setConfirmDeleteId(null)}
      />
    </div>
  )
}
