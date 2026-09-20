import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// In development the screens run on 5173 and the API on 5000. Proxying
// /api means the frontend code can use a relative path in both
// development and production, where a single process serves both on one
// port. Same code either way, no CORS, no environment variable to
// remember.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: process.env.API_TARGET || 'http://localhost:5000',
        changeOrigin: true,
      },
    },
  },
})
