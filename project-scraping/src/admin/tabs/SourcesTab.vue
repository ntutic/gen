<template>
  <section role="tabpanel" aria-labelledby="tab-sources" tabindex="0">
    <h2>Sources</h2>
    <p>Record counts show published records. Run spider queues a live run for the scraping worker. Successful runs publish their results.</p>
    <ErrorBanner :error="scrapersError" />
    <ErrorBanner :error="activeError" message="Could not refresh active runs." />
    <section class="panel table-panel">
      <table>
        <thead><tr><th>Source</th><th>Records</th><th>Scraper</th><th>Status</th><th>Action</th></tr></thead>
        <tbody>
          <tr v-if="scrapersPending"><td colspan="5">Loading scrapers…</td></tr>
          <tr v-for="scraper in visibleScrapers" :key="scraper.id">
            <td>{{ scraper.source_name || scraper.source_id }}</td>
            <td>{{ scraper.record_count.toLocaleString() }}</td>
            <td>{{ scraper.id }}</td>
            <td>{{ scraper.enabled ? 'Enabled' : 'Disabled' }}</td>
            <td>
              <button
                type="button"
                :disabled="!scraper.enabled || Boolean(currentRun(scraper)) || runPending"
                :title="!scraper.enabled ? 'This spider is disabled.' : currentRun(scraper) ? 'A run is already pending or running.' : 'Queue a live run and publish successful results.'"
                :aria-label="`${currentRun(scraper) ? `#${currentRun(scraper)!.id} ${currentRun(scraper)!.status}` : 'Run spider'} · ${scraper.source_name}`"
                @click="run(scraper)"
              >{{ currentRun(scraper) ? `#${currentRun(scraper)!.id} ${currentRun(scraper)!.status}` : 'Run spider' }}</button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, inject } from 'vue'
import { useScrapers, useActiveJobs } from '../api/queries/useSpiders.js'
import { useRunSpider } from '../api/mutations/useAdminMutations.js'
import type { Scraper } from '../api/models.js'
import ErrorBanner from '../components/ErrorBanner.vue'

const props = defineProps<{ sourceId?: string }>()
const notify = inject<(message: string) => void>('notify')

const { data: scrapers, isPending: scrapersPending, error: scrapersError } = useScrapers()
const { data: active, error: activeError } = useActiveJobs(computed(() => props.sourceId || ''))
const runSpider = useRunSpider(notify)
const runPending = computed(() => runSpider.isPending.value)

const visibleScrapers = computed(() =>
  (scrapers.value || []).filter((scraper) => !props.sourceId || scraper.source_id === props.sourceId),
)

function currentRun(scraper: Scraper) {
  return (active.value || []).find((job) => job.scraper_id === scraper.id && job.kind === 'live' && job.publish)
}

function run(scraper: Scraper) {
  runSpider.mutate({ scraperId: scraper.id, sourceName: scraper.source_name })
  notify?.(`Queuing ${scraper.source_name}…`)
}
</script>
