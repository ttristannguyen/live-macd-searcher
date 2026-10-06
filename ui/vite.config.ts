import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In development, Vite proxies /api (including the SSE stream) to the app on 8001.
// In production FastAPI serves the built files itself, same origin — no CORS anywhere.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8001',
    },
  },
  build: {
    outDir: 'dist',
  },
})
