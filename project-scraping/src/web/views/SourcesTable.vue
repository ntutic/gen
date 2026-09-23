<template>
  <div class="table-scroll">
    <table>
      <thead><tr><th scope="col">Source</th><th scope="col">Records</th></tr></thead>
      <tbody>
        <tr v-if="!sources.length"><td colspan="2">No sources available yet.</td></tr>
        <tr
          v-for="source in sources"
          :key="source.id"
          :data-source-id="source.id"
          :class="{ selected: selectedId === source.id }"
          @click="$emit('select', selectedId === source.id ? '' : source.id)"
        >
          <td>
            <span class="source-choice">
              <span>{{ source.name }}<small>{{ source.id }}</small></span>
            </span>
          </td>
          <td class="numeric">{{ source.record_count.toLocaleString() }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup lang="ts">
import type { Source } from '../api/models.js'

defineProps<{ sources: Source[]; selectedId: string }>()
defineEmits(['select'])
</script>
