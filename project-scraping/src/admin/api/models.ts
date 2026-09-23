// Response shapes for the admin app's endpoints. One interface per payload;
// queries and mutations import these instead of redeclaring fields.
export interface Scraper {
  id: string
  source_id: string
  source_name: string
  enabled: boolean
  record_count: number
}

export type JobStatus = 'pending' | 'running' | 'succeeded' | 'failed'

export interface ScrapeJob {
  id: number
  scraper_id: string
  source_id: string
  source_name: string
  status: JobStatus
  publish: boolean
  kind: string
  source_job_id: number | null
  observed_at: string | null
  heartbeat_at: string | null
  processing_version: string | null
  report: Record<string, unknown>
  queued_at: string
  started_at: string | null
  finished_at: string | null
  attempts: number
  result_count: number
  staged_records: number
  capture_files: number
  latest_capture_at: string | null
  log: string | null
}

export interface QueuedJob {
  id: number
  scraper_id: string
  status: string
}
