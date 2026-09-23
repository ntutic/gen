<template>
  <ErrorBanner :error="pageError" @retry="refetchPage" />
  <div class="table-scroll">
    <table>
      <thead><tr><th scope="col">Name</th><th scope="col">URL</th><th scope="col">Key</th></tr></thead>
      <tbody>
        <tr v-if="pagePending"><td colspan="3">Loading records…</td></tr>
        <tr v-else-if="!items.length"><td colspan="3">No records found.</td></tr>
        <tr
          v-for="record in items"
          :key="record.id"
          :class="{ selected: selectedId === record.id }"
          @click="$emit('select', selectedId === record.id ? null : record.id)"
        >
          <td>{{ record.name || '—' }}</td>
          <td>{{ record.url || '—' }}</td>
          <td>{{ record.source_key || '—' }}</td>
        </tr>
      </tbody>
    </table>
  </div>
  <div class="section-heading">
    <p>{{ pageStatus }}</p>
    <div class="actions">
      <button type="button" :disabled="offset === 0 || pagePending" @click="offset -= limit">Previous</button>
      <button type="button" :disabled="!page?.has_more || pagePending" @click="offset += limit">Next</button>
    </div>
  </div>
  <section v-if="selectedId != null" class="section-heading" aria-live="polite">
    <div v-if="detailPending">Loading record detail…</div>
    <div v-else-if="detailError">Could not load record detail.</div>
    <div v-else-if="detail">
      <h2>{{ detail.name || `Record #${detail.id}` }}</h2>
      <p v-if="detail.url"><a :href="detail.url" target="_blank" rel="noreferrer">{{ detail.url }}</a></p>
      <dl v-if="detail.features.length" class="record-features">
        <template v-for="feature in detail.features" :key="feature.id">
          <dt>{{ feature.name }}</dt>
          <dd>{{ feature.value }}<span v-if="feature.unit"> {{ feature.unit.name }}</span></dd>
        </template>
      </dl>
      <p v-else>No features recorded.</p>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRecordsPage, useRecordDetail } from '../api/queries/useRecords.js'
import ErrorBanner from '../components/ErrorBanner.vue'

const props = defineProps<{ sourceId: string; selectedId: number | null; limit?: number }>()
defineEmits(['select'])

const limit = computed(() => props.limit || 50)
const offset = ref(0)
watch(() => props.sourceId, () => { offset.value = 0 })

const {
  data: page, error: pageError, isPending: pagePending, refetch: refetchPage,
} = useRecordsPage(computed(() => props.sourceId), offset, limit.value)

const items = computed(() => page.value?.items || [])
const pageStatus = computed(() => {
  if (!page.value) return ''
  const from = page.value.total === 0 ? 0 : offset.value + 1
  return `${from}–${offset.value + items.value.length} of ${page.value.total.toLocaleString()} records`
})

const {
  data: detail, error: detailError, isPending: detailPending,
} = useRecordDetail(computed(() => props.selectedId))
</script>
