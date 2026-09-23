<template>
  <section role="tabpanel" aria-labelledby="tab-runs" tabindex="0">
    <h2>Pending and running</h2>
    <p>Runs refresh automatically while any run is pending or running.</p>
    <ErrorBanner :error="activeError" />
    <section class="panel table-panel">
      <table>
        <thead><tr><th>Run</th><th>Source / spider</th><th>Status</th><th>Results</th><th>Progress</th><th>Times</th><th>Details</th></tr></thead>
        <tbody>
          <tr v-if="activePending"><td colspan="7">Loading runs…</td></tr>
          <template v-else-if="active?.length">
            <JobRow v-for="job in active" :key="job.id" :job="job" :expanded="expanded" @toggle="toggle" />
          </template>
          <tr v-else><td colspan="7">No pending or running spiders.</td></tr>
        </tbody>
      </table>
    </section>
    <h2 class="section-title">Previous runs</h2>
    <ErrorBanner :error="historyError" retry @retry="reloadHistory" />
    <section class="panel table-panel">
      <table>
        <thead><tr><th>Run</th><th>Source / spider</th><th>Status</th><th>Results</th><th>Progress</th><th>Times</th><th>Details</th></tr></thead>
        <tbody>
          <tr v-if="historyPending"><td colspan="7">Loading previous runs…</td></tr>
          <template v-else-if="history.length">
            <JobRow v-for="job in history" :key="job.id" :job="job" :expanded="expanded" @toggle="toggle" />
          </template>
          <tr v-else><td colspan="7">No previous runs.</td></tr>
        </tbody>
      </table>
    </section>
    <button v-if="hasOlder" type="button" :disabled="historyLoadingMore" @click="loadOlder">Load older runs</button>
    <p role="status">{{ statusLine }}</p>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, toRef, watch } from 'vue'
import { useQueryClient } from '@tanstack/vue-query'
import { useActiveJobs, fetchHistoryPage } from '../api/queries/useSpiders.js'
import { keys } from '../api/keys.js'
import type { ScrapeJob } from '../api/models.js'
import ErrorBanner from '../components/ErrorBanner.vue'
import JobRow from '../components/JobRow.vue'

const props = defineProps<{ sourceId?: string }>()
const queryClient = useQueryClient()

const { data: active, isPending: activePending, error: activeError } = useActiveJobs(toRef(props, 'sourceId', ''))

// History accumulates page by page (newest first, limit 50) so "Load older"
// appends without refetching earlier pages. Open <details> survive refetches
// because expansion lives in this Set, keyed by job id.
const history = ref<ScrapeJob[]>([])
const hasOlder = ref(false)
const historyPending = ref(true)
const historyLoadingMore = ref(false)
const historyError = ref<Error | null>(null)
const expanded = ref(new Set<number>())

function toggle(id: number, open: boolean) {
  const next = new Set(expanded.value)
  if (open) next.add(id)
  else next.delete(id)
  expanded.value = next
}

const statusLine = computed(() =>
  `${(active.value || []).length} pending / running · ${history.value.length} previous runs shown`,
)

async function fetchPage(beforeId?: number) {
  return queryClient.fetchQuery({
    queryKey: keys.jobHistoryPage(props.sourceId || '', beforeId ?? null),
    queryFn: () => fetchHistoryPage(props.sourceId || '', beforeId),
  })
}

async function reloadHistory() {
  historyPending.value = true
  historyError.value = null
  try {
    const page = await fetchPage(undefined)
    history.value = page
    hasOlder.value = page.length === 50
  } catch (error) {
    historyError.value = error as Error
  } finally {
    historyPending.value = false
  }
}

async function loadOlder() {
  historyLoadingMore.value = true
  historyError.value = null
  try {
    const page = await fetchPage(history.value.at(-1)?.id)
    history.value = [...history.value, ...page]
    hasOlder.value = page.length === 50
  } catch (error) {
    historyError.value = error as Error
  } finally {
    historyLoadingMore.value = false
  }
}

watch(() => props.sourceId, reloadHistory, { immediate: true })
</script>
