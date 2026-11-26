import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { useGroupSubmissionsEnabled } from '../utils/capabilities'
import { ConfirmDialog } from '../components/ConfirmDialog'
import BuyingGroupBrowserConnection from '../components/BuyingGroupBrowserConnection'
import type { BrowserJob, BuyingGroup } from '../api/types'
import type { PaymentCapability } from './PaymentReview'

import {
  BFMR_DEFAULT_BASE_URL, BUYINGGROUP_DEFAULT_ADD_TRACKINGS_PATH, BUYINGGROUP_DEFAULT_BASE_URL,
  DEALANDRUNNER_DEFAULT_BASE_URL, DEFAULT_INTERVAL_MINUTES, GOBUYME_SIGN_IN_URL,
  PARSEFILE_DEFAULT_API_URL, USABG_DEFAULT_BASE_URL, formatLastRun, frameworkLabel, supportsCheckReceiving,
  supportsTrackingSubmit, usesUsernamePassword,
} from '../utils/buyingGroupConfig'

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

function canSubmitTrackings(g: BuyingGroup): boolean {
  if (!supportsTrackingSubmit(g.api_framework)) return false
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

function supportsBuyingGroupBrowser(g: BuyingGroup): boolean {
  const base = (g.base_url?.trim() || BUYINGGROUP_DEFAULT_BASE_URL).replace(/\/+$/, '').replace(/\/v1$/, '')
  return g.api_framework === 'buyinggroup' && base === BUYINGGROUP_DEFAULT_BASE_URL
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

function sortGroupsByName(groups: BuyingGroup[]): BuyingGroup[] {
  return [...groups].sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }))
}

export default function BuyingGroups() {
  const groupSubmissionsEnabled = useGroupSubmissionsEnabled()
  const navigate = useNavigate()
  const [groups, setGroups] = useState<BuyingGroup[]>([])
  const [loading, setLoading] = useState(true)
  const [createName, setCreateName] = useState('')
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null)
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
                <div className="space-y-2">
                  <div className="flex items-center justify-between gap-3">
                    <div className="min-w-0">
                      <Link className="font-medium text-ink hover:underline" to={`/settings/buying-groups/${g.id}`}>{g.name}</Link>
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
                        onClick={() => navigate(`/settings/buying-groups/${g.id}`)}
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
                      {supportsBuyingGroupBrowser(g) && <BuyingGroupBrowserConnection groupId={g.id} />}
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
