import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // In development the panel runs separately: `uvicorn app.main:app --reload --port 8080`.
    proxy: { '/api': process.env.DGS_PANEL_URL ?? 'http://localhost:8080' },
  },
})
