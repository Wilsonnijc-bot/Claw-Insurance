import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const gatewayOrigin = 'http://localhost:3456'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: gatewayOrigin,
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://localhost:3456',
        ws: true,
      },
    },
  },
})
