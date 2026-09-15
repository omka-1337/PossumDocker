import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // In development the panel runs separately: `uvicorn app.main:app --reload --port 8080`.
    proxy: {
      // ws: the server console is a WebSocket under /api.
      '/api': { target: process.env.POSSUM_PANEL_URL ?? 'http://localhost:8080', ws: true },
    },
  },
})
