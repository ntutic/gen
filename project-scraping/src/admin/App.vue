<template>
  <header>
    <div><p class="eyebrow">Vclist</p><h1>Administration</h1></div>
  </header>
  <main>
    <p v-if="actionMessage" role="status">{{ actionMessage }}</p>
    <section class="panel toolbar">
      <label>Source
        <select v-model="sourceId">
          <option value="">All sources</option>
          <option v-for="[id, name] in sources" :key="id" :value="id">{{ name || id }}</option>
        </select>
      </label>
      <button type="button" @click="refreshAll">Refresh</button>
    </section>
    <div class="tabs" role="tablist" aria-label="Admin sections">
      <button
        v-for="tab in tabs"
        :id="`tab-${tab.id}`"
        :key="tab.id"
        type="button"
        role="tab"
        :aria-selected="String(activeTab === tab.id)"
        :aria-controls="`${tab.id}-panel`"
        :tabindex="activeTab === tab.id ? 0 : -1"
        @click="activeTab = tab.id"
        @keydown="onTabKeydown($event, tab)"
      >{{ tab.label }}</button>
    </div>
    <div>
      <SourcesTab v-if="activeTab === 'sources'" :source-id="sourceId" />
      <RunsTab v-else :source-id="sourceId" />
    </div>
  </main>
</template>

<script setup lang="ts">
import { computed, provide, ref } from 'vue'
import { useQueryClient } from '@tanstack/vue-query'
import { useScrapers } from './api/queries/useSpiders.js'
import SourcesTab from './tabs/SourcesTab.vue'
import RunsTab from './tabs/RunsTab.vue'

const queryClient = useQueryClient()
const sourceId = ref('')
const activeTab = ref('sources')
const actionMessage = ref('')
// Replaces the legacy #action-status line: tabs report mutation feedback here.
provide('notify', (message: string) => { actionMessage.value = message })

const tabs = [
  { id: 'sources', label: 'Sources' },
  { id: 'runs', label: 'Runs' },
]

const { data: scrapers } = useScrapers()
const sources = computed(() => {
  const map = new Map((scrapers.value || []).map((scraper) => [scraper.source_id, scraper.source_name]))
  return [...map]
})

function refreshAll() {
  // Manual refresh retained: refetch whatever is currently active.
  queryClient.refetchQueries({ type: 'active' })
}

function onTabKeydown(event: KeyboardEvent, tab: { id: string }) {
  const index = tabs.findIndex((entry) => entry.id === tab.id)
  let next
  if (event.key === 'ArrowRight') next = (index + 1) % tabs.length
  else if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length
  else if (event.key === 'Home') next = 0
  else if (event.key === 'End') next = tabs.length - 1
  else return
  event.preventDefault()
  activeTab.value = tabs[next].id
  document.getElementById(`tab-${tabs[next].id}`)?.focus()
}
</script>
