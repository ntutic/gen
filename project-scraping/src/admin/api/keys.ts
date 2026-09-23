// Query-key factory for the admin app's frozen endpoints. One entry per
// endpoint; the list/detail keys keep pollers and manual refetches on the
// same cache entries so invalidation hits every view.
export const keys = {
  scrapers: () => ['scrapers'],
  activeJobs: (sourceId: string) => ['scrape-jobs', 'active', sourceId || ''],
  jobHistoryPage: (sourceId: string, beforeId: number | null) =>
    ['scrape-jobs', 'history', sourceId || '', beforeId ?? null],
}
