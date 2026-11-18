import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, ApiError } from '../api/client'

interface ImportJob {
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'
  message?: string | null
  error?: string | null
  token?: string | null
}

export default function AutomationOrderImport() {
  const { jobId } = useParams()
  const navigate = useNavigate()
  const [message, setMessage] = useState('Waiting for browser automation…')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let stopped = false
    let timer: number | undefined
    setMessage('Waiting for browser automation…')
    setError(null)

    async function poll() {
      try {
        const job = await api.get<ImportJob>(`/browser-profiles/jobs/${encodeURIComponent(jobId ?? '')}`)
        if (stopped) return
        if (job.status === 'succeeded' && job.token) {
          navigate(`/import-review/bulk?token=${encodeURIComponent(job.token)}`, { replace: true })
          return
        }
        if (job.status === 'failed' || job.status === 'cancelled' || job.status === 'succeeded') {
          setError(job.error || job.message || 'This import ended before its review became available.')
          return
        }
        setMessage(job.message || (job.status === 'queued' ? 'Waiting for a browser…' : 'Capturing the order…'))
      } catch (err) {
        if (stopped) return
        if (err instanceof ApiError && err.status === 404) {
          setError('This import job is no longer available. Start a new import from the retailer order page.')
          return
        }
        setMessage('Waiting to reconnect to Order Manager…')
      }
      timer = window.setTimeout(poll, 1500)
    }

    void poll()
    return () => {
      stopped = true
      window.clearTimeout(timer)
    }
  }, [jobId, navigate])

  return (
    <section className="mx-auto max-w-lg rounded-lg border border-brand-100 p-6 dark:border-gray-700">
      <h1 className="text-xl font-semibold">Import with browser automation</h1>
      {error ? (
        <>
          <p className="mt-3 text-red-600 dark:text-red-400" role="alert">{error}</p>
          <Link className="mt-4 inline-block text-brand-600 underline dark:text-brand-400" to="/settings/stores">
            Open browser profiles
          </Link>
        </>
      ) : (
        <p className="mt-3 text-ink-muted dark:text-gray-300" role="status">{message}</p>
      )}
    </section>
  )
}
