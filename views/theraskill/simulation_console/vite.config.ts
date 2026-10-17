import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const simulationApiProxy = {
  target: 'http://127.0.0.1:8876',
  changeOrigin: true,
  rewrite: (path: string) => path.replace(/^\/api\/plugins\/theraskill-simulation/, ''),
}

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': simulationApiProxy,
    },
  },
  preview: {
    proxy: {
      '/api': simulationApiProxy,
    },
  },
})
