import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Proxying /api and /ws to the backend keeps development same-origin, which
// means the httpOnly session cookie is sent automatically and no CORS
// configuration is needed here or in FastAPI.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.VITE_API_TARGET ?? 'http://localhost:8000',
        changeOrigin: true,
      },
      '/ws': {
        target: process.env.VITE_WS_TARGET ?? 'ws://localhost:8000',
        ws: true,
      },
    },
  },
})
