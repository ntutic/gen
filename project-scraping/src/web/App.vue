<template>
  <header>
    <div>
      <p class="eyebrow">Scraped records</p>
      <h1>Vclist</h1>
    </div>
    <strong id="record-count">{{ headerCount }}</strong>
  </header>

  <main>
    <section class="panel table-panel" aria-labelledby="sources-heading">
      <div class="section-heading">
        <div>
          <h2 id="sources-heading">Records by source</h2>
          <p>Select a source row to filter the records. Data refreshes automatically every minute.</p>
        </div>
        <div class="actions">
          <button type="button" :disabled="!sourceId" @click="clearSelection">Clear selection</button>
          <button type="button" :disabled="reloading" @click="reload">{{ reloading ? 'Loading…' : 'Reload' }}</button>
        </div>
      </div>
      <ErrorBanner
        :error="loadError"
        :message="loadedOnce ? 'Could not refresh records. Showing the previous results; try Reload again.' : 'Could not load records. Select Reload to try again.'"
        @retry="reload"
      />
      <SourcesTable :sources="sources" :selected-id="sourceId" @select="selectSource" />
    </section>

    <section class="panel table-panel" aria-labelledby="records-heading">
      <div class="section-heading">
        <div>
          <h2 id="records-heading">{{ recordsHeading }}</h2>
          <p>Select a record row to inspect its features.</p>
        </div>
      </div>
      <RecordsBrowser :source-id="sourceId" :selected-id="selectedId" @select="selectedId = $event" />
    </section>
  </main>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useSources } from './api/queries/useRecords.js'
import SourcesTable from './views/SourcesTable.vue'
import RecordsBrowser from './views/RecordsBrowser.vue'
import ErrorBanner from './components/ErrorBanner.vue'

const sourceId = ref('')
const selectedId = ref<number | null>(null)
const {
  data: sourcesData, error: sourcesError, isPending: sourcesPending, refetch: refetchSources,
} = useSources()

const sources = computed(() => sourcesData.value || [])
const reloading = computed(() => sourcesPending.value)
const loadedOnce = ref(false)
watch(sourcesData, (value) => {
  if (value) loadedOnce.value = true
  // Drop the selection when its source no longer exists.
  if (sourceId.value && !value?.some((source) => source.id === sourceId.value)) {
    sourceId.value = ''
  }
})

const loadError = computed(() => sourcesError.value || null)

const total = computed(() => sources.value.reduce((sum, source) => sum + source.record_count, 0))

const headerCount = computed(() => {
  if (!loadedOnce.value && reloading.value) return 'Loading records…'
  if (!loadedOnce.value && loadError.value) return 'Records unavailable'
  return `${total.value.toLocaleString()} records · ${sources.value.length.toLocaleString()} sources`
})

const recordsHeading = computed(() => {
  if (!sourceId.value) return 'All records'
  const source = sources.value.find((entry) => entry.id === sourceId.value)
  return source ? `Records · ${source.name}` : 'Records'
})

function selectSource(id: string) {
  sourceId.value = id
  selectedId.value = null
}

function clearSelection() {
  selectSource('')
}

function reload() {
  refetchSources()
}
</script>
