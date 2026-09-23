import { useMutation, useQueryClient } from '@tanstack/vue-query'
import { apiPost } from '../client.js'
import type { QueuedJob } from '../models.js'

// Queue a live spider run. Invalidation (not a full-page load) refreshes the
// affected views: active jobs restart polling, history and scrapers refetch.
export function useRunSpider(notify?: (message: string) => void) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ scraperId }: { scraperId: string; sourceName?: string }) =>
      apiPost<QueuedJob>('/api/scrape-jobs', { scraper_id: scraperId }),
    onSuccess: (job, variables) => {
      queryClient.invalidateQueries({ queryKey: ['scrape-jobs'] })
      queryClient.invalidateQueries({ queryKey: ['scrapers'] })
      const name = variables?.sourceName || 'Spider'
      notify?.(
        `${name}: run #${job.id} is ${job.status}.` +
          (job.status === 'pending' ? ' Waiting for the scraping worker.' : ''),
      )
    },
    onError: (error, variables) => {
      notify?.(`Could not queue ${variables?.sourceName || 'spider'}: ${error.message}`)
    },
  })
}
