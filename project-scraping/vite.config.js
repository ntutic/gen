import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = dirname(fileURLToPath(import.meta.url))
const app = process.env.APP || 'admin'
if (!['admin', 'web'].includes(app)) throw new Error(`Unknown APP=${app} (expected admin|web)`)

// One Vite project, two entry points (Vite backend-integration pattern).
// Each app builds its own index.html + assets/ directly into the directory
// FastAPI serves (scrapectl/admin.py mounts web/admin, scrapectl/api.py mounts
// web/web). Rebuild with `npm run build`.
// emptyOutDir stays false so a build never wipes the served styles.css living
// next to the built output.
export default defineConfig({
  // App-specific root so the entry index.html is emitted at the top of
  // outDir (web/<app>/index.html), exactly what FastAPI serves at `/`.
  root: resolve(root, `src/${app}`),
  plugins: [vue()],
  build: {
    outDir: resolve(root, `web/${app}`),
    emptyOutDir: false,
    rollupOptions: { input: resolve(root, `src/${app}/index.html`) },
  },
})
