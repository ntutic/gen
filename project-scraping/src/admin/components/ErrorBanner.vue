<template>
  <p v-if="error" role="alert">{{ message || `Could not load: ${errorMessage}` }} <button v-if="retry" type="button" @click="$emit('retry')">Try again</button></p>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ error: Error | { message?: string } | string | null; message?: string; retry?: boolean }>()
defineEmits(['retry'])

const errorMessage = computed(() =>
  typeof props.error === 'string' ? props.error : (props.error?.message ?? String(props.error)),
)
</script>
