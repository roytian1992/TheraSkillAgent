import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const supportApiProxy = {
  target: 'http://127.0.0.1:8878',
  changeOrigin: true,
  rewrite: (path: string) => path.replace(/^\/api\/plugins\/theraskill-support/, ''),
}

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    proxy: { '/api': supportApiProxy },
  },
  preview: {
    port: 5174,
    proxy: { '/api': supportApiProxy },
  },
})
