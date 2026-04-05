import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // Proxy API requests to the Nanobot gateway API server
      '/api': {
        target: 'http://localhost:3456',
        changeOrigin: true,
      },
      // Proxy WebSocket connections
      '/ws': {
        target: 'ws://localhost:3456',
        ws: true,
      },
    },
  },
})
