<template>
  <p v-if="error" id="load-error" role="alert">{{ message || 'Could not load records.' }} <button type="button" @click="$emit('retry')">Reload</button></p>
</template>

<script setup lang="ts">
defineProps<{ error: Error | { message?: string } | string | null; message?: string }>()
defineEmits(['retry'])
</script>
