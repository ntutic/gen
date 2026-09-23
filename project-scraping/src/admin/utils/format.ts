import type { ScrapeJob } from '../api/models.js'

// Timestamp/progress formatting matching the previous admin UI behavior.
export function timestamp(value: string | null | undefined): string {
  if (!value) return '—'
  // The database stores UTC timestamps without a timezone suffix.
  return new Date(/(?:Z|[+-]\d\d:\d\d)$/.test(value) ? value : `${value}Z`).toLocaleString()
}

export function ago(value: string | null | undefined): string {
  if (!value) return '—'
  const at = /(?:Z|[+-]\d\d:\d\d)$/.test(value) ? value : `${value}Z`
  const seconds = Math.max(0, Math.round((Date.now() - Date.parse(at)) / 1000))
  if (seconds < 60) return `${seconds}s ago`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  return `${Math.floor(seconds / 3600)}h ago`
}

export function progress(job: ScrapeJob): string {
  if (job.status === 'pending' && !job.started_at) return 'Not started'
  const lines = [`Staged ${job.staged_records ?? '—'} · captures ${job.capture_files ?? '—'}`]
  const freshness = []
  if (job.latest_capture_at) freshness.push(`latest capture ${ago(job.latest_capture_at)}`)
  else if (job.status === 'running') freshness.push('no captures yet')
  if (job.status === 'running' && job.heartbeat_at) freshness.push(`heartbeat ${ago(job.heartbeat_at)}`)
  if (freshness.length) lines.push(freshness.join(' · '))
  return lines.join('\n')
}
