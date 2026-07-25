import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Separate port from the user app so both can run side by side. The admin panel
// is a distinct build, but access control is enforced server-side by
// require_admin on every /api/admin route - a separate app is organisation,
// not security.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
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
