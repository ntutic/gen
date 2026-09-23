<template>
  <tr>
    <td>#{{ job.id }} · {{ job.kind }} · {{ job.publish ? 'Publish' : 'Preview' }}</td>
    <td>{{ job.source_name || job.source_id }} · {{ job.scraper_id }}</td>
    <td :class="`job-status ${job.status}`">{{ job.status }}</td>
    <td>{{ job.result_count ?? '—' }}</td>
    <td class="job-times">{{ progress(job) }}</td>
    <td class="job-times">Queued: {{ timestamp(job.queued_at) }}&#10;Started: {{ timestamp(job.started_at) }}&#10;Finished: {{ timestamp(job.finished_at) }}</td>
    <td>
      <details :open="expanded.has(job.id)" @toggle="$emit('toggle', job.id, ($event.target as HTMLDetailsElement).open)">
        <summary>View details</summary>
        <pre>{{ detailsText }}</pre>
      </details>
    </td>
  </tr>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { timestamp, progress } from '../utils/format.js'
import type { ScrapeJob } from '../api/models.js'

const props = defineProps<{ job: ScrapeJob; expanded: Set<number> }>()
defineEmits(['toggle'])

const detailsText = computed(() =>
  [
    `Attempts: ${props.job.attempts}`,
    props.job.source_job_id ? `Input job: #${props.job.source_job_id}` : '',
    props.job.processing_version ? `Processing: ${props.job.processing_version}` : '',
    props.job.observed_at ? `Observed: ${timestamp(props.job.observed_at)}` : '',
    props.job.heartbeat_at ? `Heartbeat: ${timestamp(props.job.heartbeat_at)}` : '',
    Object.keys(props.job.report || {}).length ? JSON.stringify(props.job.report, null, 2) : '',
    props.job.log,
  ].filter(Boolean).join('\n'),
)
</script>
