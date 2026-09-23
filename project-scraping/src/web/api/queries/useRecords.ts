import { computed, unref, type MaybeRef } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { apiFetch } from '../client.js'
import { keys } from '../keys.js'
import type { RecordDetail, RecordPage, Source } from '../models.js'

// Slow poll (60s) + manual Reload via refetch() + refetch on window focus.
export function useSources() {
  return useQuery({
    queryKey: keys.sources(),
    queryFn: () => apiFetch<Source[]>('/api/sources'),
    refetchInterval: 60_000,
  })
}

// One records page; the page key changes with the filter and offset, so the
// browser gets a fresh cached entry per page without manual accumulation.
export function useRecordsPage(
  sourceId: MaybeRef<string>,
  offset: MaybeRef<number>,
  limit: number = 50,
) {
  const queryKey = computed(() => keys.records(unref(sourceId) || '', unref(offset), limit))
  return useQuery({
    queryKey,
    queryFn: () => {
      const params = new URLSearchParams({ limit: String(limit), offset: String(unref(offset)) })
      if (unref(sourceId)) params.set('source_id', unref(sourceId))
      return apiFetch<RecordPage>(`/api/records?${params}`)
    },
  })
}

// On-demand: fetched only while a record is selected in the browser.
export function useRecordDetail(recordId: MaybeRef<number | null>) {
  const id = computed(() => unref(recordId))
  return useQuery({
    queryKey: computed(() => keys.recordDetail(id.value)),
    queryFn: () => apiFetch<RecordDetail>(`/api/records/${id.value}`),
    enabled: computed(() => id.value != null),
    staleTime: 30_000,
  })
}
