import { createApp } from 'vue'
import { VueQueryPlugin, QueryClient } from '@tanstack/vue-query'
import App from './App.vue'

// Per-app QueryClient (duplicated in src/admin/main.ts on purpose:
// strict app isolation, no cross-imports). Bounded retry, short staleTime,
// refetch on window focus. Polling intervals are per-query; nothing polls
// globally.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 2,
      staleTime: 5000,
      refetchOnWindowFocus: true,
    },
  },
})

createApp(App).use(VueQueryPlugin, { queryClient }).mount('#app')
