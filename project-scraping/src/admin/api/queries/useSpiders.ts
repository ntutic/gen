import { computed, unref, type MaybeRef } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { apiFetch } from '../client.js'
import { keys } from '../keys.js'
import type { ScrapeJob, Scraper } from '../models.js'

function jobsPath(statuses: string[], limit: number, sourceId: string, beforeId?: number) {
  const params = new URLSearchParams({ limit: String(limit) })
  if (sourceId) params.set('source_id', sourceId)
  statuses.forEach((value) => params.append('status', value))
  if (beforeId) params.set('before_id', String(beforeId))
  return `/api/scrape-jobs?${params}`
}

async function fetchAllActive(sourceId: string): Promise<ScrapeJob[]> {
  const jobs: ScrapeJob[] = []
  let page: ScrapeJob[]
  do {
    page = await apiFetch<ScrapeJob[]>(jobsPath(['pending', 'running'], 1000, sourceId, jobs.at(-1)?.id))
    jobs.push(...page)
  } while (page.length === 1000)
  return jobs
}

export function useScrapers() {
  // No interval: scrapers change only via deploys; refetch on focus and
  // after the run-spider mutation invalidates this key.
  return useQuery({ queryKey: keys.scrapers(), queryFn: () => apiFetch<Scraper[]>('/api/scrapers') })
}

export function useActiveJobs(sourceId: MaybeRef<string>) {
  const queryKey = computed(() => keys.activeJobs(unref(sourceId) || ''))
  // Function form: poll while anything is pending/running, stop when settled.
  return useQuery({
    queryKey,
    queryFn: (context) => fetchAllActive((context.queryKey[2] as string) || ''),
    refetchInterval: (query) => {
      const jobs = query.state.data
      return jobs && jobs.some((job) => job.status === 'pending' || job.status === 'running') ? 7000 : false
    },
  })
}

// One history page (limit 50, newest first). Pages accumulate in RunsTab via
// fetchQuery so "Load older" appends without refetching earlier pages.
export function fetchHistoryPage(sourceId: string, beforeId?: number): Promise<ScrapeJob[]> {
  return apiFetch<ScrapeJob[]>(jobsPath(['succeeded', 'failed'], 50, sourceId, beforeId))
}
